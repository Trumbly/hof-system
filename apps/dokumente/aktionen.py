"""Aktionen und Links an den Arbeitsposten der Dokumente (``apps.kern.aktionen``, ``verweise``).

„Belege zuordnen“: „Zuordnen“ führt auf die Seite des Belegs; dort trägt man Lieferant,
Betrag, Objekt, Kostenart, Leistungsjahr und umlagefähigen Anteil ein und bestätigt.
„Rechnung zahlen“ und „Rechnung zuordnen“: „Ansehen“ führt auf die Seite der Rechnung (mit
Beleg, Abbuchungen zur Auswahl und „strittig“); der Link am Posten führt zum Beleg.
"""

from __future__ import annotations

import datetime

from django.urls import reverse

from apps.dokumente.models import Beleg, Dokument, Eingangsrechnung, Lieferant, Schreiben
from apps.kern import aktionen, verweise
from apps.kern.models import Arbeitsposten


def _beleg_seite(posten: Arbeitsposten) -> str:
    return reverse("dokumente:beleg", args=[posten.bezug_id])


def _rechnung_seite(posten: Arbeitsposten) -> str:
    return reverse("dokumente:rechnung", args=[posten.bezug_id])


aktionen.registrieren("beleg_zuordnen", "Zuordnen", seite=_beleg_seite)
aktionen.registrieren("rechnung_ansehen", "Ansehen", seite=_rechnung_seite)


def _rechnung_text(lieferant: Lieferant | None, nummer: str, datum: datetime.date) -> str:
    """„Zänker AG · Rechnung 4779“ (Review 3, Punkt 8); ohne Nummer „Beleg vom …“, ohne
    erkannten Lieferanten nur das."""
    gegenstand = f"Rechnung {nummer}" if nummer else f"Beleg vom {datum:%d.%m.%Y}"
    return f"{lieferant.name} · {gegenstand}" if lieferant else gegenstand


def _beleg_text(beleg: Beleg) -> str:
    return _rechnung_text(beleg.lieferant, beleg.rechnungsnummer, beleg.dokument.datum)


def _eingangsrechnung_text(rechnung: Eingangsrechnung) -> str:
    beleg = rechnung.beleg
    nummer = rechnung.rechnungsnummer or beleg.rechnungsnummer
    return _rechnung_text(beleg.lieferant, nummer, beleg.dokument.datum)


verweise.registrieren(
    Beleg,
    "Beleg ansehen",
    lambda b: reverse("dokumente:beleg", args=[b.pk]),
    beschreibung=_beleg_text,
)
verweise.registrieren(
    Dokument,
    "Dokument ansehen",
    lambda d: reverse("dokumente:dokument", args=[d.pk]),
    beschreibung=lambda d: f"{d.original or 'Dokument'} vom {d.datum:%d.%m.%Y}",
)
verweise.registrieren(
    Eingangsrechnung,
    "Zum Beleg",
    lambda r: reverse("dokumente:beleg", args=[r.beleg_id]),
    beschreibung=_eingangsrechnung_text,
)
# Review 1, Punkt 8: Jeder Posten verlinkt auf seinen Bezug, auch ein Schreiben.
verweise.registrieren(
    Schreiben,
    "Schreiben ansehen",
    lambda s: reverse("dokumente:schreiben", args=[s.pk]),
    beschreibung=lambda s: s.betreff or s.get_art_display(),
)
