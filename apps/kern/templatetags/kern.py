"""Vorlagen-Helfer des Kerns."""

from django import template
from django.utils.safestring import SafeString

from apps.kern import aktionen, meldungen, verweise
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


@register.filter
def pfade_verlinken(text: str) -> SafeString:
    """Macht Pfade und Nummern in Meldungen zu Links (Review 1, Punkt 8). Dieselbe Stelle wie
    ``verlinkt`` (``apps.kern.meldungen``); der übrige Text bleibt maskiert."""
    return meldungen.verlinken(text)


@register.filter
def verlinkt(text: str) -> SafeString:
    """Meldung mit Links (Nummern und Pfade), wie ``verlinkt`` aus ``laeufe``: Hinweise am
    Posten, Toasts und Lauf-Meldungen nutzen dieselbe Stelle (Review 3, Punkt 8)."""
    return meldungen.verlinken(text)
