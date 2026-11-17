"""Import der Einheiten aus dem Einheiten-Verzeichnis (Notion, CSV).

``manage.py import_einheiten <datei.csv> [--probelauf | --ausfuehren] [--stichtag JJJJ-MM-TT]``

Standard ist der Probelauf: Der Bericht nennt Zähler und NW-IDs, geändert wird nichts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.core.management.base import CommandParser

from apps.stammdaten import importe

from ._importbefehl import ImportBefehl


class Command(ImportBefehl):
    help = "Importiert Einheiten aus dem Notion-Export (Standard: Probelauf ohne Änderung)."
    lauf_name = "import_einheiten"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("datei", type=Path, help="CSV-Export des Einheiten-Verzeichnisses")
        super().add_arguments(parser)

    def ausfuehren(self, **optionen: Any) -> str:
        self.pfad_pruefen(optionen["datei"])
        bericht = importe.einheiten_importieren(
            optionen["datei"], ausfuehren=optionen["ausfuehren"], stichtag=optionen["stichtag"]
        )
        return self.bericht_ausgeben(bericht)
