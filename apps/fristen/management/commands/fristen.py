"""Lauf ``fristen`` (täglich): fällige und überfällige Fristen werden Arbeitsposten."""

from __future__ import annotations

import datetime
from typing import Any

from django.core.management.base import CommandParser

from apps.fristen import services
from apps.kern import zeit
from apps.kern.laeufe import LaufBefehl


class Command(LaufBefehl):
    help = (
        "Legt für jede fällige oder überfällige Frist einen Arbeitsposten „Frist fällig“ an "
        "(höchstens einen je Frist) und schließt Posten, deren Frist erledigt ist."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--stichtag",
            type=datetime.date.fromisoformat,
            default=None,
            help="Stichtag im Format JJJJ-MM-TT (Standard: heute).",
        )

    def ausfuehren(self, **optionen: Any) -> str:
        stichtag: datetime.date = optionen.get("stichtag") or zeit.heute()
        ergebnis = services.fristen_pruefen(stichtag, lauf=self.lauf)
        self.koerbe.arbeitsposten += len(ergebnis.neue_posten)
        weitere = "".join(f" {name}: {anzahl}." for name, anzahl in ergebnis.weitere.items())
        return (
            f"Stichtag {stichtag:%d.%m.%Y}: {ergebnis.faellig} Fristen fällig, "
            f"{len(ergebnis.neue_posten)} Posten neu, {ergebnis.geschlossen} geschlossen." + weitere
        )
