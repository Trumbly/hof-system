"""Schreibweg der Zähler (CLAUDE.md, Regel 4.1): Zähler anlegen und wechseln (H7.1),
Stände erfassen (H7.2, O2), Stichtag der Ablesung.

Views, Läufe, Importe, der Testhof und die Übergabe der Stammdaten schreiben Zähler und
Stände nur über diese Funktionen. Fehlermeldungen nennen Zählernummern, NW-IDs und Daten,
keine Namen.

Regeln:
- Ein Stand ist nie kleiner als der Stand davor und nie größer als der Stand danach,
  jeweils am selben Zähler. Nach einem Zählerwechsel beginnt der Nachfolger mit seinem
  Einbaustand; der kleinere Stand gehört an ihn, nicht an den alten Zähler.
- Je Zähler und Tag gibt es einen Stand. Ein zweiter Stand am selben Tag korrigiert den
  ersten (die Änderung steht im Protokoll), außer es ist der Einbau- oder Ausbaustand.
- Geschätzte Stände tragen die Art „geschätzt“ als Kennzeichen.
- Die Eichfrist steht als Frist (Art Eichfrist, Quelle Zähler) im Fristenregister.
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.files import File
from django.db import models, transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from apps.fristen import services as fristen
from apps.fristen.models import Frist
from apps.kern import services as kern
from apps.kern.models import Arbeitsposten, Lauf, Objekt
from apps.stammdaten.models import Einheit, Uebergabe
from apps.zaehler import konfiguration
from apps.zaehler.models import Zaehler, Zaehlerstand, stand_text

logger = logging.getLogger(__name__)

#: Art des Arbeitspostens, den der Lauf ``zaehler_stichtag`` je fehlender Ablesung anlegt.
ART_ABLESUNG_FEHLT = "Ablesung fehlt"
_POSTEN_OFFEN = (Arbeitsposten.Status.OFFEN, Arbeitsposten.Status.ZURUECKGESTELLT)
#: Arten, die ``stand_erfassen`` annimmt. Einbau und Ausbau entstehen nur über
#: ``zaehler_anlegen``, ``zaehler_ausbauen`` und ``zaehler_wechseln``.
ERFASSBARE_ARTEN = (Zaehlerstand.Art.ABGELESEN, Zaehlerstand.Art.GESCHAETZT)
_FEST = (Zaehlerstand.Art.EINBAU, Zaehlerstand.Art.AUSBAU)


class ZaehlerFehler(Exception):
    """Eine Änderung an Zählern oder Ständen ist so nicht möglich. Nennt nur Nummern."""


class StandUnplausibel(ZaehlerFehler):
    """Der Stand passt nicht zu den Ständen davor oder danach (z. B. kleiner als der letzte)."""


def _heute() -> datetime.date:
    return timezone.localdate()


def _speichern(objekt: models.Model) -> None:
    try:
        objekt.full_clean()
    except ValidationError as fehler:
        felder = ", ".join(sorted(fehler.message_dict)) if hasattr(fehler, "error_dict") else ""
        raise ZaehlerFehler(
            f"{type(objekt)._meta.verbose_name} ungültig ({felder or fehler.messages[0]})"
        ) from fehler
    objekt.save()


def stand_lesen(wert: Any) -> Decimal:
    """Liest einen Stand aus Text („1.234,5“, „1234.5“) oder Zahl. Nie negativ."""
    if isinstance(wert, Decimal):
        stand = wert
    else:
        text = str(wert).strip().replace(" ", "")
        if "," in text:
            text = text.replace(".", "").replace(",", ".")
        try:
            stand = Decimal(text)
        except InvalidOperation as fehler:
            raise ZaehlerFehler("Der Stand ist keine Zahl.") from fehler
    if not stand.is_finite() or stand < 0:
        raise ZaehlerFehler("Der Stand muss eine Zahl ≥ 0 sein.")
    if stand != stand.quantize(Decimal("0.001")):
        raise ZaehlerFehler("Der Stand hat höchstens drei Nachkommastellen.")
    return stand.quantize(Decimal("0.001"))


# --- Lesen ---------------------------------------------------------------------------


def eingebaute_zaehler() -> QuerySet[Zaehler]:
    """Alle Zähler, die gerade eingebaut sind."""
    return Zaehler.objects.filter(ausgebaut_am__isnull=True)


def zaehler_des_objekts(objekt: Objekt, *, nur_eingebaut: bool = True) -> list[Zaehler]:
    """Zähler eines Objekts (Hauptzähler und Zähler seiner Einheiten), für die Ablesung.

    Reihenfolge: erst die Hauptzähler, dann nach NW-ID, Art und Nummer.
    """
    zaehler = Zaehler.objects.filter(Q(objekt=objekt) | Q(einheit__objekt=objekt))
    if nur_eingebaut:
        zaehler = zaehler.filter(ausgebaut_am__isnull=True)
    liste = list(zaehler.select_related("einheit", "objekt", "einheit__objekt"))
    return sorted(
        liste,
        key=lambda z: (z.einheit_id is not None, z.einheit.nw_id if z.einheit else "", z.nummer),
    )


def zaehler_der_einheiten(
    einheiten: Iterable[Einheit], am: datetime.date | None = None
) -> list[Zaehler]:
    """Zähler der Einheiten, die am Tag ``am`` eingebaut sind (Standard: heute eingebaut)."""
    zaehler = Zaehler.objects.filter(einheit__in=list(einheiten)).select_related("einheit")
    if am is None:
        return list(zaehler.filter(ausgebaut_am__isnull=True).order_by("einheit__nw_id", "nummer"))
    return [z for z in zaehler.order_by("einheit__nw_id", "nummer") if z.eingebaut_am(am)]


# --- Stichtag ------------------------------------------------------------------------


def stichtag_im_jahr(jahr: int) -> datetime.date:
    """Der Stichtag der Ablesung in einem Jahr (Standard 31.12., siehe konfiguration.toml)."""
    return konfiguration.laden().stichtag.im_jahr(jahr)


def ablesefenster(stichtag: datetime.date) -> tuple[datetime.date, datetime.date]:
    """Von wann bis wann ein Stand als Stand zum Stichtag gilt."""
    konf = konfiguration.laden()
    von = konf.erinnern_ab.im_jahr(stichtag.year)
    if von > stichtag:
        von = konf.erinnern_ab.im_jahr(stichtag.year - 1)
    return von, stichtag + datetime.timedelta(days=konf.nachlauf_tage)


def aktueller_stichtag(heute: datetime.date) -> datetime.date | None:
    """Der Stichtag, in dessen Ablesefenster ``heute`` liegt, sonst None."""
    for jahr in (heute.year - 1, heute.year, heute.year + 1):
        stichtag = stichtag_im_jahr(jahr)
        von, bis = ablesefenster(stichtag)
        if von <= heute <= bis:
            return stichtag
    return None


def letzter_stichtag(heute: datetime.date) -> datetime.date:
    """Der letzte Stichtag bis einschließlich ``heute``."""
    stichtag = stichtag_im_jahr(heute.year)
    return stichtag if stichtag <= heute else stichtag_im_jahr(heute.year - 1)


def braucht_stand(zaehler: Zaehler, stichtag: datetime.date) -> bool:
    """Gehört zum Stichtag ein Stand dieses Zählers? Ja, wenn er im Ablesefenster hing."""
    von, _ = ablesefenster(stichtag)
    if zaehler.einbaudatum is not None and zaehler.einbaudatum > stichtag:
        return False
    return zaehler.ausgebaut_am is None or zaehler.ausgebaut_am >= von


def stand_zum_stichtag(zaehler: Zaehler, stichtag: datetime.date) -> Zaehlerstand | None:
    """Der Stand im Ablesefenster des Stichtags, der dem Stichtag am nächsten liegt."""
    von, bis = ablesefenster(stichtag)
    staende = list(zaehler.staende.filter(datum__gte=von, datum__lte=bis))
    if not staende:
        return None
    return min(staende, key=lambda s: (abs((s.datum - stichtag).days), s.datum))


# --- Arbeitsposten „Ablesung fehlt“ --------------------------------------------------


def _offene_ablesung_posten(zaehler: Zaehler) -> QuerySet[Arbeitsposten]:
    return Arbeitsposten.objects.filter(
        art=ART_ABLESUNG_FEHLT,
        bezug_typ=ContentType.objects.get_for_model(Zaehler),
        bezug_id=zaehler.pk,
        status__in=_POSTEN_OFFEN,
    )


def _ablesung_posten_schliessen(zaehler: Zaehler, *, nur_fuer: datetime.date | None = None) -> int:
    """Schließt offene Posten „Ablesung fehlt“ eines Zählers.

    ``nur_fuer``: nur Posten, in deren Ablesefenster dieses Datum liegt.
    """
    anzahl = 0
    for posten in _offene_ablesung_posten(zaehler).select_for_update():
        if nur_fuer is not None:
            if posten.faellig_am is None:
                continue
            von, bis = ablesefenster(posten.faellig_am)
            if not von <= nur_fuer <= bis:
                continue
        kern.arbeitsposten_erledigen(posten)
        anzahl += 1
    return anzahl


# --- Eichfrist -----------------------------------------------------------------------


def _eichfrist_anlegen(zaehler: Zaehler) -> Frist | None:
    if zaehler.eichfrist_bis is None or not zaehler.ist_eingebaut:
        return None
    return fristen.frist_anlegen(
        Frist.Art.EICHFRIST,
        zaehler.eichfrist_bis,
        Frist.Quelle.ZAEHLER,
        zaehler,
        hinweis=f"Eichfrist Zähler {zaehler.nummer} ({zaehler.ort})",
        vorlauf_tage=konfiguration.laden().eichfrist_vorlauf_tage,
        datum_angleichen=True,
    )


def _eichfrist_erledigen(zaehler: Zaehler, am: datetime.date) -> None:
    for frist in fristen.fristen_zu(zaehler, nur_offen=True).filter(art=Frist.Art.EICHFRIST):
        fristen.frist_erledigen(frist, min(am, _heute()))


# --- Zähler --------------------------------------------------------------------------


def _ort_pruefen(einheit: Einheit | None, objekt: Objekt | None) -> None:
    if (einheit is None) == (objekt is None):
        raise ZaehlerFehler("Ein Zähler hängt an genau einer Einheit oder einem Objekt.")
    if einheit is not None and einheit.ist_wohnraum:
        raise ZaehlerFehler(f"Einheit {einheit.nw_id} ist Wohnraum; ihre Zähler führt Verwaltio.")


def zaehler_anlegen(
    nummer: str,
    art: str,
    *,
    einheit: Einheit | None = None,
    objekt: Objekt | None = None,
    bezeichnung: str = "",
    einbaudatum: datetime.date | None = None,
    eichfrist_bis: datetime.date | None = None,
    einbau_stand: Decimal | str | None = None,
    einbau_foto: File[Any] | None = None,
) -> Zaehler:
    """Legt einen Zähler an einer Einheit oder (als Hauptzähler) an einem Objekt an (H7.1).

    Mit ``einbau_stand`` entsteht der Stand der Art „Einbau“ am Einbaudatum. Die Eichfrist
    kommt ins Fristenregister.
    """
    nummer = nummer.strip()
    if not nummer:
        raise ZaehlerFehler("Ein Zähler braucht eine Nummer.")
    if art not in Zaehler.Art.values:
        raise ZaehlerFehler(f"Unbekannte Art {art}")
    _ort_pruefen(einheit, objekt)
    if Zaehler.objects.filter(nummer=nummer).exists():
        raise ZaehlerFehler(f"Zähler {nummer} gibt es schon.")
    if einbau_stand is not None and einbaudatum is None:
        raise ZaehlerFehler(f"Zähler {nummer}: Der Einbaustand braucht ein Einbaudatum.")
    with transaction.atomic():
        zaehler = Zaehler(
            nummer=nummer,
            art=art,
            einheit=einheit,
            objekt=objekt,
            bezeichnung=bezeichnung,
            einbaudatum=einbaudatum,
            eichfrist_bis=eichfrist_bis,
        )
        _speichern(zaehler)
        if einbau_stand is not None and einbaudatum is not None:
            _stand_schreiben(
                zaehler,
                einbaudatum,
                stand_lesen(einbau_stand),
                Zaehlerstand.Art.EINBAU,
                einbau_foto,
            )
        _eichfrist_anlegen(zaehler)
    logger.info("Zähler #%s angelegt (%s)", zaehler.pk, zaehler.ort)
    return zaehler


#: Felder, die ``zaehler_aendern`` ändern darf.
AENDERBAR = ("nummer", "bezeichnung", "einbaudatum", "eichfrist_bis")


def zaehler_aendern(zaehler: Zaehler, **felder: Any) -> Zaehler:
    """Ändert Nummer, Lage, Einbaudatum oder Eichfrist. Die Frist wandert mit."""
    unbekannt = set(felder) - set(AENDERBAR)
    if unbekannt:
        raise ZaehlerFehler(f"Diese Felder lassen sich hier nicht ändern: {sorted(unbekannt)}")
    if "nummer" in felder:
        felder["nummer"] = str(felder["nummer"]).strip()
        if (
            not felder["nummer"]
            or Zaehler.objects.filter(nummer=felder["nummer"]).exclude(pk=zaehler.pk).exists()
        ):
            raise ZaehlerFehler(f"Zähler {felder['nummer'] or '?'}: Nummer fehlt oder doppelt.")
    einbau = felder.get("einbaudatum", zaehler.einbaudatum)
    if einbau is not None and zaehler.staende.filter(datum__lt=einbau).exists():
        raise ZaehlerFehler(f"Zähler {zaehler.nummer} hat Stände vor dem {einbau:%d.%m.%Y}.")
    with transaction.atomic():
        for name, wert in felder.items():
            setattr(zaehler, name, wert)
        _speichern(zaehler)
        _eichfrist_anlegen(zaehler)
    return zaehler


def zaehler_ausbauen(
    zaehler: Zaehler,
    datum: datetime.date,
    stand: Decimal | str,
    *,
    foto: File[Any] | None = None,
) -> Zaehlerstand:
    """Baut einen Zähler aus: Stand der Art „Ausbau“, Eichfrist erledigt, Posten geschlossen."""
    if not zaehler.ist_eingebaut:
        raise ZaehlerFehler(f"Zähler {zaehler.nummer} ist schon ausgebaut.")
    if zaehler.staende.filter(datum__gt=datum).exists():
        raise ZaehlerFehler(
            f"Zähler {zaehler.nummer} hat Stände nach dem {datum:%d.%m.%Y}; "
            "der Ausbau muss der letzte Stand sein."
        )
    with transaction.atomic():
        ausbau = _stand_pruefen_und_schreiben(
            zaehler, datum, stand_lesen(stand), Zaehlerstand.Art.AUSBAU, foto
        )
        zaehler.ausgebaut_am = datum
        _speichern(zaehler)
        _eichfrist_erledigen(zaehler, datum)
        _ablesung_posten_schliessen(zaehler)
    return ausbau


@dataclass(frozen=True)
class Wechsel:
    alter: Zaehler
    neuer: Zaehler
    ausbau: Zaehlerstand
    einbau: Zaehlerstand


def zaehler_wechseln(
    alter: Zaehler,
    datum: datetime.date,
    ausbau_stand: Decimal | str,
    *,
    neue_nummer: str,
    einbau_stand: Decimal | str,
    eichfrist_bis: datetime.date | None = None,
    ausbau_foto: File[Any] | None = None,
    einbau_foto: File[Any] | None = None,
) -> Wechsel:
    """Zählerwechsel (H7.1) in einem Vorgang: Ausbaustand des alten, Einbaustand des neuen
    Zählers und das Datum. Alles oder nichts (eine Transaktion).

    Der neue Zähler hängt am selben Ort, hat dieselbe Art und ist ``nachfolger`` des alten.
    Ab dann gilt die Prüfung „nicht kleiner als der letzte Stand“ nur für den neuen Zähler.
    """
    with transaction.atomic():
        ausbau = zaehler_ausbauen(alter, datum, ausbau_stand, foto=ausbau_foto)
        neuer = zaehler_anlegen(
            neue_nummer,
            alter.art,
            einheit=alter.einheit,
            objekt=alter.objekt,
            bezeichnung=alter.bezeichnung,
            einbaudatum=datum,
            eichfrist_bis=eichfrist_bis,
            einbau_stand=einbau_stand,
            einbau_foto=einbau_foto,
        )
        alter.nachfolger = neuer
        _speichern(alter)
        einbau = neuer.staende.get(art=Zaehlerstand.Art.EINBAU)
    logger.info("Zähler #%s gewechselt gegen #%s", alter.pk, neuer.pk)
    return Wechsel(alter=alter, neuer=neuer, ausbau=ausbau, einbau=einbau)


# --- Stände --------------------------------------------------------------------------


def _stand_schreiben(
    zaehler: Zaehler,
    datum: datetime.date,
    stand: Decimal,
    art: str,
    foto: File[Any] | None,
    *,
    vermerk: str = "",
    uebergabe: Uebergabe | None = None,
    vorhanden: Zaehlerstand | None = None,
) -> Zaehlerstand:
    eintrag = vorhanden or Zaehlerstand(zaehler=zaehler, datum=datum)
    eintrag.stand = stand
    eintrag.art = art
    if vermerk or vorhanden is None:
        eintrag.vermerk = vermerk
    if uebergabe is not None:
        eintrag.uebergabe = uebergabe
        eintrag.bei_uebergabe = True
    if foto is not None:
        eintrag.foto.save(foto.name or "foto.jpg", foto, save=False)
    _speichern(eintrag)
    return eintrag


def _pruefen(
    zaehler: Zaehler,
    datum: datetime.date,
    stand: Decimal,
    art: str,
    *,
    ausser: Zaehlerstand | None = None,
) -> Zaehlerstand | None:
    """Prüft einen Stand gegen Einbauzeit und Nachbarstände. Rückgabe: der Stand, der am
    selben Tag schon steht (wird korrigiert), sonst None. ``ausser``: nicht mitprüfen."""
    if zaehler.einbaudatum is not None and datum < zaehler.einbaudatum:
        raise ZaehlerFehler(
            f"Zähler {zaehler.nummer} ist erst seit dem {zaehler.einbaudatum:%d.%m.%Y} eingebaut."
        )
    if zaehler.ausgebaut_am is not None and datum > zaehler.ausgebaut_am:
        meldung = f"Zähler {zaehler.nummer} ist seit dem {zaehler.ausgebaut_am:%d.%m.%Y} ausgebaut."
        if zaehler.nachfolger is not None:
            meldung += f" Stände gehören an Zähler {zaehler.nachfolger.nummer}."
        raise ZaehlerFehler(meldung)
    staende = Zaehlerstand.objects.select_for_update().filter(zaehler=zaehler)
    if ausser is not None:
        staende = staende.exclude(pk=ausser.pk)
    vorhanden = staende.filter(datum=datum).first()
    if vorhanden is not None and vorhanden.art in _FEST and vorhanden.art != art:
        raise ZaehlerFehler(
            f"Zähler {zaehler.nummer}: Am {datum:%d.%m.%Y} steht schon der Stand "
            f"„{vorhanden.get_art_display()}“."
        )
    davor = staende.filter(datum__lt=datum).order_by("-datum", "-pk").first()
    danach = staende.filter(datum__gt=datum).order_by("datum", "pk").first()
    if davor is not None and stand < davor.stand:
        raise StandUnplausibel(
            f"Zähler {zaehler.nummer}: {stand_text(stand)} ist kleiner als der letzte Stand "
            f"{davor.stand_text} vom {davor.datum:%d.%m.%Y}. Wurde der Zähler gewechselt? "
            "Dann bitte den Zählerwechsel erfassen."
        )
    if danach is not None and stand > danach.stand:
        raise StandUnplausibel(
            f"Zähler {zaehler.nummer}: {stand_text(stand)} ist größer als der spätere Stand "
            f"{danach.stand_text} vom {danach.datum:%d.%m.%Y}."
        )
    if art == Zaehlerstand.Art.EINBAU and davor is not None:
        raise ZaehlerFehler(f"Zähler {zaehler.nummer}: Der Einbaustand muss der erste sein.")
    return vorhanden


def _stand_pruefen_und_schreiben(
    zaehler: Zaehler,
    datum: datetime.date,
    stand: Decimal,
    art: str,
    foto: File[Any] | None,
    *,
    vermerk: str = "",
    uebergabe: Uebergabe | None = None,
) -> Zaehlerstand:
    vorhanden = _pruefen(zaehler, datum, stand, art)
    return _stand_schreiben(
        zaehler, datum, stand, art, foto, vermerk=vermerk, uebergabe=uebergabe, vorhanden=vorhanden
    )


def stand_erfassen(
    zaehler: Zaehler,
    datum: datetime.date,
    stand: Decimal | str,
    *,
    art: str = Zaehlerstand.Art.ABGELESEN,
    foto: File[Any] | None = None,
    vermerk: str = "",
    uebergabe: Uebergabe | None = None,
) -> Zaehlerstand:
    """Erfasst einen Stand (H7.2, O2): abgelesen oder geschätzt, mit Foto.

    Abgelehnt wird ein Stand, der kleiner ist als der letzte Stand davor oder größer als der
    Stand danach (``StandUnplausibel``) oder der nicht in die Einbauzeit des Zählers fällt.
    Ein Datum in der Zukunft lehnen die Seiten ab (die Services rechnen nicht mit „heute“).
    Gibt es am Tag schon einen Stand, wird er korrigiert. ``uebergabe``: erfasst bei Übergabe
    oder Rückgabe. Ein offener Posten „Ablesung fehlt“, in dessen Ablesefenster der Tag
    liegt, schließt sich.
    """
    if art not in ERFASSBARE_ARTEN:
        raise ZaehlerFehler(f"Stände der Art {art} entstehen nur beim Einbau oder Ausbau.")
    with transaction.atomic():
        zaehler = Zaehler.objects.select_for_update().get(pk=zaehler.pk)
        eintrag = _stand_pruefen_und_schreiben(
            zaehler,
            datum,
            stand_lesen(stand),
            art,
            foto,
            vermerk=vermerk,
            uebergabe=uebergabe,
        )
        _ablesung_posten_schliessen(zaehler, nur_fuer=datum)
    return eintrag


# --- Übergabe und Rückgabe (Schritt 4, O1) -------------------------------------------


def zaehler_der_uebergabe(uebergabe: Uebergabe) -> list[Zaehler]:
    """Die Zähler der Einheiten des Vertrags, die am Tag der Übergabe eingebaut sind."""
    return zaehler_der_einheiten(uebergabe.vertrag.einheiten.all(), uebergabe.datum)


def _zaehler_finden(eintrag: Mapping[str, Any], erlaubt: dict[int, Zaehler]) -> Zaehler:
    wert = eintrag.get("zaehler")
    if isinstance(wert, Zaehler):
        wert = wert.pk
    if wert not in (None, ""):
        try:
            zaehler = erlaubt.get(int(wert))
        except (TypeError, ValueError):
            zaehler = None
        if zaehler is None:
            raise ZaehlerFehler(f"Zähler #{wert} gehört nicht zu diesem Vorgang.")
        return zaehler
    nummer = str(eintrag.get("nummer", "")).strip()
    for zaehler in erlaubt.values():
        if zaehler.nummer == nummer:
            return zaehler
    raise ZaehlerFehler(f"Zähler {nummer or '?'} hängt an keiner Einheit dieses Vertrags.")


def uebergabe_staende_erfassen(
    uebergabe: Uebergabe,
    eintraege: Iterable[Mapping[str, Any]],
) -> list[Zaehlerstand]:
    """Erfasst die Stände einer Übergabe oder Rückgabe (Datum der Übergabe), alles oder nichts.

    Ein Eintrag nennt den Zähler (``"zaehler"``: Zähler oder ID, sonst ``"nummer"``), den
    ``"stand"`` und optional ``"foto"`` und ``"art"`` (abgelesen oder geschätzt). Einträge
    mit Zähler-ID, aber ohne Stand bleiben unberücksichtigt (die Seite zeigt alle Zähler der
    Einheit). Erneutes Speichern korrigiert die Stände.
    """
    if uebergabe.status != Uebergabe.Status.BEGONNEN:
        raise ZaehlerFehler(f"{uebergabe} ist schon abgeschlossen.")
    erlaubt = {z.pk: z for z in zaehler_der_uebergabe(uebergabe)}
    ergebnis = []
    with transaction.atomic():
        for eintrag in eintraege:
            roh = str(eintrag.get("stand") or "").strip()
            nummer = str(eintrag.get("nummer") or "").strip()
            if not roh:
                if nummer or eintrag.get("foto"):
                    raise ZaehlerFehler(
                        f"Zähler {nummer or '?'}: Zu jeder Nummer und jedem Foto gehört ein Stand."
                    )
                continue
            zaehler = _zaehler_finden(eintrag, erlaubt)
            ergebnis.append(
                stand_erfassen(
                    zaehler,
                    uebergabe.datum,
                    roh,
                    art=str(eintrag.get("art") or Zaehlerstand.Art.ABGELESEN),
                    foto=eintrag.get("foto"),
                    uebergabe=uebergabe,
                )
            )
    return ergebnis


def uebergabe_datum_angleichen(uebergabe: Uebergabe) -> int:
    """Verschiebt die Stände einer begonnenen Übergabe auf ihr (geändertes) Datum."""
    anzahl = 0
    with transaction.atomic():
        for eintrag in uebergabe.zaehlerstaende.exclude(datum=uebergabe.datum).select_related(
            "zaehler"
        ):
            zaehler = Zaehler.objects.select_for_update().get(pk=eintrag.zaehler_id)
            vorhanden = _pruefen(
                zaehler, uebergabe.datum, eintrag.stand, eintrag.art, ausser=eintrag
            )
            if vorhanden is not None:
                raise ZaehlerFehler(
                    f"Zähler {zaehler.nummer}: Am {uebergabe.datum:%d.%m.%Y} steht schon ein Stand."
                )
            eintrag.datum = uebergabe.datum
            _speichern(eintrag)
            anzahl += 1
    return anzahl


def uebergabe_staende_protokoll(uebergabe: Uebergabe) -> list[dict[str, str]]:
    """Die Stände einer Übergabe für das Protokoll: Nummer, Stand, Art, Ort."""
    return [
        {
            "nummer": s.zaehler.nummer,
            "stand": s.stand_text,
            "art": s.get_art_display(),
            "einheit": s.zaehler.ort,
        }
        for s in uebergabe.zaehlerstaende.select_related("zaehler", "zaehler__einheit").order_by(
            "zaehler__nummer"
        )
    ]


# --- Lauf „zaehler_stichtag“ ---------------------------------------------------------


@dataclass(frozen=True)
class Stichtagspruefung:
    """Ergebnis von ``stichtag_pruefen``: nur Zähler."""

    stichtag: datetime.date | None
    fehlend: int
    neue_posten: list[Arbeitsposten]
    geschlossen: int


def _posten_hinweis(zaehler: Zaehler, stichtag: datetime.date) -> str:
    return (
        f"Zähler {zaehler.nummer} ({zaehler.get_art_display()}, {zaehler.ort}): "
        f"Stand zum {stichtag:%d.%m.%Y} fehlt."
    )


def stichtag_pruefen(heute: datetime.date, *, lauf: Lauf | None = None) -> Stichtagspruefung:
    """Legt im Ablesefenster je Zähler ohne Stand zum Stichtag einen Arbeitsposten
    „Ablesung fehlt“ an (fällig am Stichtag), höchstens einen je Zähler und Stichtag.

    Posten, deren Ablesung inzwischen vorliegt oder deren Zähler ausgebaut ist, schließen
    sich. Außerhalb des Ablesefensters (Standard 15.12. bis 15.01.) passiert nichts.
    """
    stichtag = aktueller_stichtag(heute)
    typ = ContentType.objects.get_for_model(Zaehler)
    offene = Arbeitsposten.objects.filter(
        art=ART_ABLESUNG_FEHLT, bezug_typ=typ, status__in=_POSTEN_OFFEN
    )

    geschlossen = 0
    for posten in offene.select_for_update():
        zaehler = Zaehler.objects.filter(pk=posten.bezug_id or 0).first()
        erledigt = zaehler is None or posten.faellig_am is None
        if zaehler is not None and posten.faellig_am is not None:
            erledigt = (
                not braucht_stand(zaehler, posten.faellig_am)
                or stand_zum_stichtag(zaehler, posten.faellig_am) is not None
            )
        if erledigt:
            kern.arbeitsposten_erledigen(posten)
            geschlossen += 1

    if stichtag is None:
        return Stichtagspruefung(stichtag=None, fehlend=0, neue_posten=[], geschlossen=geschlossen)

    mit_posten = set(
        offene.filter(bezug_typ=typ, faellig_am=stichtag).values_list("bezug_id", flat=True)
    )
    fehlend = 0
    neue: list[Arbeitsposten] = []
    kandidaten = Zaehler.objects.select_related("einheit", "objekt").order_by("nummer")
    for zaehler in kandidaten:
        if not braucht_stand(zaehler, stichtag) or stand_zum_stichtag(zaehler, stichtag):
            continue
        fehlend += 1
        if zaehler.pk in mit_posten:
            continue
        neue.append(
            kern.arbeitsposten_anlegen(
                ART_ABLESUNG_FEHLT,
                bezug=zaehler,
                faellig_am=stichtag,
                hinweis=_posten_hinweis(zaehler, stichtag),
                lauf=lauf,
            )
        )
    return Stichtagspruefung(
        stichtag=stichtag, fehlend=fehlend, neue_posten=neue, geschlossen=geschlossen
    )


__all__ = [
    "ART_ABLESUNG_FEHLT",
    "StandUnplausibel",
    "Stichtagspruefung",
    "Wechsel",
    "ZaehlerFehler",
    "ablesefenster",
    "aktueller_stichtag",
    "braucht_stand",
    "eingebaute_zaehler",
    "letzter_stichtag",
    "stand_erfassen",
    "stand_lesen",
    "stand_zum_stichtag",
    "stichtag_im_jahr",
    "stichtag_pruefen",
    "uebergabe_datum_angleichen",
    "uebergabe_staende_erfassen",
    "uebergabe_staende_protokoll",
    "zaehler_aendern",
    "zaehler_anlegen",
    "zaehler_ausbauen",
    "zaehler_der_einheiten",
    "zaehler_der_uebergabe",
    "zaehler_des_objekts",
    "zaehler_wechseln",
]
