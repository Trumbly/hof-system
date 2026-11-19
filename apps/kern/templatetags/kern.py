"""Vorlagen-Helfer des Kerns."""

import re

from django import template
from django.utils.html import conditional_escape, format_html
from django.utils.safestring import SafeString, mark_safe

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


#: Ein Pfad der Anwendung in einem Hinweis, etwa „/bank/konten/3/freigabe/“ (Review 1, 8).
PFAD = re.compile(r"(?<![\w/:.])(/(?:[\w-]+/)+)(?=[\s.,;:)]|$)")


@register.filter(needs_autoescape=True)
def pfade_verlinken(text: str, autoescape: bool = True) -> SafeString:
    """Macht Pfade in Meldungen zu Links (Review 1, Punkt 8). Der übrige Text bleibt
    maskiert."""
    maske = conditional_escape if autoescape else (lambda t: t)
    teile = []
    letzte = 0
    for treffer in PFAD.finditer(str(text)):
        teile.append(maske(str(text)[letzte : treffer.start()]))
        pfad = treffer.group(1)
        teile.append(format_html('<a href="{}">{}</a>', pfad, pfad))
        letzte = treffer.end()
    teile.append(maske(str(text)[letzte:]))
    return mark_safe("".join(str(t) for t in teile))  # noqa: S308 – Teile sind maskiert
