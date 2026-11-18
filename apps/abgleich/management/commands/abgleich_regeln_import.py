"""Import der Mietregeln aus hof-fints (H1.4).

    manage.py abgleich_regeln_import <regeln.yaml>                 Probelauf (Standard)
    manage.py abgleich_regeln_import <regeln.yaml> --ausfuehren    anlegen

Der Probelauf ändert nichts, auch kein Laufprotokoll. Mit ``--ausfuehren`` läuft der Import
als Lauf (``kern.Lauf``). Der Bericht nennt nur Zähler, Eintrag-IDs und NW-IDs. Die
Zuordnung der Felder steht in ``apps/abgleich/importformat.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.core.management.base import CommandError, CommandParser

from apps.abgleich import importieren
from apps.abgleich.importformat import FORMATE, REGELN_YAML
from apps.kern.laeufe import LaufBefehl


class Command(LaufBefehl):
    help = (
        "Importiert die Mietregeln (Zahler und Vertrag) aus einer YAML-Datei. Standard ist "
        "der Probelauf; angelegt wird erst mit --ausfuehren. Der Bericht nennt nur Zähler."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("datei", type=Path, help="YAML-Datei mit den Regeln")
        parser.add_argument(
            "--format", choices=sorted(FORMATE), default=REGELN_YAML.name, help="Format"
        )
        modus = parser.add_mutually_exclusive_group()
        modus.add_argument(
            "--probelauf", action="store_true", help="nur zählen, nichts ändern (Standard)"
        )
        modus.add_argument("--ausfuehren", action="store_true", help="Zahlerregeln anlegen")

    def handle(self, *args: Any, **optionen: Any) -> None:
        if optionen["ausfuehren"]:
            super().handle(*args, **optionen)
            return
        self._bericht(self._importieren(optionen, ausfuehren=False))

    def ausfuehren(self, **optionen: Any) -> str:
        bericht = self._importieren(optionen, ausfuehren=True)
        self.gebucht(bericht.neu)
        self.fehler(sum(bericht.fehlerhaft.values()))
        self._bericht(bericht)
        return "; ".join(bericht.zeilen_text())

    def _importieren(
        self, optionen: dict[str, Any], *, ausfuehren: bool
    ) -> importieren.ImportBericht:
        pfad: Path = optionen["datei"]
        try:
            inhalt = pfad.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as fehler:
            raise CommandError(
                f"Die Datei lässt sich nicht lesen ({type(fehler).__name__})."
            ) from fehler
        try:
            return importieren.importieren(
                inhalt, FORMATE[optionen["format"]], ausfuehren=ausfuehren
            )
        except importieren.ImportFehler as fehler:
            raise CommandError(str(fehler)) from fehler

    def _bericht(self, bericht: importieren.ImportBericht) -> None:
        for zeile in bericht.zeilen_text():
            self.stdout.write(zeile)
