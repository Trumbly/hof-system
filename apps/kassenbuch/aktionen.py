"""Aktionen an den Arbeitsposten des Kassenbuchs (``apps.kern.aktionen``).

„Umsätze einordnen“: Zuordnen, Aufteilen und Regel anlegen führen auf ihre Seite (dort
wählt man Kategorie, Objekt und Mandant); Zurückstellen ist die allgemeine Aktion des Kerns.
„Monatsabschluss offen“: Erneut prüfen versucht den Abschluss sofort noch einmal.
„Belege fehlen“: Belege prüfen sieht nach, ob die Belege inzwischen da sind (dann erledigt).
"""

from __future__ import annotations

from django.urls import reverse

from apps.kassenbuch import auswertung, services
from apps.kassenbuch.models import Monatsabschluss
from apps.kern import aktionen
from apps.kern.models import Arbeitsposten, Benutzer


def _zuordnen(posten: Arbeitsposten) -> str:
    return reverse("kassenbuch:zuordnen", args=[posten.bezug_id])


def _aufteilen(posten: Arbeitsposten) -> str:
    return reverse("kassenbuch:aufteilen", args=[posten.bezug_id])


def _regel_anlegen(posten: Arbeitsposten) -> str:
    return reverse("kassenbuch:regel_aus_umsatz", args=[posten.bezug_id])


def _abschluss_pruefen(posten: Arbeitsposten, benutzer: Benutzer) -> object:
    abschluss = Monatsabschluss.objects.get(pk=posten.bezug_id)
    ergebnis = services.monat_abschliessen(abschluss.mandant, abschluss.monat)
    if ergebnis.neu_abgeschlossen and auswertung.monat_abgeschlossen(abschluss.monat):
        services.monatsberichte_erzeugen(abschluss.monat)
    return ergebnis


def _belege_pruefen(posten: Arbeitsposten, benutzer: Benutzer) -> object:
    abschluss = Monatsabschluss.objects.get(pk=posten.bezug_id)
    return services.belege_erneut_pruefen(abschluss)


aktionen.registrieren("umsatz_zuordnen", "Zuordnen", seite=_zuordnen)
aktionen.registrieren("umsatz_aufteilen", "Aufteilen", seite=_aufteilen)
aktionen.registrieren("regel_anlegen", "Regel anlegen", seite=_regel_anlegen)
aktionen.registrieren("abschluss_pruefen", "Erneut prüfen", _abschluss_pruefen)
aktionen.registrieren("belege_pruefen", "Belege prüfen", _belege_pruefen)
