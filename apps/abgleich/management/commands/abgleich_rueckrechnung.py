"""Rückrechnung des Abgleichs (Umzug, Stufe 2).

    manage.py abgleich_rueckrechnung --von 2026-07 --bis 2026-12 [--stichtag JJJJ-MM-TT]

Rechnet den Abgleich für den Zeitraum neu, in einer Kopie: alles läuft in einer Transaktion,
die am Ende zurückgerollt wird; die Datenbank bleibt unverändert, ein Laufprotokoll entsteht
nicht. Ausgabe je Vertrag: bezahlt, offen oder in Klärung, als Zähler, Vertragsnummern und
NW-IDs, dazu die Abweichungen zum aktuellen Stand. Ohne Namen und ohne Beträge je Vertrag;
den Vergleich mit dem alten Mietabgleich macht Max.
"""

from __future__ import annotations

import datetime
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.abgleich import services
from apps.stammdaten import fristrechnung


def _monat(text: str) -> datetime.date:
    try:
        if len(text) == 7:
            return datetime.date.fromisoformat(f"{text}-01")
        return datetime.date.fromisoformat(text)
    except ValueError as fehler:
        raise CommandError(f"Kein Monat (JJJJ-MM): {text}") from fehler


class Command(BaseCommand):
    help = "Rechnet den Abgleich für einen Zeitraum in einer Kopie neu (nichts wird gespeichert)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--von", required=True, help="erster Monat (JJJJ-MM)")
        parser.add_argument("--bis", required=True, help="letzter Monat (JJJJ-MM)")
        parser.add_argument(
            "--stichtag", type=datetime.date.fromisoformat, help="Stichtag; Standard heute"
        )

    def handle(self, *args: Any, **optionen: Any) -> None:
        von = _monat(optionen["von"]).replace(day=1)
        bis = fristrechnung.monatsende(_monat(optionen["bis"]))
        if bis < von:
            raise CommandError("--bis liegt vor --von.")
        bericht = services.rueckrechnung(von, bis, stichtag=optionen.get("stichtag"))
        for zeile in bericht.zeilen():
            self.stdout.write(zeile)
