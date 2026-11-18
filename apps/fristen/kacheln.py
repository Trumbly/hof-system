"""Kachel „Fristen“ im Hof-Cockpit: offene Fristen der nächsten 14 Tage, auch überfällige."""

from __future__ import annotations

import datetime
from typing import Any

from django.utils import timezone

from apps.fristen import services

TAGE = 14


def fristen_kontext() -> dict[str, Any]:
    heute = timezone.localdate()
    fristen = list(
        services.offene_fristen()
        .filter(datum__lte=heute + datetime.timedelta(days=TAGE))
        .order_by("datum", "pk")
    )
    return {
        "anzahl": len(fristen),
        "ueberfaellig": sum(1 for f in fristen if f.datum < heute),
        "naechste": fristen[:5],
        "heute": heute,
        "tage": TAGE,
    }


def anmelden() -> None:
    from apps.cockpit import kacheln

    kacheln.kachel_anmelden(
        "fristen", "fristen/_kachel_fristen.html", fristen_kontext, kacheln.FRISTEN
    )
