"""Kachel „Offene Posten“ im Hof-Cockpit: Zahl, Summe und ihre Aufteilung.

Alle Zahlen kommen aus ``services.aufteilung()`` (Review 3, Punkt 1): offen = überfällig +
nicht prüfbar + noch nicht fällig. Die Kachel Rückstand, die Seite Offene Posten und die
Seite Rückstände lesen dieselbe Aufteilung.
"""

from __future__ import annotations

from typing import Any

from apps.soll import services


def offene_posten_kontext() -> dict[str, Any]:
    aufteilung = services.aufteilung()
    return {
        **aufteilung.kontext(),
        "anzahl": aufteilung.offen.anzahl,
        "summe": aufteilung.offen.summe,
    }


def anmelden() -> None:
    from apps.cockpit import kacheln

    kacheln.kachel_anmelden(
        "offene_posten",
        "soll/_kachel_offene_posten.html",
        offene_posten_kontext,
        kacheln.OFFENE_POSTEN,
    )
