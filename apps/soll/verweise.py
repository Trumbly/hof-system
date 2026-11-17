"""Links an den Arbeitsposten der Sollstellung (``apps.kern.verweise``)."""

from __future__ import annotations

from django.urls import reverse

from apps.kern import verweise
from apps.soll.models import Sollposten


def _posten(posten: Sollposten) -> str:
    return f"{reverse('soll:vertrag', args=[posten.vertrag_id])}#posten-{posten.pk}"


verweise.registrieren(Sollposten, "Offene Posten des Vertrags", _posten)
