"""Umsatz einordnen (je Umsatz): ein Umsatz „Material Baumarkt“ ohne passende Regel.

Weg heute: Arbeitslisten → „Zuordnen“ am Posten → Seite „Zuordnen“, Kategorie und Objekt
wählen → „Buchen“.
"""

from __future__ import annotations

from typing import Any

from tests.e2e.klickmessung.messung import Messung

NAME = "umsatz_einordnen"
TITEL = "Umsatz einordnen (je Umsatz)"

#: Alle Selektoren des Ablaufs. ``{posten}``: Arbeitsposten „Umsätze einordnen“.
SELEKTOREN = {
    "posten": "#posten-{posten}",
    "zuordnen": "#posten-{posten} button:has-text('Zuordnen')",
    "kategorie": "select[name=kategorie]",
    "objekt": "select[name=objekt]",
    "buchen": "main button:has-text('Buchen')",
}
#: Richtige Einordnung (testhof/kassenbuch.py: „Material Baumarkt“ → Material, HOF)
KATEGORIE = r"^Material\b"
OBJEKT = r"^HOF\b"


def durchspielen(m: Messung, adressen: dict[str, Any]) -> None:
    s = {k: v.format(posten=adressen["posten"]["umsatz"]) for k, v in SELEKTOREN.items()}
    m.beginnen(adressen["arbeitslisten"])
    m.klick(s["zuordnen"])
    m.waehle(s["kategorie"], KATEGORIE)
    m.waehle(s["objekt"], OBJEKT)
    m.klick(s["buchen"])
    m.anhalten()
    # Prüfen (zählt nicht): Der Posten ist aus der Arbeitsliste verschwunden.
    m.seite.goto(adressen["arbeitslisten"])
    assert not m.gibt_es(s["posten"]), "Der Umsatz steht noch in der Arbeitsliste."
