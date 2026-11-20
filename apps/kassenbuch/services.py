"""Schreibweg des Kassenbuchs (CLAUDE.md, Regel 4.1).

Views, Läufe, Import und Testhof buchen, stornieren und schließen ab nur über diese Funktionen.
Meldungen und Hinweise nennen IDs, Daten und Zähler, nie Beträge, Namen oder Zwecke.

Regeln für Buchungen:

- Ein Umsatz wird in eine oder mehrere Buchungen aufgeteilt; die Summe der gültigen Buchungen
  ist genau sein Betrag (``umsatz_buchen``). Beträge sind positiv, die Richtung kommt vom Umsatz.
- Gebuchtes wird nie geändert, nur storniert (``umsatz_stornieren``). Die Stornobuchung hat die
  Gegenrichtung und zeigt mit ``storno_von`` auf das Original.
- Ein abgeschlossener Monat ist gesperrt (H6.6): In ihm entsteht keine Buchung mehr. Die
  Sperre gilt je Mandant des Bankkontos, denn ein Monatsabschluss umfasst die Konten eines
  Mandanten. Eine Stornobuchung oder neue Buchung zu einem Umsatz aus einem gesperrten Monat
  bekommt ein Datum im ersten offenen Monat.
"""

from __future__ import annotations

import datetime
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.core.files.base import ContentFile
from django.db import models, transaction
from django.db.models import Max, Min, Q, Sum
from django.db.models.functions import Coalesce
from django.template.loader import render_to_string

from apps.bank import fingerabdruck
from apps.bank.models import Bankkonto, Bankumsatz, Kontostand
from apps.kassenbuch import auswertung, pdf
from apps.kassenbuch.auswertung import monatsanfang, monatsende, naechster_monat, vormonat
from apps.kassenbuch.models import (
    Buchung,
    Kategorie,
    Monatsabschluss,
    Monatsbericht,
    Zuordnungsregel,
)
from apps.kern import services as kern_services
from apps.kern import zeit
from apps.kern.models import Arbeitsposten, Lauf, Mandant, Objekt
from apps.kern.vorgaenge import als_vorgang

logger = logging.getLogger(__name__)

#: Beide Arten melden sich über die Benachrichtigung (``HOF_BENACHRICHTIGUNG_ARTEN``).
ART_EINORDNEN = "Umsätze einordnen"
ART_ABSCHLUSS = "Monatsabschluss offen"
#: Aktionen am Posten „Umsätze einordnen“ (registriert in ``apps.kassenbuch.aktionen``).
AKTIONEN_EINORDNEN = ("umsatz_zuordnen", "umsatz_aufteilen", "regel_anlegen", "zurueckstellen")
AKTIONEN_ABSCHLUSS = ("abschluss_pruefen", "zurueckstellen")
#: Hinweis aus dem Abschluss (Schritt 3): Buchungen ohne Pflichtbeleg. Blockiert nicht.
ART_BELEGE_FEHLEN = "Belege fehlen"
AKTIONEN_BELEGE_FEHLEN = ("belege_pruefen", "zurueckstellen")

#: So viele Tage nach einem Eingang gilt ein Ausgang gleicher Höhe als mögliche Rückbuchung.
RUECKBUCHUNG_TAGE = 14

CENT = Decimal("0.01")
NULL = Decimal("0.00")
HUNDERT = Decimal("100")

EINGANG = Bankumsatz.Richtung.EINGANG
AUSGANG = Bankumsatz.Richtung.AUSGANG


class KassenbuchFehler(ValueError):
    """Eingaben passen nicht. Die Meldung nennt nur IDs, Feldnamen und Zähler."""


class MonatGesperrt(KassenbuchFehler):
    """Der Monat ist abgeschlossen; ändern geht nur per Stornobuchung im offenen Monat."""


class VorschauVeraltet(KassenbuchFehler):
    """Seit der Vorschau hat sich die Zahl der passenden Umsätze geändert."""


class RegelUeberschneidung(KassenbuchFehler):
    """Die Regel überschneidet sich mit einer gleichrangigen (Review 1, Punkt 5). ``andere``
    ist die vorhandene Regel, ``umsatz`` ein Umsatz, auf den beide passen (oder None)."""

    def __init__(self, text: str, *, andere: Zuordnungsregel, umsatz: Bankumsatz | None) -> None:
        super().__init__(text)
        self.andere = andere
        self.umsatz = umsatz


# --- Kategorien ----------------------------------------------------------------------


def _prozent(wert: Decimal | int | str, feld: str) -> Decimal:
    zahl = Decimal(wert).quantize(CENT)
    if not NULL <= zahl <= HUNDERT:
        raise KassenbuchFehler(f"{feld} muss zwischen 0 und 100 liegen.")
    return zahl


def kategorie_anlegen(
    bezeichnung: str,
    art: str,
    *,
    mandant: Mandant | None = None,
    wohnraum: bool = False,
    umlagefaehiger_anteil: Decimal | int | str = 0,
) -> Kategorie:
    """Legt eine Kategorie an. Entnahmen (B9) brauchen ihren Mandanten, alle anderen keinen."""
    bezeichnung = bezeichnung.strip()
    if not bezeichnung:
        raise KassenbuchFehler("Die Kategorie braucht eine Bezeichnung.")
    if art not in Kategorie.Art.values:
        raise KassenbuchFehler(f"Unbekannte Art der Kategorie: {art}")
    if art == Kategorie.Art.ENTNAHME and mandant is None:
        raise KassenbuchFehler("Eine Entnahme-Kategorie braucht ihren Mandanten.")
    if art != Kategorie.Art.ENTNAHME and mandant is not None:
        raise KassenbuchFehler("Nur Entnahme-Kategorien haben einen Mandanten.")
    if wohnraum and art != Kategorie.Art.EINNAHME:
        raise KassenbuchFehler("Wohnraum gibt es nur bei Einnahmen.")
    if Kategorie.objects.filter(bezeichnung__iexact=bezeichnung).exists():
        raise KassenbuchFehler("Eine Kategorie mit dieser Bezeichnung gibt es schon.")
    kategorie = Kategorie(
        bezeichnung=bezeichnung,
        art=art,
        mandant=mandant,
        wohnraum=wohnraum,
        umlagefaehiger_anteil=_prozent(umlagefaehiger_anteil, "Der umlagefähige Anteil"),
    )
    kategorie.full_clean()
    kategorie.save()
    return kategorie


def kategorie_aendern(
    kategorie: Kategorie,
    *,
    bezeichnung: str | None = None,
    aktiv: bool | None = None,
    umlagefaehiger_anteil: Decimal | int | str | None = None,
) -> Kategorie:
    """Ändert Bezeichnung, Vorgabe für den umlagefähigen Anteil oder den Status.

    Die Art bleibt: Gebuchte Werte sollen ihre Art nicht nachträglich wechseln.
    """
    if bezeichnung is not None:
        bezeichnung = bezeichnung.strip()
        if not bezeichnung:
            raise KassenbuchFehler("Die Kategorie braucht eine Bezeichnung.")
        if (
            Kategorie.objects.filter(bezeichnung__iexact=bezeichnung)
            .exclude(pk=kategorie.pk)
            .exists()
        ):
            raise KassenbuchFehler("Eine Kategorie mit dieser Bezeichnung gibt es schon.")
        kategorie.bezeichnung = bezeichnung
    if aktiv is not None:
        kategorie.aktiv = aktiv
    if umlagefaehiger_anteil is not None:
        kategorie.umlagefaehiger_anteil = _prozent(umlagefaehiger_anteil, "Der umlagefähige Anteil")
    kategorie.full_clean()
    kategorie.save()
    return kategorie


# --- Zuordnungsregeln (H6.3) ---------------------------------------------------------

REGEL_FELDER = (
    "bezeichnung",
    "gegenpartei",
    "gegen_iban",
    "zweck",
    "zweck_regex",
    "betrag_von",
    "betrag_bis",
    "richtung",
    "konto",
    "kategorie",
    "objekt",
    "mandant",
    "prioritaet",
    "aktiv",
)


def _regel_pruefen(regel: Zuordnungsregel) -> None:
    regel.bezeichnung = regel.bezeichnung.strip()
    regel.gegenpartei = regel.gegenpartei.strip()
    regel.gegen_iban = fingerabdruck.iban_normal(regel.gegen_iban)
    regel.zweck = regel.zweck.strip()
    if not regel.bezeichnung:
        raise KassenbuchFehler("Die Regel braucht eine Bezeichnung.")
    if not (
        regel.gegenpartei
        or regel.gegen_iban
        or regel.zweck
        or regel.betrag_von is not None
        or regel.betrag_bis is not None
        or regel.konto_id is not None
    ):
        raise KassenbuchFehler(
            "Die Regel braucht mindestens eine Bedingung: Gegenpartei, IBAN, Zweck, "
            "Betrag oder Konto."
        )
    if regel.zweck_regex:
        try:
            re.compile(regel.zweck)
        except re.error as fehler:
            raise KassenbuchFehler(f"Der reguläre Ausdruck ist ungültig ({fehler.msg}).") from None
    if regel.richtung and regel.richtung not in Bankumsatz.Richtung.values:
        raise KassenbuchFehler(f"Unbekannte Richtung: {regel.richtung}")
    for wert in (regel.betrag_von, regel.betrag_bis):
        if wert is not None and wert < 0:
            raise KassenbuchFehler("Beträge der Regel sind positiv; die Richtung steht extra.")
    if (
        regel.betrag_von is not None
        and regel.betrag_bis is not None
        and regel.betrag_von > regel.betrag_bis
    ):
        raise KassenbuchFehler("„Betrag ab“ liegt über „Betrag bis“.")
    kategorie = regel.kategorie
    if not kategorie.aktiv:
        raise KassenbuchFehler(f"Kategorie #{kategorie.pk} ist nicht aktiv.")
    if kategorie.art == Kategorie.Art.ENTNAHME and kategorie.mandant_id != regel.mandant_id:
        raise KassenbuchFehler(
            f"Kategorie #{kategorie.pk} ist eine Entnahme von Mandant #{kategorie.mandant_id}; "
            "die Regel muss denselben Mandanten nennen."
        )


