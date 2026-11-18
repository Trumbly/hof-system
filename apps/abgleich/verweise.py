"""Links an den Arbeitsposten des Abgleichs (``apps.kern.verweise``)."""

from __future__ import annotations

from django.urls import reverse

from apps.abgleich.models import Klaerfall
from apps.kern import verweise


def _klaerfall(klaerfall: Klaerfall) -> str:
    return reverse("abgleich:klaerfall", args=[klaerfall.pk])


verweise.registrieren(Klaerfall, "Zahlung ansehen", _klaerfall)
