"""Spaltenzuordnung für den Import alter Umsätze (H3.3). Die einzige Stelle dafür.

Das echte Exportformat von Vivid liegt nicht vor. ``VIVID_CSV`` ist eine Annahme,
angelehnt an einen üblichen Kontoexport: Komma als Trenner, UTF-8, Datum als
JJJJ-MM-TT, Betrag mit Vorzeichen und Punkt (Eingang positiv, Ausgang negativ). Eine
erfundene Beispieldatei liegt unter ``tests/fixtures/bank/vivid_beispiel.csv``.

Weicht der echte Export ab, wird nur ``VIVID_CSV`` angepasst: Spaltennamen in
``spalten``, Trenner, Datumsformat, Dezimal- und Tausendertrenner.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class CsvFormat:
    name: str
    trennzeichen: str
    kodierung: str
    #: für ``datetime.strptime``
    datumsformat: str
    dezimaltrenner: str
    tausendertrenner: str
    #: Feld des Hof-Systems -> Spaltenname in der Datei. Pflicht: buchungsdatum, betrag.
    spalten: Mapping[str, str]


PFLICHTFELDER = ("buchungsdatum", "betrag")
FELDER = (
    "buchungsdatum",
    "valuta",
    "betrag",
    "waehrung",
    "gegenpartei",
    "gegen_iban",
    "verwendungszweck",
    "bank_referenz",
)

VIVID_CSV = CsvFormat(
    name="vivid",
    trennzeichen=",",
    kodierung="utf-8-sig",
    datumsformat="%Y-%m-%d",
    dezimaltrenner=".",
    tausendertrenner="",
    spalten={
        "buchungsdatum": "Booking Date",
        "valuta": "Value Date",
        "betrag": "Amount",
        "waehrung": "Currency",
        "gegenpartei": "Counterparty Name",
        "gegen_iban": "Counterparty IBAN",
        "verwendungszweck": "Payment Reference",
        "bank_referenz": "Transaction ID",
    },
)

FORMATE: dict[str, CsvFormat] = {VIVID_CSV.name: VIVID_CSV}
