"""Import vorhandener Zähler und Stände (CSV).

``manage.py import_zaehler <zaehler.csv> [--staende <staende.csv>] [--probelauf | --ausfuehren]``

Standard ist der Probelauf: Der Bericht nennt Anzahlen, Zählernummern, NW-IDs und
Zeilennummern, geändert wird nichts. Die Spalten stehen in ``apps/zaehler/import_spalten.toml``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.core.management.base import CommandParser

from apps.stammdaten.management.commands._importbefehl import ImportBefehl
from apps.zaehler import importe


class Command(ImportBefehl):
    help = "Importiert Zähler und Stände (Standard: Probelauf ohne Änderung)."
    lauf_name = "import_zaehler"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("datei", type=Path, help="CSV mit den Zählern")
        parser.add_argument("--staende", type=Path, default=None, help="CSV mit den Ständen")
        super().add_arguments(parser)

    def ausfuehren(self, **optionen: Any) -> str:
        self.pfad_pruefen(optionen["datei"])
        if optionen["staende"] is not None:
            self.pfad_pruefen(optionen["staende"])
        bericht = importe.zaehler_importieren(
            optionen["datei"],
            optionen["staende"],
            ausfuehren=optionen["ausfuehren"],
        )
        self.gebucht(bericht.zaehler.get("staende_neu", 0))
        return self.bericht_ausgeben(bericht)
