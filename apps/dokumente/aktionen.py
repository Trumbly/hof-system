"""Aktionen und Links an den Arbeitsposten der Dokumente (``apps.kern.aktionen``, ``verweise``).

„Belege zuordnen“: „Zuordnen“ führt auf die Seite des Belegs; dort trägt man Lieferant,
Betrag, Objekt, Kostenart, Leistungsjahr und umlagefähigen Anteil ein und bestätigt.
„Rechnung zahlen“ und „Rechnung zuordnen“: „Ansehen“ führt auf die Seite der Rechnung (mit
Beleg, Abbuchungen zur Auswahl und „strittig“); der Link am Posten führt zum Beleg.
"""

from __future__ import annotations

from django.urls import reverse

from apps.dokumente.models import Beleg, Dokument, Eingangsrechnung, Schreiben
from apps.kern import aktionen, verweise
from apps.kern.models import Arbeitsposten


def _beleg_seite(posten: Arbeitsposten) -> str:
    return reverse("dokumente:beleg", args=[posten.bezug_id])


def _rechnung_seite(posten: Arbeitsposten) -> str:
    return reverse("dokumente:rechnung", args=[posten.bezug_id])


aktionen.registrieren("beleg_zuordnen", "Zuordnen", seite=_beleg_seite)
aktionen.registrieren("rechnung_ansehen", "Ansehen", seite=_rechnung_seite)

verweise.registrieren(Beleg, "Beleg ansehen", lambda b: reverse("dokumente:beleg", args=[b.pk]))
verweise.registrieren(
    Dokument, "Dokument ansehen", lambda d: reverse("dokumente:dokument", args=[d.pk])
)
verweise.registrieren(
    Eingangsrechnung,
    "Zum Beleg",
    lambda r: reverse("dokumente:beleg", args=[r.beleg_id]),
)
# Review 1, Punkt 8: Jeder Posten verlinkt auf seinen Bezug, auch ein Schreiben.
verweise.registrieren(
    Schreiben, "Schreiben ansehen", lambda s: reverse("dokumente:schreiben", args=[s.pk])
)
