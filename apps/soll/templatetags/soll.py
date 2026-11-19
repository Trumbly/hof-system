"""Kleine Bausteine der Sollstellung für andere Seiten, z. B. den Link auf der Vertragsseite."""

from __future__ import annotations

from typing import Any

from django import template

from apps.soll import services
from apps.stammdaten.models import Vertrag

register = template.Library()


@register.inclusion_tag("soll/_offene_posten_link.html")
def offene_posten_link(vertrag: Vertrag) -> dict[str, Any]:
    """Link „Offene Posten“ mit Anzahl und Summe für den Vertragskopf der Stammdaten.

    Zahlen aus ``services.offen(vertrag)`` (Review 1, Punkt 1), dazu der Rückstand (vor heute
    fällig) wie in Cockpit, Rückständen und Mahnwesen."""
    alle = services.offen(vertrag)
    rueckstand = services.offen(vertrag, services.heute())
    return {
        "vertrag": vertrag,
        "anzahl": alle.anzahl,
        "summe": alle.summe_offen,
        "rueckstand": rueckstand.summe_offen,
    }
