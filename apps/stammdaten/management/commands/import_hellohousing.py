"""Import der Parteien und laufenden Verträge aus dem HelloHousing-Export (CSV).

``manage.py import_hellohousing <mieter.csv> [--mietverlauf <verlauf.csv>]
[--probelauf | --ausfuehren] [--stichtag JJJJ-MM-TT]``

Vorher die Einheiten importieren: Verträge werden über NW-ID oder Bezeichnung der Einheit
zugeordnet. Wohnraum wird übersprungen. Standard ist der Probelauf ohne Änderung.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.core.management.base import CommandParser

from apps.stammdaten import importe

from ._importbefehl import ImportBefehl


class Command(ImportBefehl):
    help = "Importiert Parteien und Verträge aus HelloHousing (Standard: Probelauf ohne Änderung)."
    lauf_name = "import_hellohousing"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("datei", type=Path, help="CSV der Mietverhältnisse mit Mietern")
        parser.add_argument(
            "--mietverlauf", type=Path, default=None, help="CSV des Mietverlaufs (optional)"
        )
        super().add_arguments(parser)

    def ausfuehren(self, **optionen: Any) -> str:
        self.pfad_pruefen(optionen["datei"])
        if optionen["mietverlauf"] is not None:
            self.pfad_pruefen(optionen["mietverlauf"])
        bericht = importe.hellohousing_importieren(
            optionen["datei"],
            optionen["mietverlauf"],
            ausfuehren=optionen["ausfuehren"],
            stichtag=optionen["stichtag"],
        )
        return self.bericht_ausgeben(bericht)
