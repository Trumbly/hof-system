"""Kachel „Offene Posten“ im Hof-Cockpit: Zahl, Summe und überfällige Sollposten.

Alle Zahlen kommen aus ``services.offen()`` (Review 1, Punkt 1): „überfällig“ ist dieselbe
Menge wie auf der Seite Offene Posten mit Filter „überfällig“ und wie die Rückstände
(geprüft und nicht prüfbar zusammen).
"""

from __future__ import annotations

from typing import Any

from apps.soll import services


def offene_posten_kontext() -> dict[str, Any]:
    alle = services.offen()
    ueberfaellig = services.offen(stichtag=services.heute())
    return {
        "anzahl": alle.anzahl,
        "summe": alle.summe_offen,
        "ueberfaellig": ueberfaellig.anzahl,
        "summe_ueberfaellig": ueberfaellig.summe_offen,
    }


def anmelden() -> None:
    from apps.cockpit import kacheln

    kacheln.kachel_anmelden(
        "offene_posten",
        "soll/_kachel_offene_posten.html",
        offene_posten_kontext,
        kacheln.OFFENE_POSTEN,
    )
