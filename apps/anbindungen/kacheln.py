"""Kachel „Wohnraum“ im Hof-Cockpit (V12): Kennzahlen aus Verwaltio, ohne Namen."""

from __future__ import annotations

from typing import Any

from apps.anbindungen import services


def wohnraum_kontext() -> dict[str, Any]:
    return {"wohnraum": services.wohnraum_ueberblick()}


def anmelden() -> None:
    from apps.cockpit import kacheln

    kacheln.kachel_anmelden(
        "wohnraum", "anbindungen/_kachel_wohnraum.html", wohnraum_kontext, kacheln.WOHNRAUM
    )