#: Was eine Regel ausmacht: Bedingungen, Ergebnis und Priorität (nicht Bezeichnung und Status).
GLEICHE_REGEL_FELDER = (
    "gegen_iban",
    "zweck_regex",
    "betrag_von",
    "betrag_bis",
    "richtung",
    "konto_id",
    "kategorie_id",
    "objekt_id",
    "mandant_id",
    "prioritaet",
)


def gleiche_regel(regel: Zuordnungsregel) -> Zuordnungsregel | None:
    """Eine aktive Regel mit denselben Bedingungen, demselben Ergebnis und derselben Priorität
    wie ``regel`` (Review 3, Punkt 3), sonst None. Texte gelten ohne Groß und Klein; ein
    Regex nur genau gleich."""
    felder = {name: getattr(regel, name) for name in GLEICHE_REGEL_FELDER}
    zweck = {"zweck": regel.zweck} if regel.zweck_regex else {"zweck__iexact": regel.zweck}
    vorhandene = Zuordnungsregel.objects.filter(
        aktiv=True, gegenpartei__iexact=regel.gegenpartei, **zweck, **felder
    )
    if regel.pk is not None:
        vorhandene = vorhandene.exclude(pk=regel.pk)
    return vorhandene.order_by("pk").first()


# --- Überschneidung gleichrangiger Regeln (Review 1, Punkt 5) -------------------------


def _getrennt(a: Zuordnungsregel, b: Zuordnungsregel) -> bool:
    """Schließt eine Bedingung, die beide Regeln setzen, gemeinsame Treffer sicher aus?

    Verschiedenes Konto, verschiedene Richtung, verschiedene IBAN oder Betragsbereiche ohne
    Schnitt trennen immer. Bei Texten trennt eine Gegenpartei (bzw. ein Zweck ohne Regex), wenn
    keiner der beiden Texte den anderen enthält; ein Regex trennt von einem festen Zweck, auf
    den er nicht passt. Das ist eine Annahme aus der Praxis (zwei Namen stehen selten zugleich
    im Feld); vorhandene Umsätze, die auf beide passen, prüft ``_gemeinsamer_umsatz``.
    """
    if a.konto_id and b.konto_id and a.konto_id != b.konto_id:
        return True
    if a.richtung and b.richtung and a.richtung != b.richtung:
        return True
    if a.gegen_iban and b.gegen_iban and a.gegen_iban != b.gegen_iban:
        return True
    unten = max((w for w in (a.betrag_von, b.betrag_von) if w is not None), default=None)
    oben = min((w for w in (a.betrag_bis, b.betrag_bis) if w is not None), default=None)
    if unten is not None and oben is not None and unten > oben:
        return True
    if a.gegenpartei and b.gegenpartei:
        x, y = a.gegenpartei.casefold(), b.gegenpartei.casefold()
        if x not in y and y not in x:
            return True
    if a.zweck and b.zweck:
        if not a.zweck_regex and not b.zweck_regex:
            x, y = a.zweck.casefold(), b.zweck.casefold()
            return x not in y and y not in x
        if a.zweck_regex != b.zweck_regex:
            muster, text = (a.zweck, b.zweck) if a.zweck_regex else (b.zweck, a.zweck)
            return re.search(muster, text, flags=re.IGNORECASE) is None
        return a.zweck != b.zweck
    return False


def _umfasst(weit: Zuordnungsregel, eng: Zuordnungsregel) -> bool:
    """Passt ``weit`` auf jeden Umsatz, auf den ``eng`` passt? (Jede Bedingung von ``weit``
    steht auch in ``eng``, gleich oder strenger.)"""
    if weit.konto_id and weit.konto_id != eng.konto_id:
        return False
    if weit.richtung and weit.richtung != eng.richtung:
        return False
    if weit.gegen_iban and weit.gegen_iban != eng.gegen_iban:
        return False
    if weit.gegenpartei and weit.gegenpartei.casefold() not in eng.gegenpartei.casefold():
        return False
    if weit.zweck:
        if weit.zweck_regex:
            if not (eng.zweck_regex and eng.zweck == weit.zweck):
                return False
        elif eng.zweck_regex or weit.zweck.casefold() not in eng.zweck.casefold():
            return False
    if weit.betrag_von is not None and (eng.betrag_von is None or eng.betrag_von < weit.betrag_von):
        return False
    return weit.betrag_bis is None or (
        eng.betrag_bis is not None and eng.betrag_bis <= weit.betrag_bis
    )


def _gemeinsamer_umsatz(a: Zuordnungsregel, b: Zuordnungsregel) -> Bankumsatz | None:
    """Ein vorhandener Umsatz, auf den beide Regeln passen (der älteste), sonst None."""
    umsaetze = Bankumsatz.objects.all()
    for regel in (a, b):
        if regel.konto_id:
            umsaetze = umsaetze.filter(konto_id=regel.konto_id)
        if regel.richtung:
            umsaetze = umsaetze.filter(richtung=regel.richtung)
        if regel.betrag_von is not None:
            umsaetze = umsaetze.filter(betrag__gte=regel.betrag_von)
        if regel.betrag_bis is not None:
            umsaetze = umsaetze.filter(betrag__lte=regel.betrag_bis)
        if regel.gegenpartei:
            umsaetze = umsaetze.filter(gegenpartei__icontains=regel.gegenpartei)
        if regel.zweck and not regel.zweck_regex:
            umsaetze = umsaetze.filter(verwendungszweck__icontains=regel.zweck)
    for umsatz in umsaetze.order_by("buchungsdatum", "pk").iterator(chunk_size=500):
        if regel_passt(a, umsatz) and regel_passt(b, umsatz):
            return umsatz
    return None


def ueberschneidungen(regel: Zuordnungsregel) -> list[tuple[Zuordnungsregel, Bankumsatz | None]]:
    """Aktive Regeln mit derselben Priorität, die sich mit ``regel`` überschneiden.

    Überschneiden heißt: Keine Bedingung trennt die beiden (``_getrennt``), und entweder
    umfasst eine Regel die andere ganz (gleiche oder strengere Bedingungen), oder ein
    vorhandener Umsatz passt auf beide. Zu jeder Regel steht der gemeinsame Umsatz, falls es
    einen gibt.
    """
    if not regel.aktiv:
        return []
    andere = Zuordnungsregel.objects.filter(aktiv=True, prioritaet=regel.prioritaet)
    if regel.pk is not None:
        andere = andere.exclude(pk=regel.pk)
    treffer: list[tuple[Zuordnungsregel, Bankumsatz | None]] = []
    for andere_regel in andere.order_by("pk"):
        if _getrennt(regel, andere_regel):
            continue
        if _umfasst(regel, andere_regel) or _umfasst(andere_regel, regel):
            treffer.append((andere_regel, _gemeinsamer_umsatz(regel, andere_regel)))
            continue
        gemeinsam = _gemeinsamer_umsatz(regel, andere_regel)
        if gemeinsam is not None:
            treffer.append((andere_regel, gemeinsam))
    return treffer


def _ueberschneidung_pruefen(regel: Zuordnungsregel) -> None:
    """Zwei Regeln mit gleicher Priorität und überlappenden Bedingungen lassen sich nicht
    speichern (Review 1, Punkt 5): Sonst wäre jeder gemeinsame Umsatz mehrdeutig."""
    treffer = ueberschneidungen(regel)
    if not treffer:
        return
    andere, umsatz = treffer[0]
    beleg = f" Umsatz #{umsatz.pk} passt auf beide." if umsatz is not None else ""
    raise RegelUeberschneidung(
        f"Die Regel überschneidet sich mit Regel #{andere.pk}, die dieselbe Priorität "
        f"({regel.prioritaet}) hat.{beleg} Bitte eine andere Priorität wählen oder die "
        "Bedingungen enger fassen.",
        andere=andere,
        umsatz=umsatz,
    )


