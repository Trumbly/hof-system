"""Angaben einer Rechnung aus der Textschicht des PDFs (Review 2, B9).

Keine KI und keine Texterkennung von Bildern: gelesen wird nur der Text, den das PDF selbst
trägt (pypdf). Gesucht wird mit festen Mustern deutscher Rechnungen:

- **Rechnungsnummer** hinter „Rechnungsnummer“, „Rechnungs-Nr.“, „Rechnung Nr.“, „Re.-Nr.“
- **Datum** hinter „Rechnungsdatum“ oder „Datum“ (TT.MM.JJJJ), sonst das erste Datum
- **Betrag** hinter „Gesamtbetrag“, „Rechnungsbetrag“, „Endbetrag“, „zu zahlen“, „Summe“
  oder „Betrag“ (in dieser Rangfolge), mit Komma oder Punkt als Dezimalzeichen
- **Lieferant** über IBAN, dann E-Mail, dann Namen der vorhandenen Lieferanten; für einen
  neuen Lieferanten als Vorschlag die erste Zeile (Briefkopf)

Das Ergebnis ist ein Vorschlag für das Formular. Gespeichert wird nichts; was Max bestätigt,
schreibt ``services.beleg_bestaetigen``. Der Text selbst geht in kein Log.
"""

from __future__ import annotations

import datetime
import hashlib
import io
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.core.cache import cache
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from apps.dokumente.models import Dokument, Lieferant

logger = logging.getLogger(__name__)

#: Mehr liest die Erkennung nicht (große Scans haben ohnehin keine Textschicht).
HOECHSTENS_SEITEN = 5

