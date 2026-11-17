"""Vorlagen-Helfer des Kerns."""

from django import template

from apps.kern import aktionen
from apps.kern.models import Arbeitsposten

register = template.Library()


@register.simple_tag
def posten_aktionen(posten: Arbeitsposten) -> list[aktionen.Aktion]:
    """Die Aktionen, die ein Arbeitsposten in seinem Status anbietet."""
    return aktionen.fuer_posten(posten)
