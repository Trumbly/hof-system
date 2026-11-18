"""Zuordnung der Felder für den Import der Mietregeln aus hof-fints. Die einzige Stelle dafür.

Das echte Format von ``regeln.yaml`` ist unbekannt; die Datei wurde nie gelesen. ``REGELN_YAML``
ist eine Annahme: YAML mit Abschnitten (``mietregeln``, ``pachtregeln``), je Eintrag eine ID,
die Bedingungen des Zahlers (Name, IBAN, Zweck), der Vertrag (Vertragsnummer, NW-ID oder eine
Liste für Sammelzahler) und der erwartete Betrag. Eine erfundene Beispieldatei liegt unter
``tests/fixtures/abgleich/regeln_beispiel.yaml``.

Weicht die echte Datei ab, wird nur ``REGELN_YAML`` angepasst: Abschnitte, Pfade der Felder
(Punkte trennen verschachtelte Schlüssel) und die Kategorien für neue Regeln im Kassenbuch.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

MIETE = "miete"
PACHT = "pacht"


@dataclass(frozen=True)
class RegelFormat:
    name: str
    #: Abschnitt der Datei -> Art (``miete``: mit Vertrag; ``pacht``: nicht vertragsbezogen,
    #: Pachtverträge führt das Hof-System in v1 noch nicht).
    abschnitte: Mapping[str, str]
    #: Feld des Hof-Systems -> Pfad im Eintrag. Felder: schluessel, name, iban, zweck,
    #: vertrag, nw_id, vertraege, betrag.
    felder: Mapping[str, str]
    #: Vertragsart -> Kategorie (Bezeichnung) für eine neue Regel im Kassenbuch.
    kategorien: Mapping[str, str]
    #: Kategorie und Objekt (Kürzel) für neue Regeln ohne Vertrag (Pacht).
    pacht_kategorie: str
    pacht_objekt: str


REGELN_YAML = RegelFormat(
    name="hof-fints",
    abschnitte={"mietregeln": MIETE, "pachtregeln": PACHT},
    felder={
        "schluessel": "id",
        "name": "zahler.name",
        "iban": "zahler.iban",
        "zweck": "zahler.zweck",
        "vertrag": "vertrag",
        "nw_id": "nw_id",
        "vertraege": "vertraege",
        "betrag": "betrag",
    },
    kategorien={
        "G": "Mieteinnahmen Garagen",
        "S": "Mieteinnahmen Stellplätze",
        "L": "Mieteinnahmen Lager und Hallen",
        "H": "Mieteinnahmen Lager und Hallen",
        "GA": "Sonstige Einnahmen",
        "A": "Sonstige Einnahmen",
        "X": "Sonstige Einnahmen",
    },
    pacht_kategorie="Pachteinnahmen",
    pacht_objekt="FELD",
)

FORMATE: dict[str, RegelFormat] = {REGELN_YAML.name: REGELN_YAML}
