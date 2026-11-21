"""Vorlagen-Helfer der Seite „Läufe“ (Review 2, B16)."""

from __future__ import annotations

from django import template
from django.utils.safestring import SafeString

from apps.kern import laufnamen, meldungen

register = template.Library()


@register.filter
def laufname(name: str) -> str:
    """Anzeigename eines Laufs aus seinem technischen Namen (``apps.kern.laufnamen``)."""
    return laufnamen.anzeigename(name)


@register.filter
def verlinkt(text: str) -> SafeString:
    """Lauf-Meldung mit Links zu den betroffenen Posten: „Arbeitsposten #12“ wird ein Link."""
    return meldungen.verlinken(text)


@register.simple_tag
def mit_bestaetigung() -> bool:
    """In prod startet „jetzt ausführen“ erst nach dem Bestätigungsdialog (Review 2, B16)."""
    from apps.kern.laeufe_seite import ist_produktion

    return ist_produktion()
