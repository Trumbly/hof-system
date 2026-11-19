"""Links an den Arbeitsposten der Sollstellung (``apps.kern.verweise``)."""

from __future__ import annotations

from django.urls import reverse

from apps.kern import verweise
from apps.soll.models import Sollposten


def _posten(posten: Sollposten) -> str:
    return f"{reverse('soll:vertrag', args=[posten.vertrag_id])}#posten-{posten.pk}"


def _posten_text(posten: Sollposten) -> str:
    """Vertrag und Posten (nur Oberfläche), etwa „V-0005 · NW-G-0018 · … · Miete 11/2026“."""
    from apps.stammdaten.verweise import vertrag_text

    return " · ".join(t for t in (vertrag_text(posten.vertrag), posten.bezeichnung) if t)


verweise.registrieren(Sollposten, "Offene Posten des Vertrags", _posten, beschreibung=_posten_text)
