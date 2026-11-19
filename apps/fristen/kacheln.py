"""Kachel „Fristen“ im Hof-Cockpit: überfällige Fristen und die der nächsten 14 Tage als
zwei Listen (Review 1, Punkt 6)."""

from __future__ import annotations

import datetime
from typing import Any

from django.utils import timezone

from apps.fristen import services

TAGE = 14
#: Höchstens so viele Zeilen je Liste; der Rest hinter dem Link auf die Fristen.
ZEILEN = 5


def fristen_kontext() -> dict[str, Any]:
    heute = timezone.localdate()
    fristen = list(
        services.offene_fristen()
        .filter(datum__lte=heute + datetime.timedelta(days=TAGE))
        .order_by("datum", "pk")
    )
    ueberfaellig = [f for f in fristen if f.datum < heute]
    bald = [f for f in fristen if f.datum >= heute]
    return {
        "anzahl": len(fristen),
        "ueberfaellig": len(ueberfaellig),
        "ueberfaellige": ueberfaellig[:ZEILEN],
        "bald": bald[:ZEILEN],
        "bald_anzahl": len(bald),
        "naechste": fristen[:ZEILEN],
        "heute": heute,
        "tage": TAGE,
    }


def anmelden() -> None:
    from apps.cockpit import kacheln

    kacheln.kachel_anmelden(
        "fristen", "fristen/_kachel_fristen.html", fristen_kontext, kacheln.FRISTEN
    )
