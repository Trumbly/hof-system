"""Lauf ``abgleich`` (H3.5): täglich im Tageslauf, nach Bankabruf und Einordnen im Kassenbuch.

Gleicht die Einnahme-Buchungen ohne Wohnraum-Merkmal mit den offenen Sollposten ab (Stufen
in ``apps.abgleich.services``). Sichere Treffer werden gebucht, der Rest wird ein
Arbeitsposten „Zahlungen klären“ mit Vorschlag und Konfidenz. Idempotent: Ein zweiter Lauf
bucht nichts doppelt und legt keinen Klärfall doppelt an.
"""

from __future__ import annotations

import datetime
from typing import Any

from django.core.management.base import CommandParser

from apps.abgleich import services
from apps.kern.laeufe import LaufBefehl


class Command(LaufBefehl):
    help = (
        "Gleicht Zahlungen mit den offenen Sollposten ab; Unsicheres kommt in die Arbeitsliste "
        "„Zahlungen klären“."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--stichtag",
            type=datetime.date.fromisoformat,
            help="Stichtag für die Verrechnung von Guthaben (JJJJ-MM-TT); Standard heute",
        )

    def ausfuehren(self, **optionen: Any) -> str:
        ergebnis = services.abgleichen(stichtag=optionen.get("stichtag"), lauf=self.lauf)
        self.gebucht(ergebnis.gebucht)
        self.koerbe.arbeitsposten += ergebnis.arbeitsposten
        self.fehler(ergebnis.fehler)
        return ergebnis.text()