def regel_anlegen(
    bezeichnung: str,
    *,
    kategorie: Kategorie,
    objekt: Objekt,
    mandant: Mandant | None = None,
    gegenpartei: str = "",
    gegen_iban: str = "",
    zweck: str = "",
    zweck_regex: bool = False,
    betrag_von: Decimal | None = None,
    betrag_bis: Decimal | None = None,
    richtung: str = "",
    konto: Bankkonto | None = None,
    prioritaet: int = 10,
) -> Zuordnungsregel:
    """Legt eine Regel an. Ohne Mandant gilt der Mandant des Objekts. Gibt es schon eine
    gleiche Regel (``gleiche_regel``), kommt sie zurück, statt eine zweite anzulegen."""
    regel, _neu = regel_anlegen_oder_finden(
        bezeichnung,
        kategorie=kategorie,
        objekt=objekt,
        mandant=mandant,
        gegenpartei=gegenpartei,
        gegen_iban=gegen_iban,
        zweck=zweck,
        zweck_regex=zweck_regex,
        betrag_von=betrag_von,
        betrag_bis=betrag_bis,
        richtung=richtung,
        konto=konto,
        prioritaet=prioritaet,
    )
    return regel


def regel_anlegen_oder_finden(
    bezeichnung: str,
    *,
    kategorie: Kategorie,
    objekt: Objekt,
    mandant: Mandant | None = None,
    gegenpartei: str = "",
    gegen_iban: str = "",
    zweck: str = "",
    zweck_regex: bool = False,
    betrag_von: Decimal | None = None,
    betrag_bis: Decimal | None = None,
    richtung: str = "",
    konto: Bankkonto | None = None,
    prioritaet: int = 10,
) -> tuple[Zuordnungsregel, bool]:
    """Wie ``regel_anlegen``, dazu ob die Regel neu ist (Review 3, Punkt 3): Eine gleiche
    Regel wird verwendet, nicht ein zweites Mal angelegt. Für jeden Weg, der Regeln anlegt
    (Arbeitsliste, Regelseite, Klärfall, Import)."""
    regel = Zuordnungsregel(
        bezeichnung=bezeichnung,
        kategorie=kategorie,
        objekt=objekt,
        mandant=mandant or objekt.mandant,
        gegenpartei=gegenpartei,
        gegen_iban=gegen_iban,
        zweck=zweck,
        zweck_regex=zweck_regex,
        betrag_von=betrag_von,
        betrag_bis=betrag_bis,
        richtung=richtung,
        konto=konto,
        prioritaet=prioritaet,
    )
    _regel_pruefen(regel)
    vorhanden = gleiche_regel(regel)
    if vorhanden is not None:
        return vorhanden, False
    _ueberschneidung_pruefen(regel)
    regel.full_clean()
    regel.save()
    return regel, True


def regel_aendern(regel: Zuordnungsregel, **felder: Any) -> Zuordnungsregel:
    """Ändert Bedingungen, Ergebnis, Priorität oder Status einer Regel (steht im Protokoll)."""
    unbekannt = set(felder) - set(REGEL_FELDER)
    if unbekannt:
        raise KassenbuchFehler(f"Unbekannte Felder der Regel: {sorted(unbekannt)}")
    for name, wert in felder.items():
        setattr(regel, name, wert)
    if "objekt" in felder and "mandant" not in felder:
        regel.mandant = regel.objekt.mandant
    _regel_pruefen(regel)
    _ueberschneidung_pruefen(regel)
    regel.full_clean()
    regel.save()
    return regel


def aktive_regeln() -> list[Zuordnungsregel]:
    """Alle Regeln, die ein Lauf anwendet: aktiv und mit aktiver Kategorie."""
    return list(
        Zuordnungsregel.objects.filter(aktiv=True, kategorie__aktiv=True)
        .select_related("kategorie", "objekt", "mandant")
        .order_by("-prioritaet", "pk")
    )


def _zweck_passt(regel: Zuordnungsregel, zweck: str) -> bool:
    if regel.zweck_regex:
        return re.search(regel.zweck, zweck, flags=re.IGNORECASE) is not None
    return regel.zweck.casefold() in zweck.casefold()


def regel_passt(regel: Zuordnungsregel, umsatz: Bankumsatz) -> bool:
    """Passen alle gesetzten Bedingungen der Regel auf den Umsatz?"""
    if regel.konto_id is not None and regel.konto_id != umsatz.konto_id:
        return False
    if regel.richtung and regel.richtung != umsatz.richtung:
        return False
    if regel.gegen_iban and regel.gegen_iban != fingerabdruck.iban_normal(umsatz.gegen_iban):
        return False
    if regel.gegenpartei and regel.gegenpartei.casefold() not in umsatz.gegenpartei.casefold():
        return False
    if regel.zweck and not _zweck_passt(regel, umsatz.verwendungszweck):
        return False
    if regel.betrag_von is not None and umsatz.betrag < regel.betrag_von:
        return False
    return regel.betrag_bis is None or umsatz.betrag <= regel.betrag_bis


@dataclass(frozen=True)
class Einordnung:
    """Ergebnis der Regeln für einen Umsatz. ``regel`` ist nur bei einem eindeutigen Treffer
    gesetzt: genau eine passende Regel mit der höchsten Priorität."""

    treffer: tuple[Zuordnungsregel, ...]
    regel: Zuordnungsregel | None

    @property
    def mehrdeutig(self) -> bool:
        return bool(self.treffer) and self.regel is None

    @property
    def gleichauf(self) -> list[Zuordnungsregel]:
        """Die Regeln mit der höchsten Priorität unter den Treffern."""
        if not self.treffer:
            return []
        hoechste = max(r.prioritaet for r in self.treffer)
        return [r for r in self.treffer if r.prioritaet == hoechste]


def einordnen(umsatz: Bankumsatz, regeln: Sequence[Zuordnungsregel]) -> Einordnung:
    treffer = tuple(r for r in regeln if regel_passt(r, umsatz))
    if not treffer:
        return Einordnung(treffer=(), regel=None)
    hoechste = max(r.prioritaet for r in treffer)
    oben = [r for r in treffer if r.prioritaet == hoechste]
    return Einordnung(treffer=treffer, regel=oben[0] if len(oben) == 1 else None)


# --- Zustand eines Umsatzes ----------------------------------------------------------


def gueltige_buchungen() -> models.QuerySet[Buchung]:
    """Buchungen, die zählen: weder Storno noch storniert."""
    return Buchung.objects.filter(storno_von__isnull=True, storniert_durch__isnull=True)


def _gebucht_summe() -> Coalesce:
    gueltig = Q(buchungen__storno_von__isnull=True, buchungen__storniert_durch__isnull=True)
    return Coalesce(
        Sum("buchungen__betrag", filter=gueltig),
        NULL,
        output_field=models.DecimalField(max_digits=14, decimal_places=2),
    )


def umsaetze_mit_stand() -> models.QuerySet[Bankumsatz]:
    """Umsätze mit ``gebucht``: Summe ihrer gültigen Buchungen."""
    return Bankumsatz.objects.annotate(gebucht=_gebucht_summe())


def nicht_gebuchte_umsaetze() -> models.QuerySet[Bankumsatz]:
    """Umsätze, deren gültige Buchungen nicht ihren Betrag ergeben."""
    return umsaetze_mit_stand().exclude(Q(gebucht=models.F("betrag")))


def ist_gebucht(umsatz: Bankumsatz) -> bool:
    summe = gueltige_buchungen().filter(umsatz=umsatz).aggregate(s=Sum("betrag"))["s"]
    return bool(summe == umsatz.betrag)


# --- Sperre (H6.6) -------------------------------------------------------------------


def ist_gesperrt(mandant_id: int, datum: datetime.date) -> bool:
    """Ist der Monat des Datums für den Mandanten abgeschlossen?"""
    return Monatsabschluss.objects.filter(
        mandant_id=mandant_id, monat=monatsanfang(datum), abgeschlossen_am__isnull=False
    ).exists()


def offenes_datum(mandant_id: int, datum: datetime.date) -> datetime.date:
    """Das Datum selbst, wenn sein Monat offen ist, sonst der Erste des ersten offenen Monats."""
    if not ist_gesperrt(mandant_id, datum):
        return datum
    letzter = Monatsabschluss.objects.filter(
        mandant_id=mandant_id, abgeschlossen_am__isnull=False
    ).aggregate(m=Max("monat"))["m"]
    if letzter is None:  # kommt nicht vor: ist_gesperrt hat einen gefunden
        return datum
    return naechster_monat(letzter)


def _datum_pruefen(
    umsatz: Bankumsatz, datum: datetime.date | None, mandant_id: int
) -> datetime.date:
    if datum is None:
        return offenes_datum(mandant_id, umsatz.buchungsdatum)
    if monatsanfang(datum) < monatsanfang(umsatz.buchungsdatum):
        raise KassenbuchFehler(
            f"Umsatz #{umsatz.pk}: Das Datum im Kassenbuch liegt vor dem Monat des Umsatzes."
        )
    if ist_gesperrt(mandant_id, datum):
        raise MonatGesperrt(
            f"Der Monat {datum:%m/%Y} ist für Mandant #{mandant_id} abgeschlossen. Änderungen "
            "nur per Stornobuchung im offenen Monat."
        )
    return datum


# --- Buchen --------------------------------------------------------------------------


