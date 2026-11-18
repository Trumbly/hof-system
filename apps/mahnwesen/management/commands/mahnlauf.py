"""Lauf ``mahnlauf`` (H4.1 bis H4.3, Standardablauf 3): montags im Tageslauf, nach dem Abgleich.

Schreibt die Mahnfälle fort und legt je fälliger Stufe ein Schreiben im Status „bereit“ und
einen Arbeitsposten „Mahnungen senden“ an (ohne freigegebene Vorlage nur den Arbeitsposten:
Probebetrieb). Keine Stufe, bevor die vorige belegt ist; Sperren halten Fälle an.
Idempotent: Ein zweiter Lauf am selben Stichtag ändert nichts.
"""

from __future__ import annotations

import datetime
from typing import Any

from django.core.management.base import CommandParser

from apps.kern.laeufe import LaufBefehl
from apps.mahnwesen import services


class Command(LaufBefehl):
    help = (
        "Schreibt die Mahnfälle fort; fällige Stufen werden Schreiben „bereit“ mit einem "
        "Arbeitsposten „Mahnungen senden“."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--stichtag",
            type=datetime.date.fromisoformat,
            help="Stichtag des Laufs (JJJJ-MM-TT); Standard heute",
        )

    def ausfuehren(self, **optionen: Any) -> str:
        ergebnis = services.mahnlauf(optionen.get("stichtag"), lauf=self.lauf)
        self.gebucht(ergebnis.schreiben)
        self.koerbe.arbeitsposten += ergebnis.arbeitsposten
        return ergebnis.text()
