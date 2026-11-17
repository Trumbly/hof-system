"""Lauf ``id_abgleich`` (täglich, H1.6): NW-IDs von Website und Verwaltio gegen das Hof-System.

Jede fehlende oder abweichende ID wird eine ``Abweichung`` mit Arbeitsposten
„ID-Abweichung“. Ist sie behoben, schließt sie sich beim nächsten Lauf. Antwortet eine
Quelle nicht, zählt das als Fehler, und ihre Abweichungen bleiben, wie sie sind. Eine
Quelle, die (noch) nicht eingerichtet ist, wird übersprungen und in der Meldung genannt.
"""

from __future__ import annotations

import datetime
from typing import Any

from django.core.management.base import CommandParser
from django.utils import timezone

from apps.anbindungen import services
from apps.anbindungen.verwaltio import (
    VerwaltioClient,
    VerwaltioNichtEingerichtet,
    verwaltio_client,
)
from apps.anbindungen.website import WebsiteClient, WebsiteNichtEingerichtet, website_client
from apps.kern.laeufe import LaufBefehl


class Command(LaufBefehl):
    help = (
        "Vergleicht die NW-IDs auf der Website und in Verwaltio mit dem Hof-System; jede "
        "fehlende oder abweichende ID wird Abweichung und Arbeitsposten „ID-Abweichung“."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--stichtag",
            type=datetime.date.fromisoformat,
            default=None,
            help="Stichtag im Format JJJJ-MM-TT (Standard: heute).",
        )

    def ausfuehren(self, **optionen: Any) -> str:
        stichtag: datetime.date = optionen.get("stichtag") or timezone.localdate()
        website: WebsiteClient | None
        verwaltio: VerwaltioClient | None
        try:
            website = website_client()
        except WebsiteNichtEingerichtet:
            website = None
        try:
            verwaltio = verwaltio_client()
        except VerwaltioNichtEingerichtet:
            verwaltio = None
        ergebnis = services.id_abgleich(
            website=website, verwaltio=verwaltio, datum=stichtag, lauf=self.lauf
        )
        self.koerbe.arbeitsposten += ergebnis.neu
        gestoert = [q for q, grund in ergebnis.fehler.items() if grund != "nicht eingerichtet"]
        self.fehler(len(gestoert))
        teile = [
            f"{quelle}: {len(e.neu)} neu, {e.weiter_offen} weiter offen, {e.behoben} behoben"
            for quelle, e in ergebnis.quellen.items()
        ]
        teile += [f"{quelle}: {grund}" for quelle, grund in ergebnis.fehler.items()]
        return f"Stichtag {stichtag:%d.%m.%Y}. " + "; ".join(teile) + "."
