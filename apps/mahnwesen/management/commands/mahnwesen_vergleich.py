"""Vergleich mit dem alten Mahnlauf (Abnahme Schritt 7, Standardablauf 3).

``manage.py mahnwesen_vergleich --von JJJJ-MM-TT --bis JJJJ-MM-TT [--alt <datei>]``

Gibt je Woche die Vorschläge des Mahnlaufs (Stufen mit Anzahl und NW-IDs), die offenen
Fälle je Stufe und die gesperrten aus; mit ``--alt`` dazu die Abweichungen zur Exportdatei
des alten Laufs (Format in ``apps.mahnwesen.vergleich``). Keine Namen, keine Beträge.
Ändert nichts.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

from django.core.management.base import CommandError, CommandParser

from apps.kern.laeufe import LaufBefehl
from apps.mahnwesen import vergleich


class Command(LaufBefehl):
    help = (
        "Fälle und Stufen des Mahnlaufs je Woche (Zähler und NW-IDs), optional gegen den "
        "alten Lauf."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--von", type=datetime.date.fromisoformat, required=True)
        parser.add_argument("--bis", type=datetime.date.fromisoformat, required=True)
        parser.add_argument("--alt", type=Path, default=None, help="CSV des alten Mahnlaufs")

    def handle(self, *args: Any, **optionen: Any) -> None:
        pfad: Path | None = optionen["alt"]
        if optionen["bis"] < optionen["von"]:
            raise CommandError("--bis liegt vor --von.")
        if pfad is not None:
            if not pfad.is_file():
                raise CommandError(f"Datei nicht gefunden: {pfad.name}")
            try:
                optionen["alt_daten"] = vergleich.alt_lesen(pfad)
            except vergleich.VergleichFehler as fehler:
                raise CommandError(str(fehler)) from fehler
        super().handle(*args, **optionen)

    def ausfuehren(self, **optionen: Any) -> str:
        liste = vergleich.wochen(optionen["von"], optionen["bis"])
        alt = optionen.get("alt_daten")
        abw = vergleich.abweichungen(liste, alt) if alt is not None else None
        self.stdout.write(vergleich.text(liste, abw))
        vorschlaege = sum(len(ids) for w in liste for ids in w.vorschlaege.values())
        meldung = f"{len(liste)} Wochen, {vorschlaege} Vorschläge"
        if abw is not None:
            meldung += f", {len(abw)} Abweichungen zum alten Lauf"
        return meldung
