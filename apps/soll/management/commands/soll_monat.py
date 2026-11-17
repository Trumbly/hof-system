"""Lauf ``soll_monat`` (H3.4): am Monatsersten früh.

Stellt für alle Verträge mit Soll (aktiv, gekündigt, beendet) die Posten bis zum Ende des
laufenden Monats und gleicht vorhandene mit den Konditionen ab
(``apps.soll.services.monatslauf``). Neue Posten zählen als „gebucht“; bezahlte Posten, die
nicht mehr passen, werden Arbeitsposten „Sollposten prüfen“.

    manage.py soll_monat                          bis Ende des laufenden Monats
    manage.py soll_monat --stichtag 2026-12-01    bis Ende Dezember 2026
"""

from __future__ import annotations

import datetime
from typing import Any

from django.core.management.base import CommandParser

from apps.kern.laeufe import LaufBefehl
from apps.soll import services


class Command(LaufBefehl):
    help = "Sollstellung für alle Verträge bis zum Ende des Monats (Lauf am Monatsersten)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--stichtag",
            type=datetime.date.fromisoformat,
            default=None,
            help="Datum im Format JJJJ-MM-TT (Standard: heute).",
        )

    def ausfuehren(self, **optionen: Any) -> str:
        stichtag = optionen.get("stichtag") or services.heute()
        ergebnis = services.monatslauf(stichtag, lauf=self.lauf)
        self.gebucht(len(ergebnis.neu))
        self.koerbe.arbeitsposten += len(ergebnis.arbeitsposten)
        self.fehler(len(ergebnis.fehler))
        return f"Stichtag {stichtag:%d.%m.%Y}: {ergebnis.text()}"
