"""Gemeinsame Optionen der Import-Läufe: ``--probelauf`` (Standard) und ``--ausfuehren``."""

from __future__ import annotations

import datetime
from typing import Any

from django.core.management.base import CommandError, CommandParser

from apps.kern.laeufe import LaufBefehl
from apps.stammdaten.importe import Bericht


class ImportBefehl(LaufBefehl):
    def add_arguments(self, parser: CommandParser) -> None:
        modus = parser.add_mutually_exclusive_group()
        modus.add_argument(
            "--probelauf",
            action="store_true",
            help="Nur prüfen und berichten, nichts ändern (Standard).",
        )
        modus.add_argument(
            "--ausfuehren", action="store_true", help="Änderungen tatsächlich übernehmen."
        )
        parser.add_argument(
            "--stichtag",
            type=datetime.date.fromisoformat,
            default=None,
            help="Datum im Format JJJJ-MM-TT (Standard: heute).",
        )

    def bericht_ausgeben(self, bericht: Bericht) -> str:
        self.gebucht(bericht.zaehler.get("neu", 0) + bericht.zaehler.get("vertraege_neu", 0))
        self.fehler(bericht.uebersprungen)
        text = bericht.text()
        self.stdout.write(text)
        return text

    @staticmethod
    def pfad_pruefen(pfad: Any) -> None:
        if not pfad.is_file():
            raise CommandError(f"Datei nicht gefunden: {pfad.name}")
