"""Schreibweg der Anbindungen (CLAUDE.md, Regel 4.1): Website und Verwaltio (Schritt 10).

- **Katalog (W1):** ``katalog()`` liest die buchbaren Einheiten mit Preis, frei ab und
  Bildern, ohne Parteien. Eine Rückgabe (Status „frei“ ab dem Rückgabetag) macht die
  Einheit ohne Handgriff wieder buchbar.
- **Ereignisse (W10):** ``ereignis_annehmen()`` legt für Buchung oder Kündigung eine
  ``Buchungsanfrage`` an der Einheit an, dazu den Arbeitsposten „Buchungsanfrage prüfen“.
  Idempotent über die Ereignis-ID der Website.
- **Kennzahlen (V12):** ``kennzahlen_abrufen()`` holt je Wohneinheit offene Posten,
  Mahnfälle, Stand der NKA und Fristen aus Verwaltio und speichert sie je Tag.
- **ID-Abgleich (H1.6):** ``id_abgleich()`` vergleicht die NW-IDs von Website und Verwaltio
  mit dem Hof-System; jede fehlende oder abweichende ID wird ``Abweichung`` und
  Arbeitsposten „ID-Abweichung“ und schließt sich, sobald sie behoben ist.
- **Cockpit:** ``wohnraum_ueberblick()`` liefert die Zahlen der Kachel „Wohnraum“.

Fehlermeldungen nennen IDs (NW-ID, Ereignis-ID, #pk), nie Namen oder Kontaktdaten.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import secrets
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import PurePath

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.files import File
from django.db import IntegrityError, models, transaction
from django.db.models import Max, Prefetch, Q, Sum
from django.utils import timezone

from apps.anbindungen.models import (
    Abweichung,
    Buchungsanfrage,
    Einheitbild,
    Kennzahl,
    Websitezugang,
)
from apps.anbindungen.verwaltio import VerwaltioClient, VerwaltioFehler
from apps.anbindungen.website import WebsiteClient, WebsiteEinheit
from apps.kern import services as kern
from apps.kern.models import Arbeitsposten, Lauf
from apps.kern.vorgaenge import als_vorgang
from apps.stammdaten import services as stammdaten
from apps.stammdaten.models import (
    Einheit,
    EinheitStatus,
    Kontaktweg,
    Kuendigung,
    Vertrag,
    Vertragspartei,
)

logger = logging.getLogger(__name__)

#: Arten der Arbeitsposten; beide melden sich über ``HOF_BENACHRICHTIGUNG_ARTEN``.
ART_BUCHUNGSANFRAGE = "Buchungsanfrage prüfen"
ART_ID_ABWEICHUNG = "ID-Abweichung"

#: Aktion an „Buchungsanfrage prüfen“: führt zur Seite der Anfrage (apps.anbindungen.aktionen).
AKTION_ENTSCHEIDEN = "buchungsanfrage_entscheiden"

TOKEN_PRAEFIX = "hofw_"  # noqa: S105  (Präfix, kein Geheimnis)

#: Tage bis „Buchungsanfrage prüfen“ fällig ist.
TAGE_BIS_PRUEFUNG = 2

#: Verträge in diesen Status binden ihre Einheiten (wie ``stammdaten.services``).
BINDENDE_STATUS = (Vertrag.Status.ENTWURF, Vertrag.Status.AKTIV, Vertrag.Status.GEKUENDIGT)

BILD_ENDUNGEN = (".jpg", ".jpeg", ".png", ".webp")


class AnbindungFehler(ValueError):
    """Eine Änderung ist so nicht möglich. Die Meldung nennt nur IDs."""


class EreignisUngueltig(AnbindungFehler):
    """Die Meldung der Website passt nicht (Feld fehlt, Einheit unbekannt …)."""


class EreignisKonflikt(AnbindungFehler):
    """Dieselbe Ereignis-ID kam schon mit anderem Inhalt."""


def _speichern(objekt: models.Model) -> None:
    try:
        objekt.full_clean()
    except ValidationError as fehler:
        felder = ", ".join(sorted(fehler.message_dict)) if hasattr(fehler, "error_dict") else ""
        raise AnbindungFehler(
            f"{type(objekt)._meta.verbose_name} ungültig ({felder or 'Wert ungültig'})"
        ) from fehler
    objekt.save()


def _posten_zu(art: str, bezug: models.Model) -> models.QuerySet[Arbeitsposten]:
    return Arbeitsposten.objects.filter(
        art=art, bezug_typ=ContentType.objects.get_for_model(bezug), bezug_id=bezug.pk
    )


def _posten_schliessen(art: str, bezug: models.Model) -> int:
    """Erledigt offene und zurückgestellte Posten einer Art an einem Bezug."""
    anzahl = 0
    for posten in _posten_zu(art, bezug).exclude(status=Arbeitsposten.Status.ERLEDIGT):
        kern.arbeitsposten_erledigen(posten)
        anzahl += 1
    return anzahl


# --- Token der Website ---------------------------------------------------------------


def _token_hash(token: str) -> str:
    # Der Token hat 256 Bit Zufall; ein einfacher SHA-256 genügt (kein Passwort).
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def website_token_erzeugen(bezeichnung: str = "Website", *, ersetzen: bool = True) -> str:
    """Erzeugt einen Token für die Website; zurück kommt er genau einmal.

    Gespeichert wird nur sein Hash. Mit ``ersetzen`` (Standard) gelten frühere Tokens danach
    nicht mehr; ohne bleiben sie gültig (Wechsel ohne Ausfall).
    """
    token = TOKEN_PRAEFIX + secrets.token_urlsafe(32)
    with transaction.atomic():
        if ersetzen:
            for alt in Websitezugang.objects.filter(aktiv=True):
                alt.aktiv = False
                alt.save(update_fields=["aktiv"])
        zugang = Websitezugang(bezeichnung=bezeichnung, token_hash=_token_hash(token))
        _speichern(zugang)
    logger.info("Neuer Website-Token: Zugang #%s", zugang.pk)
    return token


def website_token_sperren() -> int:
    """Sperrt alle Tokens der Website. Ergebnis: Anzahl."""
    anzahl = 0
    for zugang in Websitezugang.objects.filter(aktiv=True):
        zugang.aktiv = False
        zugang.save(update_fields=["aktiv"])
        anzahl += 1
    return anzahl


def zugang_zum_token(token: str) -> Websitezugang | None:
    """Der aktive Zugang zu einem Token, sonst None."""
    if not token.startswith(TOKEN_PRAEFIX):
        return None
    return Websitezugang.objects.filter(token_hash=_token_hash(token), aktiv=True).first()


# --- Katalog (W1) --------------------------------------------------------------------


@dataclass(frozen=True)
class KatalogBild:
    bild_id: int
    beschreibung: str
    reihenfolge: int


@dataclass(frozen=True)
class KatalogEintrag:
    """Eine buchbare Einheit, wie die Website sie zeigt. Ohne Parteien und Verträge."""

    nw_id: str
    typ: str
    typ_titel: str
    objekt: str
    bezeichnung: str
    flaeche_qm: Decimal | None
    preis_monat: Decimal | None
    frei_ab: datetime.date
    slug: str
    bilder: tuple[KatalogBild, ...] = field(default_factory=tuple)


def vermarktbare_einheiten(stichtag: datetime.date) -> models.QuerySet[Einheit]:
    """Einheiten, die das Hof-System auf der Website vermarktet (ohne Wohnraum)."""
    return (
        Einheit.objects.filter(vermarktung=Einheit.Vermarktung.WEBSITE)
        .exclude(typ=Einheit.Typ.WOHNUNG)
        .filter(Q(aufgeloest_am__isnull=True) | Q(aufgeloest_am__gt=stichtag))
    )


def _frei_ab(staende: Sequence[EinheitStatus], stichtag: datetime.date) -> datetime.date | None:
    """Ab wann die Einheit laut Status frei ist: heute, wenn sie heute frei ist, sonst der
    Beginn des nächsten Stands, wenn der „frei“ ist (etwa nach einer Rückgabe)."""
    for stand in staende:
        if stand.gilt_am(stichtag):
            if stand.status == EinheitStatus.Status.FREI:
                return stichtag
            break
    spaeter = [s for s in staende if s.gueltig_ab > stichtag]
    if spaeter and spaeter[0].status == EinheitStatus.Status.FREI:
        return spaeter[0].gueltig_ab
    return None


def buchbar_ab(einheit: Einheit, stichtag: datetime.date) -> datetime.date | None:
    """Ab wann eine Einheit buchbar ist, sonst None.

    Buchbar ist sie, wenn sie (ab einem Tag) den Status „frei“ hat, kein Vertrag (Entwurf,
    aktiv, gekündigt) sie ab da bindet und keine angenommene Buchungsanfrage auf ihr liegt.
    Nutzt vorgeladene Listen aus ``katalog()``, sonst fragt sie die Datenbank.
    """
    staende: list[EinheitStatus] = getattr(einheit, "katalog_staende", None) or list(
        einheit.statusstaende.order_by("gueltig_ab")
    )
    ab = _frei_ab(staende, stichtag)
    if ab is None:
        return None
    vertraege: list[Vertrag] = getattr(einheit, "katalog_vertraege", None)  # type: ignore[assignment]
    if vertraege is None:
        vertraege = list(einheit.vertraege.filter(status__in=BINDENDE_STATUS))
    if any(v.ende is None or v.ende >= ab for v in vertraege):
        return None
    angenommen = getattr(einheit, "katalog_angenommen", None)
    if angenommen is None:
        angenommen = list(
            einheit.buchungsanfragen.filter(
                art=Buchungsanfrage.Art.BUCHUNG, status=Buchungsanfrage.Status.ANGENOMMEN
            )
        )
    if angenommen:
        return None
    return ab


def katalog(stichtag: datetime.date | None = None) -> list[KatalogEintrag]:
    """Alle buchbaren, vermarkteten Einheiten mit Typ, Fläche, Preis, frei ab und Bildern.

    Preis ist die Zielmiete je Monat am Tag „frei ab“ (leer, wenn keine gepflegt ist).
    Sortiert nach NW-ID.
    """
    stichtag = stichtag or timezone.localdate()
    einheiten = (
        vermarktbare_einheiten(stichtag)
        .select_related("objekt")
        .prefetch_related(
            Prefetch(
                "statusstaende",
                queryset=EinheitStatus.objects.order_by("gueltig_ab"),
                to_attr="katalog_staende",
            ),
            Prefetch(
                "vertraege",
                queryset=Vertrag.objects.filter(status__in=BINDENDE_STATUS),
                to_attr="katalog_vertraege",
            ),
            Prefetch(
                "buchungsanfragen",
                queryset=Buchungsanfrage.objects.filter(
                    art=Buchungsanfrage.Art.BUCHUNG, status=Buchungsanfrage.Status.ANGENOMMEN
                ),
                to_attr="katalog_angenommen",
            ),
            Prefetch(
                "bilder",
                queryset=Einheitbild.objects.filter(aktiv=True).order_by("reihenfolge", "pk"),
                to_attr="katalog_bilder",
            ),
            "zielmieten",
        )
        .order_by("nw_id")
    )
    ergebnis = []
    for einheit in einheiten:
        ab = buchbar_ab(einheit, stichtag)
        if ab is None:
            continue
        zielmiete = next((z for z in einheit.zielmieten.all() if z.gilt_am(ab)), None)
        bilder: list[Einheitbild] = getattr(einheit, "katalog_bilder", [])
        ergebnis.append(
            KatalogEintrag(
                nw_id=einheit.nw_id,
                typ=einheit.typ,
                typ_titel=einheit.get_typ_display(),
                objekt=einheit.objekt.kuerzel,
                bezeichnung=einheit.bezeichnung,
                flaeche_qm=einheit.flaeche_qm,
                preis_monat=zielmiete.betrag if zielmiete is not None else None,
                frei_ab=ab,
                slug=einheit.website_slug,
                bilder=tuple(
                    KatalogBild(bild_id=b.pk, beschreibung=b.beschreibung, reihenfolge=n)
                    for n, b in enumerate(bilder, start=1)
                ),
            )
        )
    return ergebnis


# --- Vermarktungsbilder --------------------------------------------------------------


def einheitbild_hinzufuegen(einheit: Einheit, datei: File, beschreibung: str = "") -> Einheitbild:  # type: ignore[type-arg]
    """Hängt ein Vermarktungsbild an eine Einheit (JPEG, PNG oder WebP), hinten an."""
    if einheit.ist_wohnraum:
        raise AnbindungFehler(f"Einheit {einheit.nw_id} ist Wohnraum (geführt in Verwaltio).")
    endung = PurePath(datei.name or "").suffix.lower()
    if endung not in BILD_ENDUNGEN:
        raise AnbindungFehler(f"Bild für {einheit.nw_id}: nur {', '.join(BILD_ENDUNGEN)}.")
    letzte = einheit.bilder.filter(aktiv=True).aggregate(m=Max("reihenfolge"))["m"]
    bild = Einheitbild(
        einheit=einheit,
        bild=datei,
        beschreibung=beschreibung,
        reihenfolge=(letzte or 0) + 1,
    )
    _speichern(bild)
    return bild


def einheitbild_entfernen(bild: Einheitbild) -> Einheitbild:
    """Nimmt ein Bild aus dem Katalog. Die Datei bleibt (stornieren statt löschen)."""
    bild.aktiv = False
    bild.save(update_fields=["aktiv"])
    return bild


# --- Ereignisse der Website (W10) ----------------------------------------------------


@dataclass(frozen=True)
class WebsiteEreignis:
    """Eine Meldung der Website: Buchung oder Kündigung einer Einheit."""

    ereignis_id: str
    art: str
    nw_id: str
    gemeldet_am: datetime.datetime
    wunsch_ab: datetime.date | None = None
    vertragsnummer: str = ""
    kontakt_name: str = ""
    kontakt_email: str = ""
    kontakt_telefon: str = ""
    nachricht: str = ""

    def pruefsumme(self) -> str:
        daten = {
            k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in asdict(self).items()
        }
        roh = json.dumps(daten, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(roh).hexdigest()


def _vertrag_zur_nummer(einheit: Einheit, nummer: str) -> Vertrag | None:
    if not nummer:
        return None
    return einheit.vertraege.filter(nummer=nummer.strip()).first()


def ereignis_annehmen(ereignis: WebsiteEreignis) -> tuple[Buchungsanfrage, bool]:
    """Legt zur Meldung eine Buchungsanfrage und den Arbeitsposten „Buchungsanfrage prüfen“ an.

    Idempotent: Kommt dieselbe Ereignis-ID noch einmal mit gleichem Inhalt, bleibt es bei
    der ersten Anfrage (Ergebnis ``(anfrage, False)``). Mit anderem Inhalt:
    ``EreignisKonflikt``.
    """
    if ereignis.art not in Buchungsanfrage.Art.values:
        raise EreignisUngueltig("art: unbekannt (buchung oder kuendigung)")
    pruefsumme = ereignis.pruefsumme()
    vorhanden = Buchungsanfrage.objects.filter(ereignis_id=ereignis.ereignis_id).first()
    if vorhanden is not None:
        if vorhanden.pruefsumme != pruefsumme:
            raise EreignisKonflikt(f"Ereignis {ereignis.ereignis_id} kam schon mit anderem Inhalt.")
        return vorhanden, False

    einheit = Einheit.objects.filter(nw_id=ereignis.nw_id).first()
    if einheit is None:
        raise EreignisUngueltig("nw_id: Einheit im Hof-System unbekannt")
    if einheit.ist_wohnraum:
        raise EreignisUngueltig("nw_id: Wohnraum, geführt in Verwaltio")
    if einheit.aufgeloest_am is not None:
        raise EreignisUngueltig("nw_id: Einheit aufgelöst")

    anfrage = Buchungsanfrage(
        ereignis_id=ereignis.ereignis_id,
        pruefsumme=pruefsumme,
        art=ereignis.art,
        einheit=einheit,
        gemeldet_am=ereignis.gemeldet_am,
        wunsch_ab=ereignis.wunsch_ab,
        vertragsnummer=ereignis.vertragsnummer.strip(),
        kontakt_name=ereignis.kontakt_name.strip(),
        kontakt_email=ereignis.kontakt_email.strip(),
        kontakt_telefon=ereignis.kontakt_telefon.strip(),
        nachricht=ereignis.nachricht.strip(),
        vertrag=(
            _vertrag_zur_nummer(einheit, ereignis.vertragsnummer)
            if ereignis.art == Buchungsanfrage.Art.KUENDIGUNG
            else None
        ),
    )
    try:
        with transaction.atomic():
            _speichern(anfrage)
            wunsch = f", gewünscht ab {ereignis.wunsch_ab:%d.%m.%Y}" if ereignis.wunsch_ab else ""
            kern.arbeitsposten_anlegen(
                ART_BUCHUNGSANFRAGE,
                bezug=anfrage,
                aktionen=(AKTION_ENTSCHEIDEN, "zurueckstellen"),
                faellig_am=timezone.localtime(ereignis.gemeldet_am).date()
                + datetime.timedelta(days=TAGE_BIS_PRUEFUNG),
                hinweis=f"{anfrage.get_art_display()} für Einheit {einheit.nw_id}{wunsch}",
            )
    except IntegrityError:
        # Gleichzeitig dieselbe Meldung: Die andere Anfrage war schneller.
        vorhanden = Buchungsanfrage.objects.filter(ereignis_id=ereignis.ereignis_id).first()
        if vorhanden is None or vorhanden.pruefsumme != pruefsumme:
            raise EreignisKonflikt(
                f"Ereignis {ereignis.ereignis_id} kam schon mit anderem Inhalt."
            ) from None
        return vorhanden, False
    logger.info(
        "Buchungsanfrage #%s (%s) für %s aus Ereignis %s",
        anfrage.pk,
        anfrage.art,
        einheit.nw_id,
        ereignis.ereignis_id,
    )
    return anfrage, True


#: Welche Entscheidungen bei welcher Art möglich sind.
ENTSCHEIDUNGEN: dict[str, tuple[str, ...]] = {
    Buchungsanfrage.Art.BUCHUNG: (
        Buchungsanfrage.Status.ANGENOMMEN,
        Buchungsanfrage.Status.ABGELEHNT,
        Buchungsanfrage.Status.VERTRAG_ANGELEGT,
    ),
    Buchungsanfrage.Art.KUENDIGUNG: (
        Buchungsanfrage.Status.ERLEDIGT,
        Buchungsanfrage.Status.ABGELEHNT,
    ),
}

#: Status, aus denen heraus sich noch entscheiden lässt.
NOCH_OFFEN = (Buchungsanfrage.Status.OFFEN, Buchungsanfrage.Status.ANGENOMMEN)


def entscheidungen(anfrage: Buchungsanfrage) -> tuple[str, ...]:
    """Die Entscheidungen, die eine Anfrage in ihrem Status anbietet."""
    if anfrage.status not in NOCH_OFFEN:
        return ()
    moeglich = ENTSCHEIDUNGEN[anfrage.art]
    return tuple(s for s in moeglich if s != anfrage.status)


def buchungsanfrage_entscheiden(
    anfrage: Buchungsanfrage,
    status: str,
    *,
    vertrag: Vertrag | None = None,
    kuendigung: Kuendigung | None = None,
    am: datetime.datetime | None = None,
) -> Buchungsanfrage:
    """Entscheidet über eine Anfrage: angenommen, abgelehnt, Vertrag angelegt (Buchung) oder
    erledigt (Kündigung erfasst).

    - „angenommen“ hält die Einheit aus dem Katalog, bis der Vertrag steht; der
      Arbeitsposten bleibt offen.
    - Jede andere Entscheidung schließt den Arbeitsposten.
    - „Vertrag angelegt“ braucht den Vertrag; er muss die Einheit der Anfrage umfassen.
    - „erledigt“ (Kündigung) braucht die erfasste Kündigung eines Vertrags dieser Einheit
      (Review 2, A3): Ohne sie ginge die Kündigung verloren.
    """
    if status not in entscheidungen(anfrage):
        raise AnbindungFehler(
            f"Buchungsanfrage #{anfrage.pk}: „{status}“ ist im Status „{anfrage.status}“ "
            "nicht möglich."
        )
    if anfrage.art == Buchungsanfrage.Art.KUENDIGUNG and status == Buchungsanfrage.Status.ERLEDIGT:
        if kuendigung is None:
            raise AnbindungFehler(
                f"Buchungsanfrage #{anfrage.pk}: Erledigt ist die Kündigung erst, wenn sie am "
                "Vertrag erfasst ist („Kündigung anlegen“)."
            )
        if not kuendigung.vertrag.vertragseinheiten.filter(einheit=anfrage.einheit).exists():
            raise AnbindungFehler(
                f"Kündigung #{kuendigung.pk} gehört nicht zu Einheit {anfrage.einheit.nw_id}."
            )
        anfrage.kuendigung = kuendigung
        anfrage.vertrag = kuendigung.vertrag
    if status == Buchungsanfrage.Status.VERTRAG_ANGELEGT:
        if vertrag is None:
            raise AnbindungFehler(f"Buchungsanfrage #{anfrage.pk}: Vertrag fehlt.")
        if not vertrag.vertragseinheiten.filter(einheit=anfrage.einheit).exists():
            raise AnbindungFehler(
                f"Vertrag {vertrag.nummer} umfasst Einheit {anfrage.einheit.nw_id} nicht."
            )
        anfrage.vertrag = vertrag
    with transaction.atomic():
        anfrage.status = status
        anfrage.entschieden_am = am or timezone.now()
        _speichern(anfrage)
        if status != Buchungsanfrage.Status.ANGENOMMEN:
            _posten_schliessen(ART_BUCHUNGSANFRAGE, anfrage)
    logger.info("Buchungsanfrage #%s: %s", anfrage.pk, status)
    return anfrage


@als_vorgang("{ergebnis.vorgang_text} aus Anfrage #{anfrage.pk} erfasst")
def kuendigung_aus_anfrage(
    anfrage: Buchungsanfrage,
    vertrag: Vertrag,
    zugang_am: datetime.date,
    *,
    von: str = Kuendigung.Von.MIETER,
    zum_wunsch: datetime.date | None = None,
    rueckgabetermin: datetime.date | None = None,
    zugangsweg: str = Kuendigung.Zugangsweg.WEBSITE,
    aufhebung: bool = False,
) -> Kuendigung:
    """Erfasst die Kündigung einer Website-Anfrage am Vertrag und erledigt die Anfrage
    (Review 2, A3). Beides in einer Transaktion: Scheitert die Kündigung, bleibt die Anfrage
    offen. Fristen, Bestätigung und Soll entstehen wie bei jeder Kündigung
    (``stammdaten.services.kuendigung_erfassen``)."""
    if anfrage.art != Buchungsanfrage.Art.KUENDIGUNG:
        raise AnbindungFehler(f"Buchungsanfrage #{anfrage.pk} ist keine Kündigung.")
    if anfrage.status not in NOCH_OFFEN:
        raise AnbindungFehler(f"Buchungsanfrage #{anfrage.pk} ist schon entschieden.")
    if not vertrag.vertragseinheiten.filter(einheit=anfrage.einheit).exists():
        raise AnbindungFehler(
            f"Vertrag {vertrag.nummer} umfasst Einheit {anfrage.einheit.nw_id} nicht."
        )
    with transaction.atomic():
        kuendigung = stammdaten.kuendigung_erfassen(
            vertrag,
            zugang_am,
            von=von,
            zum_wunsch=zum_wunsch,
            rueckgabetermin=rueckgabetermin,
            zugangsweg=zugangsweg,
            aufhebung=aufhebung,
        )
        buchungsanfrage_entscheiden(anfrage, Buchungsanfrage.Status.ERLEDIGT, kuendigung=kuendigung)
    return kuendigung


def kuendigung_vorschlag(anfrage: Buchungsanfrage) -> dict[str, object]:
    """Vorbelegung des Kündigungsdialogs aus einer Website-Anfrage (Review 2, A3): Zugang am
    Meldedatum, gekündigt vom Mieter, Wunschtermin, Zugangsweg Website-Formular."""
    return {
        "zugang_am": timezone.localtime(anfrage.gemeldet_am).date(),
        "von": Kuendigung.Von.MIETER,
        "zum_wunsch": anfrage.wunsch_ab,
        "zugangsweg": Kuendigung.Zugangsweg.WEBSITE,
        "anfrage": anfrage.pk,
    }


def vertraege_zur_kuendigung(anfrage: Buchungsanfrage) -> list[Vertrag]:
    """Welche Verträge eine Website-Kündigung meinen kann: der zur angegebenen Nummer, sonst
    die aktiven Verträge der Einheit."""
    if anfrage.vertrag is not None and anfrage.vertrag.status == Vertrag.Status.AKTIV:
        return [anfrage.vertrag]
    return list(anfrage.einheit.vertraege.filter(status=Vertrag.Status.AKTIV).order_by("nummer"))


def _vergleichbar(text: str) -> str:
    return " ".join(text.casefold().split())


def absender_abweichungen(anfrage: Buchungsanfrage, vertrag: Vertrag) -> list[str]:
    """Wo der Absender einer Website-Anfrage nicht zur Vertragspartei passt, als Sätze für
    Max' Oberfläche (Review 2, A3). Nennt Namen und Adressen: nie in Logs, Lauf-Meldungen oder
    Hinweise von Arbeitsposten schreiben."""
    mieter = [
        vp.partei
        for vp in vertrag.vertragsparteien.select_related("partei")
        if vp.rolle == Vertragspartei.Rolle.MIETER
    ]
    namen = [m.anzeigename for m in mieter]
    mails = [
        k.wert
        for m in mieter
        for k in m.kontaktwege.filter(art=Kontaktweg.Art.EMAIL).order_by("pk")
    ]
    saetze: list[str] = []
    if anfrage.kontakt_name and _vergleichbar(anfrage.kontakt_name) not in {
        _vergleichbar(n) for n in namen
    }:
        saetze.append(
            f"Der Absender „{anfrage.kontakt_name}“ steht nicht als Mieter am Vertrag "
            f"{vertrag.nummer} (Mieter: {', '.join(namen) or 'keiner'})."
        )
    if anfrage.kontakt_email and _vergleichbar(anfrage.kontakt_email) not in {
        _vergleichbar(m) for m in mails
    }:
        saetze.append(
            f"Die E-Mail-Adresse „{anfrage.kontakt_email}“ gehört zu keinem Mieter des "
            f"Vertrags {vertrag.nummer} (bekannt: {', '.join(mails) or 'keine'})."
        )
    return saetze


def angenommene_anfrage(einheit: Einheit) -> Buchungsanfrage | None:
    """Die angenommene Buchungsanfrage einer Einheit, die noch auf ihren Vertrag wartet
    (Review 2, A11)."""
    return (
        Buchungsanfrage.objects.filter(
            einheit=einheit,
            art=Buchungsanfrage.Art.BUCHUNG,
            status=Buchungsanfrage.Status.ANGENOMMEN,
        )
        .order_by("entschieden_am", "pk")
        .first()
    )


@als_vorgang("Vertrag {vertrag.nummer} mit Anfrage #{ergebnis.pk} verknüpft")
def vertrag_zur_anfrage(vertrag: Vertrag) -> Buchungsanfrage | None:
    """Verknüpft einen neu angelegten Vertrag mit der angenommenen Buchungsanfrage seiner
    Einheit (Status „Vertrag angelegt“, Arbeitsposten erledigt). Ohne solche Anfrage: nichts.

    Review 2, A11: So finden Anfrage und Vertrag zusammen, egal von welcher Seite der Vertrag
    angelegt wurde (Anfrage, Einheitenseite, „Neuer Vertrag“)."""
    for einheit in vertrag.einheiten.order_by("nw_id"):
        anfrage = angenommene_anfrage(einheit)
        if anfrage is not None:
            return buchungsanfrage_entscheiden(
                anfrage, Buchungsanfrage.Status.VERTRAG_ANGELEGT, vertrag=vertrag
            )
    return None


def buchungsanfrage_zum_posten(posten: Arbeitsposten) -> Buchungsanfrage:
    anfrage = posten.bezug
    if not isinstance(anfrage, Buchungsanfrage):
        raise AnbindungFehler(f"Arbeitsposten #{posten.pk} hängt an keiner Buchungsanfrage.")
    return anfrage


# --- Kennzahlen aus Verwaltio (V12) --------------------------------------------------


@dataclass
class KennzahlErgebnis:
    neu: int = 0
    aktualisiert: int = 0
    #: NW-IDs, deren Abruf gescheitert ist.
    fehler: list[str] = field(default_factory=list)


def wohneinheiten() -> models.QuerySet[Einheit]:
    """Die Wohneinheiten des Hof-Systems (nur NW-ID, geführt in Verwaltio)."""
    return Einheit.objects.filter(typ=Einheit.Typ.WOHNUNG, aufgeloest_am__isnull=True).order_by(
        "nw_id"
    )


def kennzahlen_abrufen(client: VerwaltioClient, datum: datetime.date) -> KennzahlErgebnis:
    """Holt die Kennzahlen jeder Wohneinheit und speichert sie mit NW-ID und Datum.

    Idempotent je Tag: Ein zweiter Lauf am selben Tag aktualisiert die Zeilen dieses Tages.
    Scheitert der Abruf einer Einheit, zählt das als Fehler; die anderen laufen weiter.
    """
    ergebnis = KennzahlErgebnis()
    for einheit in wohneinheiten():
        try:
            werte = client.kennzahlen(einheit.nw_id, datum)
        except VerwaltioFehler:
            logger.warning("Verwaltio: Kennzahlen für %s nicht abrufbar", einheit.nw_id)
            ergebnis.fehler.append(einheit.nw_id)
            continue
        if werte.offene_posten < 0 or werte.mahnfaelle < 0 or werte.fristen_offen < 0:
            ergebnis.fehler.append(einheit.nw_id)
            continue
        zeile = Kennzahl.objects.filter(nw_id=einheit.nw_id, datum=datum).first()
        if zeile is None:
            zeile = Kennzahl(nw_id=einheit.nw_id, datum=datum)
            ergebnis.neu += 1
        else:
            ergebnis.aktualisiert += 1
        zeile.einheit = einheit
        zeile.offene_posten = werte.offene_posten
        zeile.mahnfaelle = werte.mahnfaelle
        zeile.nka_jahr = werte.nka_jahr
        zeile.nka_stand = (
            werte.nka_stand
            if werte.nka_stand in Kennzahl.NkaStand.values
            else Kennzahl.NkaStand.UNBEKANNT
        )
        zeile.fristen_offen = werte.fristen_offen
        zeile.naechste_frist = werte.naechste_frist
        _speichern(zeile)
    return ergebnis


@dataclass(frozen=True)
class WohnraumUeberblick:
    """Zahlen der Kachel „Wohnraum“: nur Zähler, Summen und Daten, keine Namen."""

    stand: datetime.date | None
    wohneinheiten: int
    mit_kennzahl: int
    offene_posten: Decimal
    mahnfaelle: int
    einheiten_mit_mahnung: int
    nka_jahr: int | None
    #: (Titel, Anzahl) je Stand der NKA, in der Reihenfolge von ``Kennzahl.NkaStand``.
    nka: tuple[tuple[str, int], ...]
    fristen_offen: int
    naechste_frist: datetime.date | None
    veraltet: bool

    @property
    def ohne_kennzahl(self) -> int:
        return max(self.wohneinheiten - self.mit_kennzahl, 0)


#: Ab so vielen Tagen ohne neue Kennzahlen gilt der Stand als veraltet.
VERALTET_NACH_TAGEN = 2


def wohnraum_ueberblick(stichtag: datetime.date | None = None) -> WohnraumUeberblick:
    """Kennzahlen Wohnraum vom letzten Abruf bis zum Stichtag, zusammengezählt."""
    stichtag = stichtag or timezone.localdate()
    anzahl = wohneinheiten().count()
    stand = (
        Kennzahl.objects.filter(datum__lte=stichtag).aggregate(m=Max("datum"))["m"]
        if Kennzahl.objects.exists()
        else None
    )
    if stand is None:
        return WohnraumUeberblick(
            stand=None,
            wohneinheiten=anzahl,
            mit_kennzahl=0,
            offene_posten=Decimal("0.00"),
            mahnfaelle=0,
            einheiten_mit_mahnung=0,
            nka_jahr=None,
            nka=(),
            fristen_offen=0,
            naechste_frist=None,
            veraltet=True,
        )
    zeilen = list(Kennzahl.objects.filter(datum=stand))
    summen = Kennzahl.objects.filter(datum=stand).aggregate(
        posten=Sum("offene_posten"), mahn=Sum("mahnfaelle"), fristen=Sum("fristen_offen")
    )
    staende = Counter(z.nka_stand for z in zeilen)
    fristen = [z.naechste_frist for z in zeilen if z.naechste_frist is not None]
    jahre = [z.nka_jahr for z in zeilen if z.nka_jahr is not None]
    return WohnraumUeberblick(
        stand=stand,
        wohneinheiten=anzahl,
        mit_kennzahl=len(zeilen),
        offene_posten=(summen["posten"] or Decimal("0")).quantize(Decimal("0.01")),
        mahnfaelle=summen["mahn"] or 0,
        einheiten_mit_mahnung=sum(1 for z in zeilen if z.mahnfaelle),
        nka_jahr=max(jahre) if jahre else None,
        nka=tuple(
            (titel, staende[wert]) for wert, titel in Kennzahl.NkaStand.choices if staende[wert]
        ),
        fristen_offen=summen["fristen"] or 0,
        naechste_frist=min(fristen) if fristen else None,
        veraltet=(stichtag - stand).days >= VERALTET_NACH_TAGEN,
    )


# --- ID-Abgleich (H1.6) --------------------------------------------------------------


@dataclass(frozen=True)
class Befund:
    """Eine Abweichung, wie ein Abgleich sie findet (noch nicht gespeichert)."""

    quelle: str
    art: str
    nw_id: str
    gemeldet: str = ""

    @property
    def schluessel(self) -> tuple[str, str]:
        return (self.art, self.nw_id)


def _normal(nw_id: str) -> str:
    """Schreibweise ohne Leerzeichen, in Großbuchstaben, mit einfachem Bindestrich."""
    text = "".join(nw_id.split()).upper()
    for strich in ("‐", "‑", "‒", "–", "—", "_"):
        text = text.replace(strich, "-")
    return text


def _befunde(
    quelle: str,
    gemeldet: Iterable[WebsiteEinheit],
    erwartet: set[str],
    alle: dict[str, Einheit],
) -> list[Befund]:
    """Vergleicht die IDs einer Quelle mit dem Hof-System.

    - unbekannt: Die Quelle führt eine ID, die es im Hof-System nicht gibt.
    - Schreibweise: Die ID gibt es, aber anders geschrieben (z. B. klein).
    - nicht erwartet: Die ID gibt es, aber sie gehört nicht in diese Quelle (aufgelöst, nicht
      vermarktet, Wohnraum auf der Website, kein Wohnraum in Verwaltio).
    - Slug: Website-Slug weicht vom Slug im Hof-System ab.
    - fehlt: Eine ID, die in die Quelle gehört, fehlt dort.
    """
    normal = {_normal(nw_id): nw_id for nw_id in alle}
    befunde: list[Befund] = []
    gesehen: set[str] = set()
    for eintrag in gemeldet:
        roh = eintrag.nw_id.strip()[:100]
        if roh in alle:
            nw_id = roh
        elif _normal(roh) in normal:
            nw_id = normal[_normal(roh)]
            befunde.append(Befund(quelle, Abweichung.Art.SCHREIBWEISE, nw_id, roh))
        else:
            befunde.append(Befund(quelle, Abweichung.Art.UNBEKANNT, roh, roh))
            continue
        if nw_id in gesehen:
            continue
        gesehen.add(nw_id)
        if nw_id not in erwartet:
            befunde.append(Befund(quelle, Abweichung.Art.NICHT_ERWARTET, nw_id, roh))
        slug_hof = alle[nw_id].website_slug
        if eintrag.slug and slug_hof and eintrag.slug != slug_hof:
            befunde.append(Befund(quelle, Abweichung.Art.SLUG, nw_id, eintrag.slug[:100]))
    befunde += [Befund(quelle, Abweichung.Art.FEHLT, nw_id) for nw_id in sorted(erwartet - gesehen)]
    # Doppelte Befunde (dieselbe falsche ID zweimal) zählen einmal.
    return list({b.schluessel: b for b in befunde}.values())


def befunde_website(eintraege: Iterable[WebsiteEinheit], stichtag: datetime.date) -> list[Befund]:
    alle = {e.nw_id: e for e in Einheit.objects.all()}
    erwartet = set(vermarktbare_einheiten(stichtag).values_list("nw_id", flat=True))
    return _befunde(Abweichung.Quelle.WEBSITE, eintraege, erwartet, alle)


def befunde_verwaltio(ids: Iterable[str]) -> list[Befund]:
    alle = {e.nw_id: e for e in Einheit.objects.all()}
    erwartet = set(wohneinheiten().values_list("nw_id", flat=True))
    return _befunde(
        Abweichung.Quelle.VERWALTIO, [WebsiteEinheit(nw_id=i) for i in ids], erwartet, alle
    )


@dataclass
class AbgleichErgebnis:
    neu: list[Abweichung] = field(default_factory=list)
    weiter_offen: int = 0
    behoben: int = 0


def abgleich_anwenden(
    quelle: str,
    befunde: Sequence[Befund],
    datum: datetime.date,
    *,
    lauf: Lauf | None = None,
) -> AbgleichErgebnis:
    """Speichert die Befunde einer Quelle: Neue werden Abweichung und Arbeitsposten, bekannte
    bleiben offen, verschwundene schließen sich (behoben am ``datum``)."""
    if quelle not in Abweichung.Quelle.values:
        raise AnbindungFehler(f"Unbekannte Quelle {quelle}")
    ergebnis = AbgleichErgebnis()
    offen = {
        (a.art, a.nw_id): a
        for a in Abweichung.objects.filter(quelle=quelle, behoben_am__isnull=True)
    }
    with transaction.atomic():
        for befund in befunde:
            vorhanden = offen.pop(befund.schluessel, None)
            if vorhanden is not None:
                vorhanden.zuletzt_gesehen_am = datum
                vorhanden.gemeldet = befund.gemeldet
                _speichern(vorhanden)
                if not _posten_zu(ART_ID_ABWEICHUNG, vorhanden).exists():
                    _abweichung_posten(vorhanden, lauf)
                ergebnis.weiter_offen += 1
                continue
            abweichung = Abweichung(
                quelle=quelle,
                art=befund.art,
                nw_id=befund.nw_id,
                gemeldet=befund.gemeldet,
                erkannt_am=datum,
                zuletzt_gesehen_am=datum,
            )
            _speichern(abweichung)
            _abweichung_posten(abweichung, lauf)
            ergebnis.neu.append(abweichung)
        for behoben in offen.values():
            behoben.behoben_am = datum
            _speichern(behoben)
            _posten_schliessen(ART_ID_ABWEICHUNG, behoben)
            ergebnis.behoben += 1
    return ergebnis


def _abweichung_posten(abweichung: Abweichung, lauf: Lauf | None) -> Arbeitsposten:
    return kern.arbeitsposten_anlegen(
        ART_ID_ABWEICHUNG,
        bezug=abweichung,
        aktionen=("zurueckstellen",),
        hinweis=(
            f"{abweichung.get_quelle_display()}: {abweichung.nw_id} {abweichung.get_art_display()}"
        ),
        lauf=lauf,
    )


@dataclass
class IdAbgleichErgebnis:
    #: Quelle -> Ergebnis; eine Quelle, die nicht antwortet, fehlt hier und steht in ``fehler``.
    quellen: dict[str, AbgleichErgebnis] = field(default_factory=dict)
    #: Quelle -> Meldung (ohne Werte).
    fehler: dict[str, str] = field(default_factory=dict)

    @property
    def neu(self) -> int:
        return sum(len(e.neu) for e in self.quellen.values())

    @property
    def behoben(self) -> int:
        return sum(e.behoben for e in self.quellen.values())

    @property
    def weiter_offen(self) -> int:
        return sum(e.weiter_offen for e in self.quellen.values())


def id_abgleich(
    *,
    website: WebsiteClient | None,
    verwaltio: VerwaltioClient | None,
    datum: datetime.date,
    lauf: Lauf | None = None,
) -> IdAbgleichErgebnis:
    """Vergleicht die NW-IDs von Website und Verwaltio mit dem Hof-System.

    Antwortet eine Quelle nicht (oder ist ihr Client nicht eingerichtet, ``None``), bleiben
    ihre Abweichungen unverändert: Nichts schließt sich, nur weil die Quelle schweigt.
    """
    ergebnis = IdAbgleichErgebnis()
    abfragen: list[tuple[str, Callable[[], list[Befund]] | None]] = [
        (
            Abweichung.Quelle.WEBSITE,
            (lambda: befunde_website(website.einheiten(), datum)) if website else None,
        ),
        (
            Abweichung.Quelle.VERWALTIO,
            (lambda: befunde_verwaltio(verwaltio.wohneinheiten())) if verwaltio else None,
        ),
    ]
    for quelle, abfrage in abfragen:
        if abfrage is None:
            ergebnis.fehler[quelle] = "nicht eingerichtet"
            continue
        try:
            befunde = abfrage()
        except Exception as fehler:
            logger.warning(
                "ID-Abgleich: Quelle %s antwortet nicht (%s)", quelle, type(fehler).__name__
            )
            ergebnis.fehler[quelle] = type(fehler).__name__
            continue
        ergebnis.quellen[quelle] = abgleich_anwenden(quelle, befunde, datum, lauf=lauf)
    return ergebnis
