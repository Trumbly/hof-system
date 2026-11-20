"""Links an den Arbeitsposten des Abgleichs (``apps.kern.verweise``)."""

from __future__ import annotations

from django.urls import reverse

from apps.abgleich.models import Klaerfall
from apps.kern import verweise


def _klaerfall(klaerfall: Klaerfall) -> str:
    return reverse("abgleich:klaerfall", args=[klaerfall.pk])


def _klaerfall_text(klaerfall: Klaerfall) -> str:
    """Gegenpartei und Art des Klärfalls (nur Oberfläche), wie in der Arbeitsliste."""
    umsatz = klaerfall.buchung.umsatz
    return f"{umsatz.gegenpartei or 'Zahlung ohne Gegenpartei'} · {klaerfall.get_art_display()}"


verweise.registrieren(Klaerfall, "Zahlung ansehen", _klaerfall, beschreibung=_klaerfall_text)
