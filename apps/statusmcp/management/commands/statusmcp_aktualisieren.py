"""Lauf ``statusmcp_aktualisieren`` (Tageslauf, Rang 85): Kennzahlen für den Status-MCP.

Rechnet Rückstände, Datenstand, Leerstand und die NW-IDs der offenen Fristen mit dem Code
der Fach-Apps aus und schreibt sie als Momentaufnahme in ``statusmcp.Kennzahl``. Die
Sichten ``sm_*`` lesen daraus. Die Meldung nennt nur Zähler.
"""

from __future__ import annotations

import datetime
from typing import Any

from django.core.management.base import CommandParser
from django.utils import timezone

from apps.kern.laeufe import LaufBefehl
from apps.statusmcp import services


class Command(LaufBefehl):
    help = (
        "Schreibt die Kennzahlen des Status-MCP neu (Rückstände, Datenstand, Leerstand, Fristen)."
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
        anzahl = services.kennzahlen_aktualisieren(stichtag)
        self.gebucht(sum(anzahl.values()))
        teile = ", ".join(f"{bereich} {zahl}" for bereich, zahl in sorted(anzahl.items()))
        return f"Stichtag {stichtag:%d.%m.%Y}: Kennzahlen neu geschrieben ({teile or 'keine'})."