@dataclass(frozen=True)
class Teil:
    """Ein Teil eines Umsatzes. Ohne Mandant gilt der des Objekts; ohne Leistungsjahr das Jahr
    des Umsatzes; ohne umlagefähigen Anteil die Vorgabe der Kategorie."""

    betrag: Decimal
    kategorie: Kategorie
    objekt: Objekt
    mandant: Mandant | None = None
    leistungsjahr: int | None = None
    umlagefaehiger_anteil: Decimal | None = None


def _teil_pruefen(umsatz: Bankumsatz, nummer: int, teil: Teil) -> None:
    if teil.betrag <= 0:
        raise KassenbuchFehler(f"Umsatz #{umsatz.pk}, Teil {nummer}: Betrag muss positiv sein.")
    if teil.betrag != teil.betrag.quantize(CENT):
        raise KassenbuchFehler(
            f"Umsatz #{umsatz.pk}, Teil {nummer}: Betrag hat mehr als zwei Nachkommastellen."
        )
    if not teil.kategorie.aktiv:
        raise KassenbuchFehler(f"Kategorie #{teil.kategorie.pk} ist nicht aktiv.")
    if not teil.objekt.aktiv:
        raise KassenbuchFehler(f"Objekt #{teil.objekt.pk} ist nicht aktiv.")
    mandant = teil.mandant or teil.objekt.mandant
    if teil.kategorie.art == Kategorie.Art.ENTNAHME and teil.kategorie.mandant_id != mandant.pk:
        raise KassenbuchFehler(
            f"Umsatz #{umsatz.pk}, Teil {nummer}: Die Entnahme-Kategorie #{teil.kategorie.pk} "
            f"gehört zu Mandant #{teil.kategorie.mandant_id}, nicht zu #{mandant.pk}."
        )
    if teil.leistungsjahr is not None and not 1990 <= teil.leistungsjahr <= 2100:
        raise KassenbuchFehler(f"Umsatz #{umsatz.pk}, Teil {nummer}: Leistungsjahr unplausibel.")


@transaction.atomic
def umsatz_buchen(
    umsatz: Bankumsatz,
    teile: Sequence[Teil],
    *,
    datum: datetime.date | None = None,
    regel: Zuordnungsregel | None = None,
    lauf: Lauf | None = None,
) -> list[Buchung]:
    """Bucht einen Umsatz in einem oder mehreren Teilen. Die Summe muss dem Umsatz entsprechen.

    Der Umsatz darf noch keine gültige Buchung haben (sonst erst ``umsatz_stornieren``). Ohne
    Datum gilt der Buchungstag, oder im gesperrten Monat der Erste des ersten offenen Monats.
    Offene Arbeitsposten „Umsätze einordnen“ zum Umsatz sind danach erledigt.
    """
    umsatz = Bankumsatz.objects.select_for_update().select_related("konto").get(pk=umsatz.pk)
    if not teile:
        raise KassenbuchFehler(f"Umsatz #{umsatz.pk}: Es fehlt mindestens ein Teil.")
    for nummer, teil in enumerate(teile, start=1):
        _teil_pruefen(umsatz, nummer, teil)
    summe = sum((t.betrag for t in teile), NULL)
    if summe != umsatz.betrag:
        raise KassenbuchFehler(
            f"Umsatz #{umsatz.pk}: Die Summe der Teile muss genau den Betrag des Umsatzes ergeben."
        )
    if gueltige_buchungen().filter(umsatz=umsatz).exists():
        raise KassenbuchFehler(f"Umsatz #{umsatz.pk} ist schon gebucht. Erst stornieren.")
    tag = _datum_pruefen(umsatz, datum, umsatz.konto.mandant_id)

    buchungen = []
    for teil in teile:
        anteil = (
            teil.kategorie.umlagefaehiger_anteil
            if teil.umlagefaehiger_anteil is None
            else _prozent(teil.umlagefaehiger_anteil, "Der umlagefähige Anteil")
        )
        buchung = Buchung(
            umsatz=umsatz,
            datum=tag,
            betrag=teil.betrag,
            richtung=umsatz.richtung,
            kategorie=teil.kategorie,
            objekt=teil.objekt,
            mandant=teil.mandant or teil.objekt.mandant,
            leistungsjahr=teil.leistungsjahr or umsatz.buchungsdatum.year,
            umlagefaehiger_anteil=anteil,
            wohnraum=teil.kategorie.wohnraum,
            regel=regel,
            lauf=lauf,
        )
        buchung.save()
        buchungen.append(buchung)
    for posten in _posten_zu(ART_EINORDNEN, umsatz, offen=True):
        kern_services.arbeitsposten_erledigen(posten)
    return buchungen


def umsatz_zuordnen(
    umsatz: Bankumsatz,
    *,
    kategorie: Kategorie,
    objekt: Objekt,
    mandant: Mandant | None = None,
    leistungsjahr: int | None = None,
    umlagefaehiger_anteil: Decimal | None = None,
    datum: datetime.date | None = None,
) -> list[Buchung]:
    """Bucht einen Umsatz ganz auf eine Kategorie (Aktion „Zuordnen“)."""
    teil = Teil(
        betrag=umsatz.betrag,
        kategorie=kategorie,
        objekt=objekt,
        mandant=mandant,
        leistungsjahr=leistungsjahr,
        umlagefaehiger_anteil=umlagefaehiger_anteil,
    )
    return umsatz_buchen(umsatz, [teil], datum=datum)


def nach_regel_buchen(
    umsatz: Bankumsatz, regel: Zuordnungsregel, *, lauf: Lauf | None = None
) -> list[Buchung]:
    teil = Teil(
        betrag=umsatz.betrag, kategorie=regel.kategorie, objekt=regel.objekt, mandant=regel.mandant
    )
    return umsatz_buchen(umsatz, [teil], regel=regel, lauf=lauf)


@transaction.atomic
def umsatz_stornieren(
    umsatz: Bankumsatz,
    *,
    datum: datetime.date | None = None,
    neu_einordnen: bool = True,
    lauf: Lauf | None = None,
) -> list[Buchung]:
    """Storniert alle gültigen Buchungen eines Umsatzes. Gibt die Stornobuchungen zurück.

    Jede Stornobuchung hat die Gegenrichtung ihres Originals und dessen Kategorie, Objekt und
    Mandant. Ihr Datum ist das des Originals, liegt dessen Monat schon abgeschlossen, der Erste
    des ersten offenen Monats. Mit ``neu_einordnen`` steht der Umsatz danach in der Arbeitsliste.
    """
    umsatz = Bankumsatz.objects.select_for_update().select_related("konto").get(pk=umsatz.pk)
    originale = list(gueltige_buchungen().filter(umsatz=umsatz).order_by("pk"))
    if not originale:
        raise KassenbuchFehler(f"Umsatz #{umsatz.pk} hat keine gültige Buchung zum Stornieren.")
    mandant_id = umsatz.konto.mandant_id
    stornos = []
    for original in originale:
        tag = (
            offenes_datum(mandant_id, original.datum)
            if datum is None
            else _datum_pruefen(umsatz, datum, mandant_id)
        )
        storno = Buchung(
            umsatz=umsatz,
            datum=tag,
            betrag=original.betrag,
            richtung=AUSGANG if original.richtung == EINGANG else EINGANG,
            kategorie=original.kategorie,
            objekt=original.objekt,
            mandant=original.mandant,
            leistungsjahr=original.leistungsjahr,
            umlagefaehiger_anteil=original.umlagefaehiger_anteil,
            wohnraum=original.wohnraum,
            lauf=lauf,
            storno_von=original,
        )
        storno.save()
        stornos.append(storno)
    logger.info("Umsatz #%s: %s Buchungen storniert", umsatz.pk, len(stornos))
    if neu_einordnen:
        einordnen_posten(
            umsatz,
            f"Umsatz #{umsatz.pk}: Die Buchung ist storniert. Bitte neu einordnen.",
            lauf=lauf,
        )
    return stornos


@transaction.atomic
def umsatz_umbuchen(
    umsatz: Bankumsatz, teile: Sequence[Teil], *, datum: datetime.date | None = None
) -> list[Buchung]:
    """Storniert die gültigen Buchungen eines Umsatzes und bucht ihn neu (im offenen Monat)."""
    if gueltige_buchungen().filter(umsatz=umsatz).exists():
        umsatz_stornieren(umsatz, neu_einordnen=False)
    return umsatz_buchen(umsatz, teile, datum=datum)


# --- Vorschlag übernehmen (Review 2, B3) ---------------------------------------------


def regel_aus_umsatz_anlegen(
    umsatz: Bankumsatz,
    *,
    kategorie: Kategorie,
    objekt: Objekt,
    mandant: Mandant | None = None,
    prioritaet: int = 10,
) -> tuple[Zuordnungsregel, bool]:
    """„Als Regel merken“: Regel mit der Gegen-IBAN (ohne IBAN: der Gegenpartei) und der
    Richtung des Umsatzes, dazu ob sie neu ist. Eine gleiche Regel wird verwendet (Review 3,
    Punkt 3); überschneidet sie sich mit einer anderen gleichrangigen, kommt
    ``RegelUeberschneidung`` (Review 1, Punkt 5)."""
    return regel_anlegen_oder_finden(
        (umsatz.gegenpartei or f"Regel aus Umsatz #{umsatz.pk}")[:200],
        kategorie=kategorie,
        objekt=objekt,
        mandant=mandant,
        gegen_iban=umsatz.gegen_iban,
        gegenpartei="" if umsatz.gegen_iban else umsatz.gegenpartei[:200],
        richtung=umsatz.richtung,
        prioritaet=prioritaet,
    )


