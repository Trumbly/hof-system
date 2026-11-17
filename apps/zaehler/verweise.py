"""Link an Arbeitsposten mit Bezug auf einen Zähler (z. B. „Ablesung fehlt“)."""

from __future__ import annotations

from django.urls import reverse

from apps.kern import verweise
from apps.zaehler.models import Zaehler


def _ablesen(zaehler: Zaehler) -> str:
    return reverse("zaehler:ablesung", args=[zaehler.standort_objekt.pk]) + f"#zaehler-{zaehler.pk}"


verweise.registrieren(Zaehler, "Ablesen", _ablesen)
