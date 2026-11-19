"""Zahlen und Beträge in deutscher Schreibweise (Review 1, Punkt 11).

Die eine Stelle für das Format: Seiten (Filter ``|euro`` und ``|zahl``, eingebaut in jede
Vorlage, siehe ``apps.kern.templatetags.formate``) und Vorlagen an Mieter
(``apps.dokumente.platzhalter``) rufen diese Funktionen.

- ``euro(209533.7)`` → ``209.533,70 €`` (immer zwei Stellen, kaufmännisch gerundet; vor
  dem „€“ ein geschütztes Leerzeichen, wie bisher in den Schreiben)
- ``zahl(Decimal("9602.7"))`` → ``9.602,7`` (Stellen wie im Wert, oder fest mit ``stellen``)

Was keine Zahl ist (etwa ‹betrag.offen› in der Vorschau einer Vorlage), kommt unverändert
als Text zurück; ``None`` und ``""`` werden zu ``""``.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

TAUSENDER = "."
KOMMA = ","
#: Zwischen Betrag und „€“ ein geschütztes Leerzeichen: Der Betrag bricht nie um.
VOR_EURO = "\u00a0"


def _tausender(ganz: str) -> str:
    gruppen: list[str] = []
    while len(ganz) > 3:
        gruppen.insert(0, ganz[-3:])
        ganz = ganz[:-3]
    gruppen.insert(0, ganz)
    return TAUSENDER.join(gruppen)


def _dezimal(wert: Any) -> Decimal | None:
    if isinstance(wert, bool):
        return None
    if isinstance(wert, Decimal):
        return wert if wert.is_finite() else None
    try:
        zahl = Decimal(str(wert).strip())
    except (InvalidOperation, ValueError):
        return None
    return zahl if zahl.is_finite() else None


def zahl(wert: Any, stellen: int | None = None) -> str:
    """Zahl mit Tausenderpunkt und Komma: ``9602.7`` → ``9.602,7``.

    Ohne ``stellen`` bleiben die Nachkommastellen des Werts (``Decimal("12.50")`` →
    ``12,50``); mit ``stellen`` wird kaufmännisch gerundet.
    """
    if wert is None or wert == "":
        return ""
    betrag = _dezimal(wert)
    if betrag is None:
        return str(wert)
    if stellen is not None:
        betrag = betrag.quantize(Decimal(1).scaleb(-stellen), rounding=ROUND_HALF_UP)
    vorzeichen = "-" if betrag < 0 else ""
    text = f"{abs(betrag):f}"
    ganz, _, rest = text.partition(".")
    ergebnis = _tausender(ganz)
    if rest:
        ergebnis = f"{ergebnis}{KOMMA}{rest}"
    if vorzeichen and set(ergebnis) <= {"0", TAUSENDER, KOMMA}:
        vorzeichen = ""  # kein „-0,00“
    return f"{vorzeichen}{ergebnis}"


def euro(wert: Any) -> str:
    """Betrag in Euro: ``1234.5`` → ``1.234,50 €``."""
    if wert is None or wert == "":
        return ""
    if _dezimal(wert) is None:
        return str(wert)
    return f"{zahl(wert, 2)}{VOR_EURO}€"
