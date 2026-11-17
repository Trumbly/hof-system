"""Lauf: Versandstand der Briefe bei LetterXpress abrufen (Sendungsnummer, Einlieferung,
Zustellung). ``manage.py versandstand_abrufen``

Für jedes gesendete Schreiben per Brief oder Einwurf-Einschreiben fragt der Lauf den
Auftrag ab. Meldet die Sendungsverfolgung „Zugestellt“, ist das Schreiben mit diesem
Vermerk als Zustellnachweis erledigt. Scheitert eine Abfrage, zählt sie als Fehler; die
anderen laufen weiter.
"""

from __future__ import annotations

from typing import Any

from django.db import transaction

from apps.dokumente import services
from apps.dokumente.models import Schreiben
from apps.dokumente.versand import VersandFehler
from apps.dokumente.vorlagenarten import Versandweg
from apps.kern.laeufe import LaufBefehl


class Command(LaufBefehl):
    help = "Holt Sendungsnummer, Einlieferung und Zustellung der Briefe bei LetterXpress."
    lauf_name = "versandstand_abrufen"

    def ausfuehren(self, **optionen: Any) -> str:
        offen = list(
            Schreiben.objects.filter(
                status=Schreiben.Status.GESENDET,
                versandweg__in=[Versandweg.BRIEF, Versandweg.EINWURF_EINSCHREIBEN],
            ).order_by("pk")
        )
        fehlerhaft: list[int] = []
        for schreiben in offen:
            try:
                with transaction.atomic():
                    if services.versandstand_abrufen(schreiben):
                        self.gebucht()
            except (VersandFehler, services.SchreibenFehler):
                self.fehler()
                fehlerhaft.append(schreiben.pk)
        meldung = f"{len(offen)} Briefe unterwegs geprüft"
        if fehlerhaft:
            meldung += f"; Fehler bei Schreiben {', '.join(map(str, fehlerhaft))}"
        return meldung
