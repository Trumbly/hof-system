"""Aktionen und Links an den Arbeitsposten der Anbindungen.

- „Buchungsanfrage prüfen“: Knopf „Anfrage entscheiden“ führt zur Seite der Anfrage
  (angenommen, abgelehnt, Vertrag angelegt). Die Entscheidung schließt den Posten.
- „ID-Abweichung“: Link zur Liste der Abweichungen. Der Posten schließt sich von selbst,
  sobald der nächste ``id_abgleich`` die Abweichung nicht mehr findet.
"""

from __future__ import annotations

from django.urls import reverse

from apps.anbindungen import services
from apps.anbindungen.models import Abweichung, Buchungsanfrage
from apps.kern import aktionen, verweise
from apps.kern.models import Arbeitsposten


def _seite_der_anfrage(posten: Arbeitsposten) -> str:
    return reverse("anbindungen:anfrage", args=[posten.bezug_id])


aktionen.registrieren(services.AKTION_ENTSCHEIDEN, "Anfrage entscheiden", seite=_seite_der_anfrage)


def _anfrage_text(anfrage: Buchungsanfrage) -> str:
    """„NW-G-0004 · Jochem Stumpf“, bei einer Kündigung mit dem Zusatz (Review 3, Punkt 8)."""
    teile = [anfrage.einheit.nw_id if anfrage.einheit else "", anfrage.kontakt_name]
    if anfrage.art == Buchungsanfrage.Art.KUENDIGUNG:
        teile.append(anfrage.get_art_display())
    return " · ".join(t for t in teile if t) or f"Anfrage #{anfrage.pk}"


verweise.registrieren(
    Buchungsanfrage,
    "Anfrage öffnen",
    lambda anfrage: reverse("anbindungen:anfrage", args=[anfrage.pk]),
    beschreibung=_anfrage_text,
)
verweise.registrieren(
    Abweichung,
    "Abweichungen ansehen",
    lambda abweichung: reverse("anbindungen:abweichungen") + f"#abweichung-{abweichung.pk}",
    beschreibung=lambda a: f"{a.nw_id} · {a.get_art_display()}",
)
