"""Vorlagen-Helfer der Anbindungen: die Kachel „Wohnraum“ für Startseite und Cockpit."""

from typing import Any

from django import template

from apps.anbindungen import services

register = template.Library()


@register.inclusion_tag("anbindungen/_kachel_wohnraum.html")
def kachel_wohnraum() -> dict[str, Any]:
    """Kachel „Wohnraum“ aus den Kennzahlen von Verwaltio: nur Zähler und Summen."""
    return {"wohnraum": services.wohnraum_ueberblick()}
