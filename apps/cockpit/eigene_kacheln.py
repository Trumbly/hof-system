"""Die Kacheln des Cockpits selbst: Freigaben, Läufe, Sicherung, Platzhalter „Rückstände“.

Alle lesen bei jedem Aufruf direkt aus der Datenbank (bzw. der Statusdatei der Sicherung).
"""

from __future__ import annotations

import datetime
from typing import Any

from django.db.models import Count, Min, Q
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode

from apps.cockpit import kacheln, services
from apps.kern.models import Arbeitsposten, Lauf

GESCHEITERT = (Lauf.Status.ABGEBROCHEN, Lauf.Status.MIT_FEHLERN)


#: Arten, die nicht ins Cockpit gehören: Der Selbsttest prüft nur das Lauf-Gerüst
#: (sichtbar unter Läufe und Arbeitslisten), er wartet auf keine Entscheidung.
NICHT_IM_COCKPIT = frozenset({"Selbsttest"})
#: So viele Arten zeigt die Kachel, der Rest steht hinter dem Link (Review 1, Punkt 7).
ARTEN_SICHTBAR = 5


def freigaben() -> dict[str, Any]:
    """Offene Arbeitsposten je Art, nach Dringlichkeit: Arten mit überfälligen Posten zuerst,
    dann nach der frühesten Fälligkeit, dann nach Anzahl. Arten mit eigener Kachel
    (``kacheln.eigene_arten``) und der Selbsttest fehlen."""
    heute = timezone.localdate()
    liste = reverse("kern:arbeitslisten")
    weg = kacheln.eigene_arten() | NICHT_IM_COCKPIT
    zeilen = (
        Arbeitsposten.objects.filter(status=Arbeitsposten.Status.OFFEN)
        .exclude(art__in=weg)
        .values("art")
        .annotate(
            anzahl=Count("pk"),
            naechste=Min("faellig_am"),
            ueberfaellig=Count("pk", filter=Q(faellig_am__lt=heute)),
        )
    )
    arten = sorted(
        (
            {
                "art": z["art"],
                "anzahl": z["anzahl"],
                "ueberfaellig": z["ueberfaellig"],
                "naechste": z["naechste"],
                "url": f"{liste}?{urlencode({'art': z['art']})}",
            }
            for z in zeilen
        ),
        key=lambda a: (
            not a["ueberfaellig"],
            a["naechste"] or datetime.date.max,
            -a["anzahl"],
            a["art"],
        ),
    )
    return {
        "arten": arten[:ARTEN_SICHTBAR],
        "weitere": arten[ARTEN_SICHTBAR:],
        "weitere_posten": sum(a["anzahl"] for a in arten[ARTEN_SICHTBAR:]),
        "anzahl_arten": len(arten),
        "summe": sum(a["anzahl"] for a in arten),
        "heute": heute,
    }


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
