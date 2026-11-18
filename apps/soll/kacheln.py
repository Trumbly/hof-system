"""Kachel „Offene Posten“ im Hof-Cockpit: Zahl, Summe und überfällige Sollposten."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from apps.soll import services


def offene_posten_kontext() -> dict[str, Any]:
    heute = services.heute()
    posten = services.offene_posten()
    liste = list(posten)
    return {
        "anzahl": len(liste),
        "summe": sum((p.offen for p in liste), Decimal("0.00")),
        "ueberfaellig": sum(1 for p in liste if p.faellig_am < heute),
    }


def anmelden() -> None:
    from apps.cockpit import kacheln

    kacheln.kachel_anmelden(
        "offene_posten",
        "soll/_kachel_offene_posten.html",
        offene_posten_kontext,
        kacheln.OFFENE_POSTEN,
    )
