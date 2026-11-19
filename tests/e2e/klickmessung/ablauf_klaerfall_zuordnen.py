"""Klärfall zuordnen: Der Zweck nennt NW-S-0006, die Zahlerregel V-0010 (NW-S-0001).

Richtig ist der Vertrag der Einheit aus dem Zweck, V-0011 (testhof/abgleich.py,
``falsche_einheit_im_verwendungszweck``). Weg (R2-6, Review 2, B4): In der Arbeitsliste stehen
Vorschlag (V-0010, Regel) und Alternative (V-0011, Zweck) als Karten in der Zeile; ein Klick
auf „Zuordnen“ der Karte V-0011. „Zahlerregel ändern“ ist vorangehakt (zählt nicht).
"""

from __future__ import annotations

from typing import Any

from tests.e2e.klickmessung.messung import Messung

NAME = "klaerfall_zuordnen"
TITEL = "Klärfall zuordnen"

#: Alle Selektoren des Ablaufs. ``{posten}``: Arbeitsposten „Zahlungen klären“.
SELEKTOREN = {
    "posten": "#posten-{posten}",
    "zahlerregel": "#posten-{posten} input[name$='-zahlerregel']",
    "karte": "#posten-{posten} .wahl-karte[data-vertrag='{vertrag}'] button:has-text('Zuordnen')",
}
VERTRAG = "V-0011"


def durchspielen(m: Messung, adressen: dict[str, Any]) -> None:
    s = {
        k: v.format(posten=adressen["posten"]["klaerfall"], vertrag=VERTRAG)
        for k, v in SELEKTOREN.items()
    }
    m.beginnen(adressen["arbeitslisten"])
    m.haken(s["zahlerregel"])
    m.klick(s["karte"])
    m.anhalten()
    m.seite.goto(adressen["arbeitslisten"])
    assert not m.gibt_es(s["posten"]), "Der Klärfall steht noch in der Arbeitsliste."
