"""Spaltenzuordnung für den Import der Kategorien. Die einzige Stelle dafür.

Das echte Format (Kategorien aus dem Finanz-Dashboard) liegt nicht vor. ``KATEGORIEN_CSV``
ist eine Annahme: Semikolon als Trenner, UTF-8 (auch mit BOM), Kopfzeile mit den Spalten
„Kategorie“, „Art“ und optional „Mandant“ (Kürzel, nur bei Entnahmen nötig). Eine erfundene
Beispieldatei liegt unter ``tests/fixtures/kassenbuch/kategorien_beispiel.csv``.

Weicht die echte Liste ab, wird nur ``KATEGORIEN_CSV`` angepasst: Spaltennamen in
``spalten``, Trenner, Kodierung und die Schreibweisen der Arten in ``arten``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from apps.kassenbuch.models import Kategorie


@dataclass(frozen=True)
class KategorienFormat:
    name: str
    trennzeichen: str
    kodierung: str
    #: Feld des Hof-Systems -> Spaltenname in der Datei. Pflicht: name, art.
    spalten: Mapping[str, str]
    #: Schreibweise in der Datei (klein, ohne Leerzeichen am Rand) -> Art im Hof-System.
    arten: Mapping[str, str]


PFLICHTFELDER = ("name", "art")

KATEGORIEN_CSV = KategorienFormat(
    name="dashboard",
    trennzeichen=";",
    kodierung="utf-8-sig",
    spalten={"name": "Kategorie", "art": "Art", "mandant": "Mandant"},
    arten={
        "einnahme": Kategorie.Art.EINNAHME,
        "einnahmen": Kategorie.Art.EINNAHME,
        "ertrag": Kategorie.Art.EINNAHME,
        "ausgabe": Kategorie.Art.AUSGABE,
        "ausgaben": Kategorie.Art.AUSGABE,
        "kosten": Kategorie.Art.AUSGABE,
        "aufwand": Kategorie.Art.AUSGABE,
        "entnahme": Kategorie.Art.ENTNAHME,
        "entnahmen": Kategorie.Art.ENTNAHME,
        "umbuchung": Kategorie.Art.UMBUCHUNG,
        "umbuchungen": Kategorie.Art.UMBUCHUNG,
        "transfer": Kategorie.Art.UMBUCHUNG,
    },
)

FORMATE: dict[str, KategorienFormat] = {KATEGORIEN_CSV.name: KATEGORIEN_CSV}
