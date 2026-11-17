"""Import alter Umsätze aus einer CSV-Datei (H3.3): lesen, prüfen, Bericht mit Zählern.

Der Bericht nennt nur Zähler und Arten von Abweichungen, nie Namen, Beträge oder Zwecke
(CLAUDE.md, Abschnitt 1). Geschrieben wird über ``services.umsaetze_uebernehmen``; der
Fingerabdruck verhindert Doppelte, auch wenn dieselbe Datei zweimal kommt.

Abruf und Export können denselben Umsatz unterschiedlich darstellen (etwa den Zweck).
Damit sich Import und Abruf nicht überschneiden, übernimmt der Import nur Tage *vor*
dem ersten Abruf des Kontos; spätere Zeilen zählt er als „ab Abrufbeginn übersprungen“.
"""

from __future__ import annotations

import csv
import datetime
import io
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.db.models import Min

from apps.bank import services
from apps.bank.importformat import PFLICHTFELDER, CsvFormat
from apps.bank.models import Bankabruf, Bankkonto, Bankumsatz
from apps.kern.models import Lauf


class ImportFehler(ValueError):
    """Die Datei passt nicht zum Format. Die Meldung nennt nur Spalten und Zähler."""


@dataclass
class ImportBericht:
    zeilen: int = 0
    gueltig: int = 0
    fehlerhaft: Counter[str] = field(default_factory=Counter)
    uebersprungen_ab_abruf: int = 0
    neu: int = 0
    doppelt: int = 0
    ausgefuehrt: bool = False
    abruf_id: int | None = None

    def zeilen_text(self) -> list[str]:
        """Der Bericht als Zeilen: nur Zähler und Abweichungsarten."""
        zeilen = [
            f"Zeilen gelesen: {self.zeilen}",
            f"gültig: {self.gueltig}",
            f"fehlerhaft: {sum(self.fehlerhaft.values())}",
        ]
        zeilen += [f"  {art}: {anzahl}" for art, anzahl in sorted(self.fehlerhaft.items())]
        zeilen += [
            f"ab Abrufbeginn übersprungen: {self.uebersprungen_ab_abruf}",
            f"neu: {self.neu}",
            f"schon vorhanden (doppelt): {self.doppelt}",
        ]
        if self.ausgefuehrt:
            zeilen.append(f"Ausgeführt, Abruf #{self.abruf_id}.")
        else:
            zeilen.append("Probelauf: nichts geändert. Übernehmen mit --ausfuehren.")
        return zeilen


def _betrag(text: str, format: CsvFormat) -> Decimal:
    roh = text.strip().replace(" ", "")
    if format.tausendertrenner:
        roh = roh.replace(format.tausendertrenner, "")
    if format.dezimaltrenner != ".":
        roh = roh.replace(format.dezimaltrenner, ".")
    return Decimal(roh)


def _datum(text: str, format: CsvFormat) -> datetime.date:
    return datetime.datetime.strptime(text.strip(), format.datumsformat).date()


