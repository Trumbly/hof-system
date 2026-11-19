"""Klärfall zuordnen: Der Zweck nennt NW-S-0006, die Zahlerregel V-0010 (NW-S-0001).

Richtig ist der Vertrag der Einheit aus dem Zweck, V-0011 (testhof/abgleich.py,
``falsche_einheit_im_verwendungszweck``). Weg heute: Arbeitslisten → „Zuordnen“ am Posten →
Vertrag in der Auswahlliste wählen → „Zuordnen“.
"""

from __future__ import annotations

from typing import Any

from tests.e2e.klickmessung.messung import Messung

NAME = "klaerfall_zuordnen"
TITEL = "Klärfall zuordnen"

#: Alle Selektoren des Ablaufs. ``{posten}``: Arbeitsposten „Zahlungen klären“.
SELEKTOREN = {
    "posten": "#posten-{posten}",
    "zuordnen": "#posten-{posten} button:has-text('Zuordnen')",
    "vertrag": "select[name=vertrag]",
    "absenden": "main form button:has-text('Zuordnen')",
}
VERTRAG = r"^V-0011\b"


def durchspielen(m: Messung, adressen: dict[str, Any]) -> None:
    s = {k: v.format(posten=adressen["posten"]["klaerfall"]) for k, v in SELEKTOREN.items()}
    m.beginnen(adressen["arbeitslisten"])
    m.klick(s["zuordnen"])
    m.waehle(s["vertrag"], VERTRAG)
    m.klick(s["absenden"])
    m.anhalten()
    m.seite.goto(adressen["arbeitslisten"])
    assert not m.gibt_es(s["posten"]), "Der Klärfall steht noch in der Arbeitsliste."