@dataclass
class Uebernahme:
    """Ergebnis von ``einordnen_uebernehmen``: die neuen Buchungen und, mit „Als Regel
    merken“, die Regel (neu oder schon vorhanden) oder der Grund, warum es keine gibt."""

    buchungen: list[Buchung] = field(default_factory=list)
    regel: Zuordnungsregel | None = None
    #: Ist ``regel`` gerade angelegt (sonst gab es sie schon)?
    regel_neu: bool = False
    regel_fehler: str = ""
    #: Die vorhandene Regel, mit der sich die neue überschnitten hätte
    regel_konflikt: Zuordnungsregel | None = None

    @property
    def umsaetze(self) -> int:
        return len({b.umsatz_id for b in self.buchungen})


def _vorhandene_regel_passt(
    regel: Zuordnungsregel | None, umsatz: Bankumsatz, kategorie: Kategorie, objekt: Objekt
) -> bool:
    return (
        regel is not None
        and regel.aktiv
        and regel.kategorie_id == kategorie.pk
        and regel.objekt_id == objekt.pk
        and regel_passt(regel, umsatz)
    )


@transaction.atomic
def einordnen_uebernehmen(
    umsaetze: Sequence[Bankumsatz],
    *,
    kategorie: Kategorie,
    objekt: Objekt,
    mandant: Mandant | None = None,
    regel_merken: bool = False,
    regel: Zuordnungsregel | None = None,
    leistungsjahr: int | None = None,
    umlagefaehiger_anteil: Decimal | None = None,
) -> Uebernahme:
    """Bucht Umsätze ganz auf eine Kategorie und ein Objekt (Arbeitsliste: „Übernehmen“,
    „für alle anwenden“). Schon gebuchte Teile werden vorher storniert.

    Mit ``regel_merken`` entsteht zuerst eine Regel aus dem ersten Umsatz; jeder Umsatz, auf
    den sie passt, trägt sie in seiner Buchung. Gibt es die Regel schon (``regel`` aus dem
    Vorschlag mit gleichem Ergebnis, oder eine gleiche Regel), wird sie verwendet und nicht
    neu angelegt (Review 3, Punkt 3). Lässt sich die Regel nicht anlegen, wird trotzdem
    gebucht, und ``regel_fehler`` nennt den Grund. ``regel`` ist die Regel, aus der der
    Vorschlag stammt (bei gleichem Ergebnis steht sie an der Buchung).
    """
    ergebnis = Uebernahme()
    if not umsaetze:
        return ergebnis
    if regel_merken:
        if _vorhandene_regel_passt(regel, umsaetze[0], kategorie, objekt):
            ergebnis.regel = regel
        else:
            try:
                with transaction.atomic():
                    ergebnis.regel, ergebnis.regel_neu = regel_aus_umsatz_anlegen(
                        umsaetze[0], kategorie=kategorie, objekt=objekt, mandant=mandant
                    )
            except RegelUeberschneidung as fehler:
                ergebnis.regel_fehler = str(fehler)
                ergebnis.regel_konflikt = fehler.andere
            except KassenbuchFehler as fehler:
                ergebnis.regel_fehler = str(fehler)
    for umsatz in umsaetze:
        nach: Zuordnungsregel | None = None
        if ergebnis.regel is not None and regel_passt(ergebnis.regel, umsatz):
            nach = ergebnis.regel
        elif _vorhandene_regel_passt(regel, umsatz, kategorie, objekt):
            nach = regel
        if gueltige_buchungen().filter(umsatz=umsatz).exists():
            umsatz_stornieren(umsatz, neu_einordnen=False)
        teil = Teil(
            betrag=umsatz.betrag,
            kategorie=kategorie,
            objekt=objekt,
            mandant=mandant,
            leistungsjahr=leistungsjahr,
            umlagefaehiger_anteil=umlagefaehiger_anteil,
        )
        ergebnis.buchungen += umsatz_buchen(umsatz, [teil], regel=nach)
    return ergebnis


@transaction.atomic
def regel_anwenden_auf(regel: Zuordnungsregel, umsaetze: Sequence[Bankumsatz]) -> list[Bankumsatz]:
    """„Regel anwenden“ an einer Gruppe der Arbeitsliste (Review 3, Punkt 3): bucht die
    genannten Umsätze nach der Regel, sofern sie noch offen sind und die Regel bei ihnen
    eindeutig gewinnt. Die übrigen bleiben, wie sie sind; zurück kommen die gebuchten."""
    if not regel.aktiv or not regel.kategorie.aktiv:
        raise KassenbuchFehler(f"Regel #{regel.pk} ist nicht aktiv und bucht nichts.")
    regeln = aktive_regeln()
    gebucht = []
    for umsatz in umsaetze:
        if gueltige_buchungen().filter(umsatz=umsatz).exists():
            continue
        einordnung = einordnen(umsatz, regeln)
        if einordnung.regel is None or einordnung.regel.pk != regel.pk:
            continue
        nach_regel_buchen(umsatz, regel)
        gebucht.append(umsatz)
    return gebucht


# --- Protokoll: Rücknahme als Storno -------------------------------------------------


def _buchung_zuruecknehmen(ereignis: kern_services.Ereignis) -> None:
    """„Zurücknehmen“ im Protokoll storniert eine Buchung, statt sie zu löschen."""
    if ereignis.art != "insert":
        raise kern_services.RuecknahmeNichtMoeglich(
            f"Buchung #{ereignis.objekt_id}: Nur die Anlage einer Buchung lässt sich "
            "zurücknehmen, und zwar als Storno."
        )
    buchung = Buchung.objects.filter(pk=ereignis.objekt_id).select_related("umsatz").first()
    if buchung is None:
        raise kern_services.RuecknahmeNichtMoeglich(
            f"Buchung #{ereignis.objekt_id} gibt es nicht mehr."
        )
    if buchung.ist_storno:
        raise kern_services.RuecknahmeNichtMoeglich(
            f"Buchung #{buchung.pk} ist eine Stornobuchung. Den Umsatz bitte neu buchen."
        )
    if Buchung.objects.filter(storno_von=buchung).exists():
        raise kern_services.RuecknahmeNichtMoeglich(f"Buchung #{buchung.pk} ist schon storniert.")
    umsatz_stornieren(buchung.umsatz)


def _endgueltig(titel: str) -> kern_services.StornoWeg:
    def verweigern(ereignis: kern_services.Ereignis) -> None:
        raise kern_services.RuecknahmeNichtMoeglich(
            f"{titel} #{ereignis.objekt_id} lässt sich nicht zurücknehmen: Ein abgeschlossener "
            "Monat bleibt abgeschlossen. Änderungen nur per Stornobuchung im offenen Monat."
        )

    return verweigern


def stornowege_registrieren() -> None:
    """Meldet beim Kern an, wie Rücknahmen im Kassenbuch laufen (beim Start der App)."""
    kern_services.stornoweg_registrieren(Buchung, _buchung_zuruecknehmen)
    kern_services.stornoweg_registrieren(Monatsabschluss, _endgueltig("Monatsabschluss"))
    kern_services.stornoweg_registrieren(Monatsbericht, _endgueltig("Monatsbericht"))


# --- Arbeitsposten -------------------------------------------------------------------


def _posten_zu(art: str, bezug: models.Model, *, offen: bool = False) -> list[Arbeitsposten]:
    posten = Arbeitsposten.objects.filter(
        art=art, bezug_typ=ContentType.objects.get_for_model(bezug), bezug_id=bezug.pk
    )
    if offen:
        posten = posten.filter(
            status__in=[Arbeitsposten.Status.OFFEN, Arbeitsposten.Status.ZURUECKGESTELLT]
        )
    return list(posten)


def einordnen_posten(
    umsatz: Bankumsatz, hinweis: str, *, lauf: Lauf | None = None
) -> Arbeitsposten | None:
    """Ein Posten „Umsätze einordnen“ je Umsatz, solange einer offen oder zurückgestellt ist."""
    if _posten_zu(ART_EINORDNEN, umsatz, offen=True):
        return None
    return kern_services.arbeitsposten_anlegen(
        ART_EINORDNEN,
        bezug=umsatz,
        aktionen=AKTIONEN_EINORDNEN,
        faellig_am=umsatz.buchungsdatum,
        hinweis=hinweis,
        lauf=lauf,
    )


def einordnen_posten_offen(umsatz: Bankumsatz) -> Arbeitsposten | None:
    """Der offene oder zurückgestellte Posten „Umsätze einordnen“ zum Umsatz, falls es ihn gibt."""
    posten = _posten_zu(ART_EINORDNEN, umsatz, offen=True)
    return posten[0] if posten else None


def offene_posten_umsatz_ids() -> set[int]:
    typ = ContentType.objects.get_for_model(Bankumsatz)
    ids = Arbeitsposten.objects.filter(
        art=ART_EINORDNEN,
        bezug_typ=typ,
        status__in=[Arbeitsposten.Status.OFFEN, Arbeitsposten.Status.ZURUECKGESTELLT],
    ).values_list("bezug_id", flat=True)
    return {i for i in ids if i is not None}


