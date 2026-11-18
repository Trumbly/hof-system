"""Befehl ``statusmcp_benutzer``: Login-Benutzer des Status-MCP anlegen (einmalig, auf dem Server).

Max führt ihn auf dem Server aus (siehe docs/betrieb.md, Status-MCP). Das Passwort kommt aus
einer Datei (erste Zeile; ``-`` liest von der Standardeingabe), nie von der Kommandozeile,
und steht in keiner Ausgabe. Ein zweiter Aufruf setzt das Passwort neu.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import connection

from apps.statusmcp import rechte, services

STANDARDNAME = "hof_statusmcp_leser"


class Command(BaseCommand):
    help = (
        "Legt den Login-Benutzer des Status-MCP an (oder setzt sein Passwort neu). Er bekommt "
        f"nur die Rolle {rechte.ROLLE}: SELECT auf die Sichten sm_*."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--name", default=STANDARDNAME, help=f"Standard: {STANDARDNAME}")
        parser.add_argument(
            "--passwort-datei",
            required=True,
            help="Datei mit dem Passwort in der ersten Zeile (mindestens 24 Zeichen); "
            "„-“ liest von der Standardeingabe.",
        )

    def handle(self, *args: Any, **optionen: Any) -> None:
        pfad: str = optionen["passwort_datei"]
        try:
            text = sys.stdin.read() if pfad == "-" else Path(pfad).read_text(encoding="utf-8")
        except OSError as fehler:
            raise CommandError(f"Passwortdatei nicht lesbar: {pfad}") from fehler
        zeilen = text.splitlines()
        passwort = zeilen[0].strip() if zeilen else ""
        name: str = optionen["name"]
        try:
            neu = services.lesebenutzer_anlegen(name, passwort)
        except services.LesebenutzerFehler as fehler:
            raise CommandError(str(fehler)) from fehler
        datenbank = connection.settings_dict["NAME"]
        self.stdout.write(
            f"Lese-Benutzer {name} {'angelegt' if neu else 'aktualisiert'} "
            f"(Rolle {rechte.ROLLE}).\n"
            f"Für den Status-MCP: HOF_STATUSMCP_DATABASE_URL="
            f"postgres://{name}:<passwort>@db:5432/{datenbank}"
        )