def lesen(inhalt: str, format: CsvFormat) -> tuple[list[services.UmsatzDaten], ImportBericht]:
    """Liest die Datei in ``UmsatzDaten``; fehlerhafte Zeilen zählt der Bericht nach Art."""
    leser = csv.DictReader(io.StringIO(inhalt), delimiter=format.trennzeichen)
    kopf = set(leser.fieldnames or [])
    fehlend = [format.spalten[f] for f in PFLICHTFELDER if format.spalten[f] not in kopf]
    if fehlend:
        raise ImportFehler(f"Pflichtspalten fehlen: {', '.join(fehlend)}")

    def spalte(zeile: dict[str, str], feld: str) -> str:
        name = format.spalten.get(feld)
        return (zeile.get(name) or "").strip() if name else ""

    bericht = ImportBericht()
    umsaetze = []
    for zeile in leser:
        bericht.zeilen += 1
        try:
            buchungsdatum = _datum(spalte(zeile, "buchungsdatum"), format)
        except ValueError:
            bericht.fehlerhaft["Buchungsdatum ungültig"] += 1
            continue
        try:
            betrag = _betrag(spalte(zeile, "betrag"), format)
        except InvalidOperation:
            bericht.fehlerhaft["Betrag ungültig"] += 1
            continue
        if betrag != betrag.quantize(services.CENT):
            bericht.fehlerhaft["Betrag mit mehr als zwei Nachkommastellen"] += 1
            continue
        valuta_text = spalte(zeile, "valuta")
        try:
            valuta = _datum(valuta_text, format) if valuta_text else None
        except ValueError:
            bericht.fehlerhaft["Valuta ungültig"] += 1
            continue
        umsaetze.append(
            services.UmsatzDaten(
                buchungsdatum=buchungsdatum,
                betrag=abs(betrag),
                richtung=(
                    Bankumsatz.Richtung.AUSGANG if betrag < 0 else Bankumsatz.Richtung.EINGANG
                ),
                gegenpartei=spalte(zeile, "gegenpartei"),
                gegen_iban=spalte(zeile, "gegen_iban"),
                verwendungszweck=spalte(zeile, "verwendungszweck"),
                valuta=valuta,
                bank_referenz=spalte(zeile, "bank_referenz"),
                waehrung=spalte(zeile, "waehrung") or "EUR",
                rohdaten={"quelle": f"import:{format.name}", "zeile": dict(zeile)},
            )
        )
        bericht.gueltig += 1
    return umsaetze, bericht


def abrufbeginn(konto: Bankkonto) -> datetime.date | None:
    """Der erste Tag, den ein Abruf oder eine Zulieferung für das Konto abgedeckt hat."""
    abrufe = konto.abrufe.exclude(quelle=Bankabruf.Quelle.IMPORT).filter(
        status=Bankabruf.Status.ERFOLGREICH
    )
    ergebnis: dict[str, datetime.date | None] = abrufe.aggregate(
        von=Min("von"), erster=Min("umsaetze__buchungsdatum")
    )
    tage = [t for t in (ergebnis["von"], ergebnis["erster"]) if t is not None]
    return min(tage) if tage else None


def importieren(
    konto: Bankkonto,
    inhalt: str,
    format: CsvFormat,
    *,
    ausfuehren: bool = False,
    bis: datetime.date | None = None,
    lauf: Lauf | None = None,
) -> ImportBericht:
    """Probelauf (Standard) oder Übernahme. Im Probelauf ändert sich nichts.

    ``bis``: Nur Umsätze vor diesem Tag; Standard ist der Abrufbeginn des Kontos.
    """
    umsaetze, bericht = lesen(inhalt, format)
    grenze = bis or abrufbeginn(konto)
    if grenze is not None:
        behalten = [u for u in umsaetze if u.buchungsdatum < grenze]
        bericht.uebersprungen_ab_abruf = len(umsaetze) - len(behalten)
        umsaetze = behalten

    if not ausfuehren:
        vorschau = services.umsaetze_vorschau(konto, umsaetze)
        bericht.neu, bericht.doppelt = vorschau.neu, vorschau.doppelt
        return bericht

    abruf = services.bankabruf_starten(
        konto,
        Bankabruf.Quelle.IMPORT,
        von=min((u.buchungsdatum for u in umsaetze), default=None),
        bis=max((u.buchungsdatum for u in umsaetze), default=None),
        lauf=lauf,
    )
    ergebnis = services.umsaetze_uebernehmen(konto, umsaetze, abruf=abruf)
    services.bankabruf_beenden(
        abruf,
        meldung=(
            f"Import: {ergebnis.neu} neu, {ergebnis.doppelt} doppelt, "
            f"{sum(bericht.fehlerhaft.values())} fehlerhaft"
        ),
    )
    bericht.neu, bericht.doppelt = ergebnis.neu, ergebnis.doppelt
    bericht.ausgefuehrt = True
    bericht.abruf_id = abruf.pk
    return bericht