def rueckbuchung_zu(umsatz: Bankumsatz) -> Bankumsatz | None:
    """Ein Ausgang kann die Rückbuchung eines Eingangs sein: gleiches Konto, gleiche Gegen-IBAN,
    gleicher Betrag, höchstens 14 Tage vorher. Nur ein Vorschlag, gebucht wird er nie allein."""
    if umsatz.richtung != AUSGANG or not umsatz.gegen_iban:
        return None
    return (
        Bankumsatz.objects.filter(
            konto_id=umsatz.konto_id,
            richtung=EINGANG,
            gegen_iban=umsatz.gegen_iban,
            betrag=umsatz.betrag,
            buchungsdatum__lte=umsatz.buchungsdatum,
            buchungsdatum__gte=umsatz.buchungsdatum - datetime.timedelta(days=RUECKBUCHUNG_TAGE),
        )
        .exclude(pk=umsatz.pk)
        .order_by("-buchungsdatum", "-pk")
        .first()
    )


def _einordnen_hinweis(umsatz: Bankumsatz, einordnung: Einordnung) -> str:
    kopf = (
        f"Umsatz #{umsatz.pk} vom {umsatz.buchungsdatum:%d.%m.%Y} "
        f"({umsatz.get_richtung_display()}, Konto #{umsatz.konto_id}): "
    )
    if einordnung.mehrdeutig:
        ids = ", ".join(f"#{r.pk}" for r in einordnung.gleichauf)
        text = kopf + f"Mehrere Regeln passen mit gleicher Priorität ({ids})."
    else:
        text = kopf + "Keine Regel passt."
    zurueck = rueckbuchung_zu(umsatz)
    if zurueck is not None:
        text += f" Vermutlich Rückbuchung zu Umsatz #{zurueck.pk}."
    return text


# --- Lauf „kassenbuch_einordnen“ -----------------------------------------------------


@dataclass
class EinordnenErgebnis:
    gebucht: int = 0
    arbeitsposten: int = 0
    fehler: int = 0
    gebuchte_umsaetze: list[int] = field(default_factory=list)


def umsaetze_einordnen(
    *, ab: datetime.date | None = None, lauf: Lauf | None = None
) -> EinordnenErgebnis:
    """Wendet die Regeln auf alle Umsätze ohne Buchung an (H6.3). Idempotent.

    - eindeutig (genau eine Regel mit der höchsten Priorität): gebucht,
    - keine oder mehrere gleichrangige Regeln: Arbeitsposten „Umsätze einordnen“,
    - Umsätze, die schon einmal gebucht waren und jetzt (storniert oder nur teilweise) offen
      sind, bucht der Lauf nie selbst: Arbeitsposten.

    Umsätze mit offenem oder zurückgestelltem Posten bleiben unberührt.
    """
    ergebnis = EinordnenErgebnis()
    regeln = aktive_regeln()
    umsaetze = umsaetze_mit_stand().select_related("konto").order_by("buchungsdatum", "pk")
    if ab is not None:
        umsaetze = umsaetze.filter(buchungsdatum__gte=ab)
    mit_posten = offene_posten_umsatz_ids()
    schon_gebucht = set(Buchung.objects.values_list("umsatz_id", flat=True).distinct())

    for umsatz in umsaetze.exclude(Q(gebucht=models.F("betrag"))).iterator(chunk_size=500):
        if umsatz.pk in mit_posten:
            continue
        if umsatz.pk in schon_gebucht:
            if einordnen_posten(
                umsatz,
                f"Umsatz #{umsatz.pk} vom {umsatz.buchungsdatum:%d.%m.%Y} ist nicht mehr "
                "vollständig gebucht (storniert oder nur teilweise).",
                lauf=lauf,
            ):
                ergebnis.arbeitsposten += 1
            continue
        einordnung = einordnen(umsatz, regeln)
        if einordnung.regel is None:
            if einordnen_posten(umsatz, _einordnen_hinweis(umsatz, einordnung), lauf=lauf):
                ergebnis.arbeitsposten += 1
            continue
        try:
            with transaction.atomic():
                nach_regel_buchen(umsatz, einordnung.regel, lauf=lauf)
        except KassenbuchFehler as fehler:
            ergebnis.fehler += 1
            logger.warning("Umsatz #%s: Regel #%s nicht angewandt", umsatz.pk, einordnung.regel.pk)
            if einordnen_posten(
                umsatz,
                f"Umsatz #{umsatz.pk}: Regel #{einordnung.regel.pk} ließ sich nicht anwenden "
                f"({fehler}).",
                lauf=lauf,
            ):
                ergebnis.arbeitsposten += 1
            continue
        ergebnis.gebucht += 1
        ergebnis.gebuchte_umsaetze.append(umsatz.pk)
    return ergebnis


# --- Abgleiche nach dem Einordnen (Erweiterungsstelle) --------------------------------


@dataclass(frozen=True)
class Abgleich:
    """Ergebnis eines Abgleichs nach dem Einordnen, nur Zähler (für das Laufprotokoll)."""

    name: str
    verbunden: int = 0
    arbeitsposten: int = 0
    fehler: int = 0


#: Läuft im Lauf ``kassenbuch_einordnen`` nach dem Einordnen, etwa der Abgleich der
#: Eingangsrechnungen mit den Abbuchungen (Dokumente, Schritt 3b), später der Abgleich mit
#: den Sollposten (Schritt 6). Bekommt den Lauf, liefert ein ``Abgleich``.
AbgleichFunktion = Callable[[Lauf | None], Abgleich]
_ABGLEICHE: dict[str, AbgleichFunktion] = {}


def abgleich_anmelden(name: str, funktion: AbgleichFunktion) -> None:
    _ABGLEICHE[name] = funktion


def abgleiche_ausfuehren(*, lauf: Lauf | None = None) -> list[Abgleich]:
    """Alle angemeldeten Abgleiche, nach Namen sortiert."""
    return [funktion(lauf) for _, funktion in sorted(_ABGLEICHE.items())]


# --- Rückwirkende Regel mit Vorschau -------------------------------------------------


@dataclass
class RegelVorschau:
    """Was ``regel_rueckwirkend_anwenden`` täte: Zähler zuerst, dann die Umsätze."""

    regel: Zuordnungsregel
    zu_buchen: list[Bankumsatz] = field(default_factory=list)
    #: passt, aber gleichauf mit einer anderen Regel: bleibt in der Arbeitsliste
    mehrdeutig: list[Bankumsatz] = field(default_factory=list)
    #: passt, aber eine Regel mit höherer Priorität gewinnt
    andere_regel: list[Bankumsatz] = field(default_factory=list)
    #: zu buchen, aber der Monat ist gesperrt: Buchung im ersten offenen Monat
    im_offenen_monat: int = 0


def regel_vorschau(regel: Zuordnungsregel) -> RegelVorschau:
    """Prüft eine Regel gegen alle offenen Umsätze (ohne gültige Buchung), ohne zu buchen."""
    vorschau = RegelVorschau(regel=regel)
    if not regel.aktiv or not regel.kategorie.aktiv:
        return vorschau
    regeln = aktive_regeln()
    offene = (
        umsaetze_mit_stand()
        .filter(Q(gebucht=0))
        .select_related("konto")
        .order_by("buchungsdatum", "pk")
    )
    for umsatz in offene.iterator(chunk_size=500):
        if not regel_passt(regel, umsatz):
            continue
        einordnung = einordnen(umsatz, regeln)
        if einordnung.regel is not None and einordnung.regel.pk == regel.pk:
            vorschau.zu_buchen.append(umsatz)
            if ist_gesperrt(umsatz.konto.mandant_id, umsatz.buchungsdatum):
                vorschau.im_offenen_monat += 1
        elif einordnung.mehrdeutig:
            vorschau.mehrdeutig.append(umsatz)
        else:
            vorschau.andere_regel.append(umsatz)
    return vorschau


@transaction.atomic
def regel_rueckwirkend_anwenden(
    regel: Zuordnungsregel, *, erwartet: int | None = None
) -> list[Bankumsatz]:
    """Bucht die offenen Umsätze, bei denen die Regel eindeutig gewinnt (nach der Vorschau).

    ``erwartet`` ist die Zahl aus der Vorschau; weicht sie ab, bucht die Funktion nichts.
    """
    vorschau = regel_vorschau(regel)
    if erwartet is not None and len(vorschau.zu_buchen) != erwartet:
        raise VorschauVeraltet(
            f"Regel #{regel.pk}: Die Vorschau nannte {erwartet} Umsätze, jetzt sind es "
            f"{len(vorschau.zu_buchen)}. Bitte die Vorschau neu ansehen."
        )
    for umsatz in vorschau.zu_buchen:
        nach_regel_buchen(umsatz, regel)
    return vorschau.zu_buchen


# --- Monatsabschluss (H6.6) ----------------------------------------------------------


