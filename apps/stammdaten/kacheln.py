"""Kacheln „Leerstand“ und „Datenstand“ im Hof-Cockpit (H2.11, Datenstand)."""

from __future__ import annotations

import datetime
from typing import Any

from apps.kern import zeit
from apps.stammdaten import datenstand, leerstand

#: Zeitraum für den entgangenen Ertrag (wie die Seite „Leerstand“).
LEERSTAND_TAGE = 365


def leerstand_kontext() -> dict[str, Any]:
    heute = zeit.heute()
    zeilen = leerstand.berechnen(heute - datetime.timedelta(days=LEERSTAND_TAGE - 1), heute)
    frei = sorted(z.einheit.nw_id for z in zeilen if z.frei_seit is not None)
    return {
        "frei": frei[:8],
        "frei_anzahl": len(frei),
        "entgangen": leerstand.summe(zeilen),
        "tage": LEERSTAND_TAGE,
    }


def datenstand_kontext() -> dict[str, Any]:
    bereiche = [
        {"titel": stand.titel, "luecken": stand.anzahl_luecken}
        for stand in datenstand.ermitteln(zeit.heute())
    ]
    return {
        "bereiche": [b for b in bereiche if b["luecken"]],
        "summe": sum(b["luecken"] for b in bereiche),
    }


def anmelden() -> None:
    from apps.cockpit import kacheln

    kacheln.kachel_anmelden(
        "leerstand", "stammdaten/_kachel_leerstand.html", leerstand_kontext, kacheln.LEERSTAND
    )
    kacheln.kachel_anmelden(
        "datenstand", "stammdaten/_kachel_datenstand.html", datenstand_kontext, kacheln.DATENSTAND
    )
