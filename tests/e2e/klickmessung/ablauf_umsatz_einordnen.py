"""Umsatz einordnen (je Umsatz): ein Umsatz „Material Baumarkt“ ohne passende Regel.

Weg (R2-6, Review 2, B3): In der Arbeitsliste stehen Umsatz, Vorschlag und Knöpfe in einer
Zeile. Kategorie und Objekt sind vorgewählt (Vorschlag aus Zweck und Verlauf); stimmen sie,
zählt die Auswahl nicht. „Übernehmen“ bucht, ohne Seitenwechsel.
"""

from __future__ import annotations

from typing import Any

from tests.e2e.klickmessung.messung import Messung

NAME = "umsatz_einordnen"
TITEL = "Umsatz einordnen (je Umsatz)"

#: Alle Selektoren des Ablaufs. ``{posten}``: Arbeitsposten „Umsätze einordnen“.
SELEKTOREN = {
    "posten": "#posten-{posten}",
    "kategorie": "#posten-{posten} select[name$='-kategorie']",
    "objekt": "#posten-{posten} select[name$='-objekt']",
    "uebernehmen": "#posten-{posten} button:has-text('Übernehmen')",
}
#: Richtige Einordnung (testhof/kassenbuch.py: „Material Baumarkt“ → Material, HOF)
KATEGORIE = r"^Material\b"
OBJEKT = r"^HOF\b"


def durchspielen(m: Messung, adressen: dict[str, Any]) -> None:
    s = {k: v.format(posten=adressen["posten"]["umsatz"]) for k, v in SELEKTOREN.items()}
    m.beginnen(adressen["arbeitslisten"])
    m.waehle(s["kategorie"], KATEGORIE)
    m.waehle(s["objekt"], OBJEKT)
    m.klick(s["uebernehmen"])
    m.anhalten()
    # Prüfen (zählt nicht): Der Posten ist aus der Arbeitsliste verschwunden.
    m.seite.goto(adressen["arbeitslisten"])
    assert not m.gibt_es(s["posten"]), "Der Umsatz steht noch in der Arbeitsliste."