def _bewegung(konto: Bankkonto, nach: datetime.date, bis: datetime.date) -> Decimal:
    """Summe der Umsätze mit Vorzeichen, Buchungstag nach ``nach`` bis einschließlich ``bis``."""
    summe = konto.umsaetze.filter(buchungsdatum__gt=nach, buchungsdatum__lte=bis).aggregate(
        s=auswertung.vorzeichen_summe()
    )["s"]
    return summe or NULL


@dataclass(frozen=True)
class BankSaldo:
    tag: datetime.date
    saldo: Decimal
    #: Kontostand der Bank, aus dem der Saldo stammt
    kontostand: Kontostand

    @property
    def genau(self) -> bool:
        """Meldet die Bank den Saldo für genau diesen Tag?"""
        return self.kontostand.datum == self.tag


def bank_saldo_am(konto: Bankkonto, datum: datetime.date) -> BankSaldo | None:
    """Saldo eines Kontos zum Ende eines Tages aus ``bank.Kontostand``.

    Gibt es für den Tag keinen Kontostand, rechnet die Funktion vom nächsten späteren
    Kontostand (sonst vom letzten früheren) mit den Umsätzen dazwischen zurück.
    """
    spaeter = konto.kontostaende.filter(datum__gte=datum).order_by("datum").first()
    if spaeter is not None:
        saldo = spaeter.saldo - _bewegung(konto, datum, spaeter.datum)
        return BankSaldo(tag=datum, saldo=saldo, kontostand=spaeter)
    frueher = konto.kontostaende.filter(datum__lt=datum).order_by("-datum").first()
    if frueher is not None:
        saldo = frueher.saldo + _bewegung(konto, frueher.datum, datum)
        return BankSaldo(tag=datum, saldo=saldo, kontostand=frueher)
    return None


@dataclass
class AbschlussPruefung:
    """Was einem Monatsabschluss fehlt. Leer heißt: Der Monat lässt sich abschließen."""

    mandant: Mandant
    monat: datetime.date
    beginn: datetime.date
    vormonat_offen: bool = False
    offene_umsaetze: list[int] = field(default_factory=list)
    konten_ohne_saldo: list[int] = field(default_factory=list)
    saldo_bank: Decimal | None = None
    saldo_buch: Decimal | None = None
    #: Buchungen im Monat, denen ein Pflichtbeleg fehlt. Nur ein Hinweis: zählt nicht für ``ok``.
    ohne_beleg: list[int] = field(default_factory=list)

    @property
    def saldo_stimmt(self) -> bool:
        return self.saldo_bank is not None and self.saldo_bank == self.saldo_buch

    @property
    def ok(self) -> bool:
        return (
            not self.vormonat_offen
            and not self.offene_umsaetze
            and not self.konten_ohne_saldo
            and self.saldo_stimmt
        )

    def hinweis(self) -> str:
        """Die Lücken, nur mit IDs und Zählern."""
        teile = []
        if self.vormonat_offen:
            teile.append(f"Der Vormonat {vormonat(self.monat):%m/%Y} ist noch offen.")
        if self.offene_umsaetze:
            ids = ", ".join(f"#{i}" for i in self.offene_umsaetze[:10])
            mehr = " …" if len(self.offene_umsaetze) > 10 else ""
            teile.append(
                f"{len(self.offene_umsaetze)} Umsätze sind nicht vollständig eingeordnet "
                f"({ids}{mehr})."
            )
        if self.konten_ohne_saldo:
            ids = ", ".join(f"#{i}" for i in self.konten_ohne_saldo)
            teile.append(f"Für Konto {ids} gibt es keinen Kontostand der Bank.")
        elif not self.saldo_stimmt:
            teile.append("Der Saldo im Buch passt nicht zum Saldo der Bank.")
        if self.ohne_beleg:
            teile.append(self.beleg_hinweis())
        return f"Monatsabschluss {self.monat:%m/%Y} für Mandant #{self.mandant.pk}: " + " ".join(
            teile
        )

    def beleg_hinweis(self) -> str:
        ids = ", ".join(f"#{i}" for i in self.ohne_beleg[:10])
        mehr = " …" if len(self.ohne_beleg) > 10 else ""
        return (
            f"Hinweis: {len(self.ohne_beleg)} Buchungen ohne Beleg ({ids}{mehr}); "
            "der Abschluss geht trotzdem."
        )


# --- Belege im Abschluss (Erweiterungsstelle für Schritt 3) ---------------------------

#: Liefert zu Buchungen die IDs derer, denen ein Pflichtbeleg fehlt. Die Dokumente melden
#: die Funktion beim Start an (``apps.dokumente.apps``); welche Buchungen einen Beleg
#: brauchen, steht in ``DOKUMENTE["BELEGPFLICHT"]``. Ohne Anmeldung fehlt nie ein Beleg.
BelegPruefung = Callable[["models.QuerySet[Buchung]"], list[int]]
_BELEGPRUEFUNG: list[BelegPruefung] = []


def belegpruefung_anmelden(funktion: BelegPruefung) -> None:
    _BELEGPRUEFUNG[:] = [funktion]


def buchungen_ohne_beleg(buchungen: models.QuerySet[Buchung]) -> list[int]:
    """IDs der gültigen Buchungen, denen ein Pflichtbeleg fehlt (sortiert)."""
    if not _BELEGPRUEFUNG:
        return []
    gueltig = buchungen.filter(storno_von__isnull=True, storniert_durch__isnull=True)
    return sorted(_BELEGPRUEFUNG[0](gueltig))


def mandanten_mit_konten() -> list[Mandant]:
    return list(Mandant.objects.filter(bankkonten__isnull=False).distinct().order_by("kuerzel"))


def _beginn(mandant: Mandant, monat: datetime.date) -> datetime.date:
    """Erster Monat des Kassenbuchs für den Mandanten: sein frühester Abschluss(versuch)."""
    erster = Monatsabschluss.objects.filter(mandant=mandant).aggregate(m=Min("monat"))["m"]
    return min(erster, monat) if erster is not None else monat


def abschluss_pruefen(mandant: Mandant, monat: datetime.date) -> AbschlussPruefung:
    """Prüft einen Monat (H6.6): Vormonat abgeschlossen, alle Umsätze seit Beginn des
    Kassenbuchs vollständig gebucht, Saldo im Buch gleich Saldo der Bank.

    Saldo Buch = Saldo der Bank zum Ende des Vormonats + Buchungen mit Datum im Monat.
    Buchungen ohne Pflichtbeleg (Schritt 3) stehen in ``ohne_beleg``; sie blockieren nicht.
    """
    monat = monatsanfang(monat)
    ende = monatsende(monat)
    beginn = _beginn(mandant, monat)
    pruefung = AbschlussPruefung(mandant=mandant, monat=monat, beginn=beginn)
    if monat > beginn:
        pruefung.vormonat_offen = not Monatsabschluss.objects.filter(
            mandant=mandant, monat=vormonat(monat), abgeschlossen_am__isnull=False
        ).exists()

    konten = list(Bankkonto.objects.filter(mandant=mandant).order_by("pk"))
    pruefung.offene_umsaetze = list(
        nicht_gebuchte_umsaetze()
        .filter(konto__in=konten, buchungsdatum__gte=beginn, buchungsdatum__lte=ende)
        .order_by("buchungsdatum", "pk")
        .values_list("pk", flat=True)
    )
    saldo_bank = NULL
    saldo_vormonat = NULL
    for konto in konten:
        am_ende = bank_saldo_am(konto, ende)
        davor = bank_saldo_am(konto, monat - datetime.timedelta(days=1))
        if am_ende is None or davor is None:
            pruefung.konten_ohne_saldo.append(konto.pk)
            continue
        saldo_bank += am_ende.saldo
        saldo_vormonat += davor.saldo
    bewegung = (
        Buchung.objects.filter(
            umsatz__konto__in=konten, datum__gte=monat, datum__lte=ende
        ).aggregate(s=auswertung.vorzeichen_summe())["s"]
        or NULL
    )
    if not pruefung.konten_ohne_saldo:
        pruefung.saldo_bank = saldo_bank
        pruefung.saldo_buch = saldo_vormonat + bewegung
    pruefung.ohne_beleg = buchungen_ohne_beleg(
        Buchung.objects.filter(umsatz__konto__in=konten, datum__gte=monat, datum__lte=ende)
    )
    return pruefung


@dataclass
class AbschlussErgebnis:
    abschluss: Monatsabschluss
    pruefung: AbschlussPruefung | None
    neu_abgeschlossen: bool = False
    posten: Arbeitsposten | None = None
    #: neuer Arbeitsposten „Belege fehlen“ (nur ein Hinweis, der Abschluss geht trotzdem)
    belege_posten: Arbeitsposten | None = None


def _belege_hinweis(abschluss: Monatsabschluss, pruefung: AbschlussPruefung) -> str:
    return (
        f"Monatsabschluss {abschluss.monat:%m/%Y} für Mandant #{abschluss.mandant_id}: "
        + pruefung.beleg_hinweis()
    )


