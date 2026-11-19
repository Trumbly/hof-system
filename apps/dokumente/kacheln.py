"""Kacheln „Belege zuordnen“ und „Eingangsrechnungen“ im Hof-Cockpit (Schritt 3a, 3b).

Nur Zähler, keine Lieferanten, keine Beträge.
"""

from __future__ import annotations

import datetime
from typing import Any

from django.utils import timezone

from apps.dokumente.models import Beleg, Eingangsrechnung

TAGE = 14


def belege_kontext() -> dict[str, Any]:
    return {"offen": Beleg.objects.filter(status=Beleg.Status.OFFEN).count()}


def rechnungen_kontext() -> dict[str, Any]:
    heute = timezone.localdate()
    offen = Eingangsrechnung.objects.exclude(status=Eingangsrechnung.Status.BEZAHLT)
    return {
        "offen": offen.count(),
        "strittig": offen.filter(status=Eingangsrechnung.Status.STRITTIG).count(),
        "bald": offen.filter(
            faellig_am__gte=heute, faellig_am__lte=heute + datetime.timedelta(days=TAGE)
        ).count(),
        "ueberfaellig": offen.filter(faellig_am__lt=heute).count(),
        "tage": TAGE,
    }


def anmelden() -> None:
    from apps.cockpit import kacheln

    kacheln.kachel_anmelden(
        "belege",
        "dokumente/_kachel_belege.html",
        belege_kontext,
        kacheln.BELEGE,
        # Die Kachel zeigt die offenen Belege; „Offene Freigaben“ nennt sie nicht noch einmal.
        arten=("Belege zuordnen",),
    )
    kacheln.kachel_anmelden(
        "eingangsrechnungen",
        "dokumente/_kachel_eingangsrechnungen.html",
        rechnungen_kontext,
        kacheln.EINGANGSRECHNUNGEN,
    )
