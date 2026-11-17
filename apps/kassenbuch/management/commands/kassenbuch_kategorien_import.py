"""Import der Kategorien aus einer Liste mit Name und Art.

    manage.py kassenbuch_kategorien_import <datei>                 Probelauf (Standard)
    manage.py kassenbuch_kategorien_import <datei> --ausfuehren    anlegen

Der Probelauf ändert nichts, auch kein Laufprotokoll. Mit ``--ausfuehren`` läuft der Import
als Lauf (``kern.Lauf``). Der Bericht nennt nur Zähler, Abweichungsarten und Zeilennummern.
Die Spaltenzuordnung steht in ``apps/kassenbuch/importformat.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.core.management.base import CommandError, CommandParser

from apps.kassenbuch import importieren
from apps.kassenbuch.importformat import FORMATE, KATEGORIEN_CSV
from apps.kern.laeufe import LaufBefehl


class Command(LaufBefehl):
    help = (
        "Importiert Kategorien (Name und Art) aus einer CSV-Datei. Standard ist der "
        "Probelauf; angelegt wird erst mit --ausfuehren. Der Bericht nennt nur Zähler."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("datei", type=Path, help="CSV-Datei mit Kategorien")
        parser.add_argument(
            "--format",
            choices=sorted(FORMATE),
            default=KATEGORIEN_CSV.name,
            help="Format der Datei",
        )
        modus = parser.add_mutually_exclusive_group()
        modus.add_argument(
            "--probelauf", action="store_true", help="nur zählen, nichts ändern (Standard)"
        )
        modus.add_argument("--ausfuehren", action="store_true", help="Kategorien anlegen")

    def handle(self, *args: Any, **optionen: Any) -> None:
        if optionen["ausfuehren"]:
            super().handle(*args, **optionen)
            return
        bericht = self._importieren(optionen, ausfuehren=False)
        self._bericht(bericht)

    def ausfuehren(self, **optionen: Any) -> str:
        bericht = self._importieren(optionen, ausfuehren=True)
        self.gebucht(bericht.neu)
        self.fehler(sum(bericht.fehlerhaft.values()))
        self._bericht(bericht)
        return "; ".join(bericht.zeilen_text())

    def _importieren(
        self, optionen: dict[str, Any], *, ausfuehren: bool
    ) -> importieren.ImportBericht:
        format = FORMATE[optionen["format"]]
        pfad: Path = optionen["datei"]
        try:
            inhalt = pfad.read_text(encoding=format.kodierung)
        except (OSError, UnicodeDecodeError) as fehler:
            raise CommandError(
                f"Die Datei lässt sich nicht lesen ({type(fehler).__name__})."
            ) from fehler
        try:
            return importieren.importieren(inhalt, format, ausfuehren=ausfuehren)
        except importieren.ImportFehler as fehler:
            raise CommandError(str(fehler)) from fehler

    def _bericht(self, bericht: importieren.ImportBericht) -> None:
        for zeile in bericht.zeilen_text():
            self.stdout.write(zeile)
