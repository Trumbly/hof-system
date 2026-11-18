"""Die Kacheln des Cockpits selbst: Freigaben, Läufe, Sicherung, Platzhalter „Rückstände“.

Alle lesen bei jedem Aufruf direkt aus der Datenbank (bzw. der Statusdatei der Sicherung).
"""

from __future__ import annotations

from typing import Any

from django.db.models import Count
from django.urls import reverse
from django.utils.http import urlencode

from apps.cockpit import kacheln, services
from apps.kern.models import Arbeitsposten, Lauf

GESCHEITERT = (Lauf.Status.ABGEBROCHEN, Lauf.Status.MIT_FEHLERN)


def freigaben() -> dict[str, Any]:
    """Offene Arbeitsposten je Art, jede Art mit Link auf ihre Arbeitsliste."""
    liste = reverse("kern:arbeitslisten")
    arten = [
        {
            "art": zeile["art"],
            "anzahl": zeile["anzahl"],
            "url": f"{liste}?{urlencode({'art': zeile['art']})}",
        }
        for zeile in Arbeitsposten.objects.filter(status=Arbeitsposten.Status.OFFEN)
        .values("art")
        .annotate(anzahl=Count("pk"))
        .order_by("-anzahl", "art")
    ]
    return {"arten": arten, "summe": sum(a["anzahl"] for a in arten)}


def laeufe() -> dict[str, Any]:
    """Letzter Lauf je Name mit Status (Postgres: DISTINCT ON)."""
    letzte = list(Lauf.objects.order_by("name", "-gestartet_am", "-pk").distinct("name"))
    letzte.sort(key=lambda lauf: (lauf.status not in GESCHEITERT, lauf.name))
    return {
        "laeufe": letzte,
        "gescheitert": sum(1 for lauf in letzte if lauf.status in GESCHEITERT),
    }


def sicherung() -> dict[str, Any]:
    return {"sicherung": services.sicherung_lesen()}


def rueckstaende_platzhalter() -> dict[str, Any]:
    return {}


def anmelden() -> None:
    kacheln.kachel_anmelden(
        "freigaben", "cockpit/_kachel_freigaben.html", freigaben, kacheln.FREIGABEN
    )
    kacheln.kachel_anmelden(
        "rueckstaende",
        "cockpit/_kachel_rueckstaende_platzhalter.html",
        rueckstaende_platzhalter,
        kacheln.RUECKSTAENDE,
        platzhalter=True,
    )
    kacheln.kachel_anmelden("laeufe", "cockpit/_kachel_laeufe.html", laeufe, kacheln.LAEUFE)
    kacheln.kachel_anmelden(
        "sicherung", "cockpit/_kachel_sicherung.html", sicherung, kacheln.SICHERUNG
    )
