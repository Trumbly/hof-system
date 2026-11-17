"""Kleine Bausteine der Sollstellung für andere Seiten, z. B. den Link auf der Vertragsseite."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django import template

from apps.soll import services
from apps.stammdaten.models import Vertrag

register = template.Library()


@register.inclusion_tag("soll/_offene_posten_link.html")
def offene_posten_link(vertrag: Vertrag) -> dict[str, Any]:
    """Link „Offene Posten“ mit Anzahl und Summe für die Vertragsseite der Stammdaten."""
    posten = list(services.offene_posten(vertrag))
    return {
        "vertrag": vertrag,
        "anzahl": len(posten),
        "summe": sum((p.offen for p in posten), Decimal("0.00")),
    }
