"""Aktion und Link an „Mahnungen senden“ (``apps.kern.aktionen``, ``apps.kern.verweise``).

„Ansehen“ führt auf die Seite des Mahnfalls: Dort stehen Stufenverlauf, Sperren und das
Schreiben mit Vorschau und Senden-Knopf (in der App dokumente).
"""

from __future__ import annotations

from django.urls import reverse

from apps.kern import aktionen, verweise
from apps.kern.models import Arbeitsposten
from apps.mahnwesen.models import Mahnfall
from apps.stammdaten.verweise import vertrag_text


def _fall_seite(posten: Arbeitsposten) -> str:
    return reverse("mahnwesen:fall", args=[posten.bezug_id])


aktionen.registrieren("mahnfall_ansehen", "Ansehen", seite=_fall_seite)
verweise.registrieren(
    Mahnfall,
    "Mahnfall ansehen",
    lambda f: reverse("mahnwesen:fall", args=[f.pk]),
    beschreibung=lambda f: vertrag_text(f.vertrag),
)
