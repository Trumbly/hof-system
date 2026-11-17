"""Aktionen und Links an den Arbeitsposten der Dokumente (``apps.kern.aktionen``, ``verweise``).

„Belege zuordnen“: „Zuordnen“ führt auf die Seite des Belegs; dort trägt man Lieferant,
Betrag, Objekt, Kostenart, Leistungsjahr und umlagefähigen Anteil ein und bestätigt.
"""

from __future__ import annotations

from django.urls import reverse

from apps.dokumente.models import Beleg, Dokument
from apps.kern import aktionen, verweise
from apps.kern.models import Arbeitsposten


def _beleg_seite(posten: Arbeitsposten) -> str:
    return reverse("dokumente:beleg", args=[posten.bezug_id])


aktionen.registrieren("beleg_zuordnen", "Zuordnen", seite=_beleg_seite)

verweise.registrieren(Beleg, "Beleg ansehen", lambda b: reverse("dokumente:beleg", args=[b.pk]))
verweise.registrieren(
    Dokument, "Dokument ansehen", lambda d: reverse("dokumente:dokument", args=[d.pk])
)
