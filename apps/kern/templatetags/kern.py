"""Vorlagen-Helfer des Kerns."""

from django import template

from apps.kern import aktionen, verweise
from apps.kern.models import Arbeitsposten

register = template.Library()


@register.simple_tag
def posten_aktionen(posten: Arbeitsposten) -> list[aktionen.Aktion]:
    """Die Aktionen, die ein Arbeitsposten in seinem Status anbietet."""
    return aktionen.fuer_posten(posten)


@register.simple_tag
def posten_verweis(posten: Arbeitsposten) -> verweise.Verweis | None:
    """Der Link zur Seite, auf der man den Posten bearbeitet (falls angemeldet)."""
    return verweise.fuer_posten(posten)