_NUMMER = re.compile(
    r"(?:Rechnungs\s*-?\s*(?:nummer|nr\.?)|Rechnung\s+Nr\.?|Re\.?\s*-?\s*Nr\.?)"
    r"\s*[:.]?\s*(?P<nummer>[A-Za-z0-9][A-Za-z0-9/._-]*)",
    re.IGNORECASE,
)
_DATUM_TEXT = r"(?P<tag>\d{1,2})\.(?P<monat>\d{1,2})\.(?P<jahr>\d{4})"
_DATUM_MIT_TITEL = re.compile(
    r"(?:Rechnungsdatum|Belegdatum|Datum)\s*:?\s*" + _DATUM_TEXT, re.IGNORECASE
)
_DATUM = re.compile(r"(?<![\d.])" + _DATUM_TEXT + r"(?![\d])")
_ZAHL = r"(?P<betrag>\d{1,3}(?:\.\d{3})+,\d{2}|\d+,\d{2}|\d+\.\d{2})"
#: Rangfolge der Titel: Der erste Titel, hinter dem ein Betrag steht, gilt.
_BETRAG_TITEL = (
    r"Gesamtbetrag",
    r"Rechnungsbetrag",
    r"Endbetrag",
    r"zu\s+zahlen",
    r"Gesamtsumme",
    r"Summe",
    r"Betrag",
)
_BETRAG = [
    re.compile(
        rf"(?<![A-Za-zÄÖÜäöüß]){titel}(?:\s*\(?brutto\)?)?\s*:?\s*(?:EUR|€)?\s*{_ZAHL}",
        re.IGNORECASE,
    )
    for titel in _BETRAG_TITEL
]
_IBAN = re.compile(r"\b([A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}(?:\s?[A-Z0-9]{1,4})?)\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


@dataclass(frozen=True)
class Angaben:
    """Was die Textschicht hergibt. Leere Felder: nicht gefunden."""

    datum: datetime.date | None = None
    betrag: Decimal | None = None
    rechnungsnummer: str = ""
    lieferant: Lieferant | None = None
    #: Vorschlag für einen neuen Lieferanten (erste Zeile), wenn keiner erkannt ist.
    lieferant_name: str = ""

    @property
    def leer(self) -> bool:
        return not (self.datum or self.betrag or self.rechnungsnummer or self.lieferant)

    @property
    def gefunden(self) -> list[str]:
        """Titel der gefundenen Angaben (für den Hinweis über dem Formular)."""
        titel = []
        if self.lieferant is not None:
            titel.append("Lieferant")
        if self.datum is not None:
            titel.append("Datum")
        if self.betrag is not None:
            titel.append("Betrag")
        if self.rechnungsnummer:
            titel.append("Rechnungsnummer")
        return titel


def text_aus_pdf(inhalt: bytes) -> str:
    """Der Text der ersten Seiten; leer, wenn das PDF keine Textschicht hat oder kaputt ist."""
    try:
        leser = PdfReader(io.BytesIO(inhalt))
        seiten = [seite.extract_text() or "" for seite in leser.pages[:HOECHSTENS_SEITEN]]
    except (PyPdfError, ValueError, OSError, KeyError, TypeError):
        logger.info("Textschicht nicht lesbar")
        return ""
    return "\n".join(seiten)


def betrag_lesen(text: str) -> Decimal | None:
    """„1.234,56“, „627,34“ oder „627.34“ als Decimal mit zwei Stellen."""
    roh = text.strip()
    if "," in roh:
        roh = roh.replace(".", "").replace(",", ".")
    try:
        wert = Decimal(roh).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None
    return wert if wert > 0 else None


def _datum(treffer: re.Match[str]) -> datetime.date | None:
    try:
        return datetime.date(int(treffer["jahr"]), int(treffer["monat"]), int(treffer["tag"]))
    except ValueError:
        return None


def datum_finden(text: str) -> datetime.date | None:
    for muster in (_DATUM_MIT_TITEL, _DATUM):
        for treffer in muster.finditer(text):
            datum = _datum(treffer)
            if datum is not None:
                return datum
    return None


def betrag_finden(text: str) -> Decimal | None:
    for muster in _BETRAG:
        treffer = list(muster.finditer(text))
        if treffer:
            # Bei mehreren Zeilen gleichen Titels (Zwischensummen) gilt die letzte.
            betrag = betrag_lesen(treffer[-1]["betrag"])
            if betrag is not None:
                return betrag
    return None


def nummer_finden(text: str) -> str:
    treffer = _NUMMER.search(text)
    if treffer is None:
        return ""
    return treffer["nummer"].rstrip(".")[:100]


def _iban_normal(text: str) -> str:
    return re.sub(r"\s", "", text).upper()


def lieferant_finden(text: str, lieferanten: Iterable[Lieferant]) -> Lieferant | None:
    """Über IBAN, dann E-Mail, dann Namen (der längste passende Name gewinnt)."""
    alle = list(lieferanten)
    ibans = {_iban_normal(t) for t in _IBAN.findall(text)}
    for lieferant in alle:
        if lieferant.iban and _iban_normal(lieferant.iban) in ibans:
            return lieferant
    mails = {m.casefold() for m in _EMAIL.findall(text)}
    for lieferant in alle:
        if lieferant.email and lieferant.email.casefold() in mails:
            return lieferant
    klein = text.casefold()
    nach_name = sorted(
        (lf for lf in alle if len(lf.name.strip()) >= 4 and lf.name.strip().casefold() in klein),
        key=lambda lf: -len(lf.name),
    )
    return nach_name[0] if nach_name else None


def _erste_zeile(text: str) -> str:
    for zeile in text.splitlines():
        zeile = zeile.strip()
        if len(zeile) >= 3 and not _DATUM.search(zeile) and "rechnung" not in zeile.casefold():
            return zeile[:200]
    return ""


def angaben_aus_text(text: str, lieferanten: Iterable[Lieferant] = ()) -> Angaben:
    if not text.strip():
        return Angaben()
    lieferant = lieferant_finden(text, lieferanten)
    return Angaben(
        datum=datum_finden(text),
        betrag=betrag_finden(text),
        rechnungsnummer=nummer_finden(text),
        lieferant=lieferant,
        lieferant_name="" if lieferant is not None else _erste_zeile(text),
    )


def angaben_aus_pdf(inhalt: bytes, lieferanten: Iterable[Lieferant] | None = None) -> Angaben:
    if lieferanten is None:
        lieferanten = Lieferant.objects.filter(aktiv=True).order_by("pk")
    return angaben_aus_text(text_aus_pdf(inhalt), lieferanten)


def angaben_aus_dokument(dokument: Dokument) -> Angaben:
    """Liest das PDF eines Dokuments der Ablage (ohne Datei: keine Angaben)."""
    if not dokument.datei:
        return Angaben()
    try:
        with dokument.datei.open("rb") as datei:
            inhalt = datei.read()
    except (OSError, ValueError):
        logger.info("Dokument #%s: PDF nicht lesbar", dokument.pk)
        return Angaben()
    return angaben_aus_pdf(inhalt)


# --- Gelesene Angaben für Listen (Review 3, Punkt 7) ---------------------------------
#
# Die Ablage zeigt bei offenen Belegen Lieferant und Betrag, wie sie im PDF stehen (grau,
# „gelesen“). Entschieden: beim Anzeigen lesen, mit Cache, statt beim Eingang speichern.
# - Ein Vorschlag ist kein Datenstand. Gespeichert wird erst, was Max bestätigt
#   (``services.beleg_bestaetigen``); es bleibt bei einem Schreibweg und ohne Migration.
# - Es gilt sofort für alle Belege, auch die vor diesem Stand eingegangenen.
# - Kommt ein Lieferant dazu oder ändert sich seine IBAN, erkennt die Liste ihn gleich: Der
#   Schlüssel des Caches enthält den Stand der Lieferanten, die Prüfsumme steht für die Datei.
# Teuer ist nur das erste Lesen je Datei (pypdf, höchstens fünf Seiten); danach kommt es aus
# dem Cache. Im Cache stehen nur Datum, Betrag, Nummer, die ID des Lieferanten und die erste
# Zeile, nie der Text des PDFs.

#: So lange bleibt eine gelesene Angabe im Cache (die Datei ändert sich nie).
CACHE_SEKUNDEN = 7 * 24 * 3600

Gemerkt = dict[str, str | int | None]


def _lieferanten_stand(lieferanten: Iterable[Lieferant]) -> str:
    teile = [f"{lf.pk}|{lf.name}|{lf.iban}|{lf.email}" for lf in lieferanten]
    return hashlib.sha256("\n".join(teile).encode()).hexdigest()[:16]


def _ablegen(angaben: Angaben) -> Gemerkt:
    return {
        "datum": angaben.datum.isoformat() if angaben.datum else None,
        "betrag": str(angaben.betrag) if angaben.betrag is not None else None,
        "rechnungsnummer": angaben.rechnungsnummer,
        "lieferant": angaben.lieferant.pk if angaben.lieferant is not None else None,
        "lieferant_name": angaben.lieferant_name,
    }


def _laden(werte: Gemerkt, lieferanten: dict[int, Lieferant]) -> Angaben:
    datum, betrag, lieferant = werte.get("datum"), werte.get("betrag"), werte.get("lieferant")
    return Angaben(
        datum=datetime.date.fromisoformat(str(datum)) if datum else None,
        betrag=Decimal(str(betrag)) if betrag is not None else None,
        rechnungsnummer=str(werte.get("rechnungsnummer") or ""),
        lieferant=lieferanten.get(int(lieferant)) if lieferant is not None else None,
        lieferant_name=str(werte.get("lieferant_name") or ""),
    )


def angaben_gemerkt(dokumente: Iterable[Dokument]) -> dict[int, Angaben]:
    """Die gelesenen Angaben je Dokument-ID, aus dem Cache oder frisch aus dem PDF."""
    liste = [d for d in dokumente if d.datei]
    if not liste:
        return {}
    lieferanten = list(Lieferant.objects.filter(aktiv=True).order_by("pk"))
    nach_pk = {lf.pk: lf for lf in lieferanten}
    stand = _lieferanten_stand(lieferanten)
    schluessel = {d.pk: f"textschicht:{d.sha256}:{stand}" for d in liste}
    gemerkt = cache.get_many(list(schluessel.values()))
    ergebnis: dict[int, Angaben] = {}
    neu: dict[str, Gemerkt] = {}
    for dok in liste:
        werte = gemerkt.get(schluessel[dok.pk])
        if werte is None:
            try:
                with dok.datei.open("rb") as datei:
                    inhalt = datei.read()
            except (OSError, ValueError):
                logger.info("Dokument #%s: PDF nicht lesbar", dok.pk)
                inhalt = b""
            werte = _ablegen(angaben_aus_pdf(inhalt, lieferanten) if inhalt else Angaben())
            neu[schluessel[dok.pk]] = werte
        ergebnis[dok.pk] = _laden(werte, nach_pk)
    if neu:
        cache.set_many(neu, CACHE_SEKUNDEN)
    return ergebnis
