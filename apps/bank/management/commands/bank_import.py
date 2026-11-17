"""Import alter Umsätze aus einer CSV-Datei (H3.3).

    manage.py bank_import <datei> --konto <id>                 Probelauf (Standard)
    manage.py bank_import <datei> --konto <id> --ausfuehren    übernehmen

Der Probelauf ändert nichts, auch kein Laufprotokoll. Mit ``--ausfuehren`` läuft der
Import als Lauf (``kern.Lauf``). Der Bericht nennt nur Zähler und Abweichungsarten.
Die Spaltenzuordnung steht in ``apps/bank/importformat.py``.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

from django.core.management.base import CommandError, CommandParser

from apps.bank import importieren
from apps.bank.importformat import FORMATE, VIVID_CSV
from apps.bank.models import Bankkonto
from apps.kern.laeufe import LaufBefehl


class Command(LaufBefehl):
    help = (
        "Importiert alte Umsätze aus einer CSV-Datei. Standard ist der Probelauf; "
        "übernommen wird erst mit --ausfuehren. Der Bericht nennt nur Zähler."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("datei", type=Path, help="CSV-Datei (Export der Bank)")
        parser.add_argument("--konto", type=int, required=True, help="ID des Bankkontos")
        parser.add_argument(
            "--format", choices=sorted(FORMATE), default=VIVID_CSV.name, help="Format der Datei"
        )
        parser.add_argument(
            "--bis",
            type=datetime.date.fromisoformat,
            help="nur Umsätze vor diesem Tag (Standard: erster Tag des Abrufs)",
        )
        modus = parser.add_mutually_exclusive_group()
        modus.add_argument(
            "--probelauf", action="store_true", help="nur zählen, nichts ändern (Standard)"
        )
        modus.add_argument("--ausfuehren", action="store_true", help="Umsätze übernehmen")

    def handle(self, *args: Any, **optionen: Any) -> None:
        if optionen["ausfuehren"]:
            super().handle(*args, **optionen)
            return
        # Probelauf: ohne Lauf und ohne Transaktion, es wird nichts geschrieben.
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
        konto = Bankkonto.objects.filter(pk=optionen["konto"]).first()
        if konto is None:
            raise CommandError(f"Konto #{optionen['konto']} gibt es nicht.")
        format = FORMATE[optionen["format"]]
        pfad: Path = optionen["datei"]
        try:
            inhalt = pfad.read_text(encoding=format.kodierung)
        except (OSError, UnicodeDecodeError) as fehler:
            raise CommandError(
                f"Die Datei lässt sich nicht lesen ({type(fehler).__name__})."
            ) from fehler
        try:
            return importieren.importieren(
                konto,
                inhalt,
                format,
                ausfuehren=ausfuehren,
                bis=optionen.get("bis"),
                lauf=getattr(self, "lauf", None) if ausfuehren else None,
            )
        except importieren.ImportFehler as fehler:
            raise CommandError(str(fehler)) from fehler

    def _bericht(self, bericht: importieren.ImportBericht) -> None:
        for zeile in bericht.zeilen_text():
            self.stdout.write(zeile)
