"""Lauf ``zaehler_stichtag`` (täglich, mit eigener Prüfung): Ablesung zum Stichtag (H7.2).

Im Ablesefenster (Standard 15.12. bis 15.01., siehe ``apps/zaehler/konfiguration.toml``)
legt er je Zähler ohne Stand zum Stichtag einen Arbeitsposten „Ablesung fehlt“ an,
höchstens einen je Zähler und Stichtag, und schließt Posten, deren Ablesung vorliegt.
Außerhalb des Fensters tut er nichts.
"""

from __future__ import annotations

import datetime
from typing import Any

from django.core.management.base import CommandParser
from django.utils import timezone

from apps.kern.laeufe import LaufBefehl
from apps.zaehler import services


class Command(LaufBefehl):
    help = (
        "Legt im Ablesefenster je Zähler ohne Stand zum Stichtag einen Arbeitsposten "
        "„Ablesung fehlt“ an (höchstens einen je Zähler) und schließt erledigte Posten."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--stichtag",
            type=datetime.date.fromisoformat,
            default=None,
            help=(
                "Tag, für den der Lauf prüft, im Format JJJJ-MM-TT (Standard: heute). "
                "Der Stichtag der Ablesung selbst steht in apps/zaehler/konfiguration.toml."
            ),
        )

    def ausfuehren(self, **optionen: Any) -> str:
        heute: datetime.date = optionen.get("stichtag") or timezone.localdate()
        ergebnis = services.stichtag_pruefen(heute, lauf=self.lauf)
        self.koerbe.arbeitsposten += len(ergebnis.neue_posten)
        if ergebnis.stichtag is None:
            return (
                f"{heute:%d.%m.%Y}: außerhalb des Ablesefensters; "
                f"{ergebnis.geschlossen} Posten geschlossen."
            )
        return (
            f"{heute:%d.%m.%Y}, Stichtag {ergebnis.stichtag:%d.%m.%Y}: "
            f"{ergebnis.fehlend} Zähler ohne Stand, {len(ergebnis.neue_posten)} Posten neu, "
            f"{ergebnis.geschlossen} geschlossen."
        )
