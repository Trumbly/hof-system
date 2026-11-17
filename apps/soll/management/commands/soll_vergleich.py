"""Vergleich mit dem Soll-Export aus HelloHousing (H3.4, Abnahme Schritt 5).

``manage.py soll_vergleich --export <datei> [--von JJJJ-MM-TT] [--bis JJJJ-MM-TT]``

Zählt je Vertrag die Abweichungen zwischen Export und Sollposten. Ändert nichts. Der
Bericht nennt nur Zähler, Vertragsnummern, NW-IDs und Zeilennummern, keine Beträge je
Vertrag und keine Namen. Die Spaltenzuordnung steht in ``apps/soll/konfiguration.toml``.
Vorher ``soll_monat --stichtag`` bis zum Ende des Zeitraums laufen lassen.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

from django.core.management.base import CommandError, CommandParser

from apps.kern.laeufe import LaufBefehl
from apps.soll import vergleich


class Command(LaufBefehl):
    help = "Zählt je Vertrag die Abweichungen gegen den Soll-Export aus HelloHousing."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--export", type=Path, required=True, help="CSV des Soll-Exports")
        parser.add_argument(
            "--von", type=datetime.date.fromisoformat, default=None, help="erster Beginn"
        )
        parser.add_argument(
            "--bis", type=datetime.date.fromisoformat, default=None, help="letzter Beginn"
        )

    def handle(self, *args: Any, **optionen: Any) -> None:
        # Datei und Spalten vor dem Lauf prüfen, damit die Meldung beim Aufrufer ankommt.
        pfad: Path = optionen["export"]
        if not pfad.is_file():
            raise CommandError(f"Datei nicht gefunden: {pfad.name}")
        try:
            vergleich.koepfe_pruefen(pfad)
        except vergleich.VergleichFehler as fehler:
            raise CommandError(str(fehler)) from fehler
        super().handle(*args, **optionen)

    def ausfuehren(self, **optionen: Any) -> str:
        bericht = vergleich.vergleichen(
            optionen["export"], von=optionen["von"], bis=optionen["bis"]
        )
        self.stdout.write(bericht.text())
        gesamt = sum(bericht.gesamt.values())
        return (
            f"{len(bericht.je_vertrag)} Verträge verglichen, {len(bericht.mit_abweichung)} mit "
            f"Abweichungen ({gesamt} gesamt): {', '.join(bericht.mit_abweichung) or 'keine'}"
        )