def belege_posten_abgleichen(
    abschluss: Monatsabschluss, pruefung: AbschlussPruefung, *, lauf: Lauf | None = None
) -> Arbeitsposten | None:
    """Ein offener Posten „Belege fehlen“ je Abschluss, solange Belege fehlen; sonst erledigt.

    Rückgabe: der neu angelegte Posten (oder None).
    """
    offen = _posten_zu(ART_BELEGE_FEHLEN, abschluss, offen=True)
    if not pruefung.ohne_beleg:
        for posten in offen:
            kern_services.arbeitsposten_erledigen(posten)
        return None
    if offen:
        return None
    return kern_services.arbeitsposten_anlegen(
        ART_BELEGE_FEHLEN,
        bezug=abschluss,
        aktionen=AKTIONEN_BELEGE_FEHLEN,
        faellig_am=zeit.heute(),
        hinweis=_belege_hinweis(abschluss, pruefung),
        lauf=lauf,
    )


def belege_erneut_pruefen(abschluss: Monatsabschluss) -> AbschlussPruefung:
    """Aktion „Belege prüfen“: prüft die Belege eines (auch abgeschlossenen) Monats erneut."""
    pruefung = abschluss_pruefen(abschluss.mandant, abschluss.monat)
    belege_posten_abgleichen(abschluss, pruefung)
    return pruefung


@als_vorgang("Monatsabschluss {monat:%m/%Y} {mandant.kuerzel}")
@transaction.atomic
def monat_abschliessen(
    mandant: Mandant, monat: datetime.date, *, lauf: Lauf | None = None
) -> AbschlussErgebnis:
    """Schließt einen Monat für einen Mandanten ab oder legt die Lücken in die Arbeitsliste.

    Idempotent: Ein abgeschlossener Monat bleibt, wie er ist; je Monat und Mandant gibt es
    höchstens einen offenen Posten „Monatsabschluss offen“.
    """
    monat = monatsanfang(monat)
    abschluss = (
        Monatsabschluss.objects.select_for_update().filter(mandant=mandant, monat=monat).first()
    )
    if abschluss is not None and abschluss.abgeschlossen:
        return AbschlussErgebnis(abschluss=abschluss, pruefung=None)
    if abschluss is None:
        abschluss = Monatsabschluss(mandant=mandant, monat=monat, lauf=lauf)
        abschluss.full_clean()
        abschluss.save()

    pruefung = abschluss_pruefen(mandant, monat)
    abschluss.saldo_bank = pruefung.saldo_bank
    abschluss.saldo_buch = pruefung.saldo_buch
    abschluss.lauf = lauf or abschluss.lauf
    if pruefung.ok:
        abschluss.abgeschlossen_am = zeit.jetzt()
        abschluss.save()
        for erledigt in _posten_zu(ART_ABSCHLUSS, abschluss, offen=True):
            kern_services.arbeitsposten_erledigen(erledigt)
        logger.info("Monatsabschluss #%s: abgeschlossen", abschluss.pk)
        return AbschlussErgebnis(
            abschluss=abschluss,
            pruefung=pruefung,
            neu_abgeschlossen=True,
            belege_posten=belege_posten_abgleichen(abschluss, pruefung, lauf=lauf),
        )

    abschluss.save()
    belege_posten = belege_posten_abgleichen(abschluss, pruefung, lauf=lauf)
    posten = None
    if not _posten_zu(ART_ABSCHLUSS, abschluss, offen=True):
        posten = kern_services.arbeitsposten_anlegen(
            ART_ABSCHLUSS,
            bezug=abschluss,
            aktionen=AKTIONEN_ABSCHLUSS,
            faellig_am=zeit.heute(),
            hinweis=pruefung.hinweis(),
            lauf=lauf,
        )
    return AbschlussErgebnis(
        abschluss=abschluss, pruefung=pruefung, posten=posten, belege_posten=belege_posten
    )


def naechster_abschlussmonat(mandant: Mandant, ab: datetime.date | None) -> datetime.date | None:
    """Der Monat, den der nächste Abschluss für den Mandanten prüft (oder None ohne Umsätze)."""
    letzter = Monatsabschluss.objects.filter(
        mandant=mandant, abgeschlossen_am__isnull=False
    ).aggregate(m=Max("monat"))["m"]
    if letzter is not None:
        return naechster_monat(letzter)
    versucht: datetime.date | None = Monatsabschluss.objects.filter(mandant=mandant).aggregate(
        m=Min("monat")
    )["m"]
    if versucht is not None:
        return versucht
    if ab is not None:
        return monatsanfang(ab)
    erster = Bankumsatz.objects.filter(konto__mandant=mandant).aggregate(d=Min("buchungsdatum"))[
        "d"
    ]
    return monatsanfang(erster) if erster is not None else None


@dataclass
class AbschlussLauf:
    abgeschlossen: list[Monatsabschluss] = field(default_factory=list)
    offen: list[AbschlussErgebnis] = field(default_factory=list)
    berichte: list[Monatsbericht] = field(default_factory=list)
    berichte_ohne_pdf: list[Monatsbericht] = field(default_factory=list)
    arbeitsposten: int = 0


def monatsabschluesse_ausfuehren(
    bis: datetime.date, *, ab: datetime.date | None = None, lauf: Lauf | None = None
) -> AbschlussLauf:
    """Schließt je Mandant mit Bankkonten die offenen Monate bis ``bis`` der Reihe nach ab.

    Der erste Monat ist der nach dem letzten Abschluss; gibt es noch keinen, ``ab`` oder der
    Monat des ersten Umsatzes. Scheitert ein Monat, bleiben die späteren offen. Ist ein Monat
    danach für alle Mandanten abgeschlossen, entstehen die Monatsberichte je Objekt.
    """
    bis = monatsanfang(bis)
    ergebnis = AbschlussLauf()
    for mandant in mandanten_mit_konten():
        monat = naechster_abschlussmonat(mandant, ab)
        while monat is not None and monat <= bis:
            schritt = monat_abschliessen(mandant, monat, lauf=lauf)
            ergebnis.arbeitsposten += 1 if schritt.belege_posten is not None else 0
            if schritt.neu_abgeschlossen:
                ergebnis.abgeschlossen.append(schritt.abschluss)
            elif not schritt.abschluss.abgeschlossen:
                ergebnis.offen.append(schritt)
                ergebnis.arbeitsposten += 1 if schritt.posten is not None else 0
                break
            monat = naechster_monat(monat)

    for monat in sorted({a.monat for a in ergebnis.abgeschlossen}):
        if auswertung.monat_abgeschlossen(monat):
            for bericht in monatsberichte_erzeugen(monat, lauf=lauf):
                ergebnis.berichte.append(bericht)
                if not bericht.datei:
                    ergebnis.berichte_ohne_pdf.append(bericht)
    return ergebnis


# --- Monatsbericht je Objekt (H10.2) -------------------------------------------------


def bericht_html(daten: auswertung.Bericht) -> str:
    return render_to_string("kassenbuch/bericht_pdf.html", {"bericht": daten})


def monatsbericht_erzeugen(
    objekt: Objekt, monat: datetime.date, *, lauf: Lauf | None = None
) -> Monatsbericht:
    """Hält die Zahlen fest und legt das PDF ab (Name aus Objektkürzel und Monat).

    Fehlt WeasyPrint (etwa unter macOS ohne Pango), entsteht der Bericht ohne PDF; die Seite
    zeigt ihn trotzdem, und ein späterer Aufruf ergänzt das PDF.
    """
    monat = monatsanfang(monat)
    daten = auswertung.bericht(objekt, monat)
    bericht = Monatsbericht.objects.filter(objekt=objekt, monat=monat).first() or Monatsbericht(
        objekt=objekt, monat=monat
    )
    bericht.einnahmen = daten.summe_einnahmen
    bericht.kosten = daten.summe_kosten
    bericht.ergebnis = daten.ergebnis
    bericht.entnahmen = daten.summe_entnahmen
    bericht.lauf = lauf or bericht.lauf
    try:
        inhalt = pdf.html_zu_pdf(bericht_html(daten))
    except pdf.PdfNichtVerfuegbar:
        logger.warning("Monatsbericht Objekt #%s %s: kein PDF", objekt.pk, f"{monat:%m/%Y}")
        bericht.save()
        return bericht
    if bericht.datei:
        bericht.datei.delete(save=False)
    # Je Objekt und Monat gibt es genau ein PDF: Eine alte Datei gleichen Namens (etwa aus
    # einem gelöschten Testhof) wird ersetzt, statt einen Namen mit Zufallsendung zu bekommen.
    ziel = bericht.datei.field.generate_filename(bericht, daten.dateiname)
    if bericht.datei.storage.exists(ziel):
        bericht.datei.storage.delete(ziel)
    bericht.datei.save(daten.dateiname, ContentFile(inhalt), save=False)
    bericht.save()
    return bericht


def berichtsobjekte(monat: datetime.date) -> list[Objekt]:
    """Aktive Objekte und alle mit Buchungen im Monat."""
    mit_buchungen = Buchung.objects.filter(datum__gte=monat, datum__lte=monatsende(monat)).values(
        "objekt_id"
    )
    return list(Objekt.objects.filter(Q(aktiv=True) | Q(pk__in=mit_buchungen)).order_by("kuerzel"))


def monatsberichte_erzeugen(
    monat: datetime.date, *, lauf: Lauf | None = None
) -> list[Monatsbericht]:
    monat = monatsanfang(monat)
    return [monatsbericht_erzeugen(o, monat, lauf=lauf) for o in berichtsobjekte(monat)]
