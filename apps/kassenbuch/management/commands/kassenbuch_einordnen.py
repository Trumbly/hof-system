"""Lauf ``kassenbuch_einordnen`` (H6.3): täglich um 6:30, nach dem Bankabruf um 6:00.

Wendet die Zuordnungsregeln auf alle Umsätze ohne Buchung an. Eindeutige Treffer (genau eine
Regel mit der höchsten Priorität) werden gebucht; keine oder mehrere gleichrangige Regeln
ergeben einen Arbeitsposten „Umsätze einordnen“ mit den Aktionen Zuordnen, Aufteilen, Regel
anlegen und Zurückstellen. Idempotent: Ein zweiter Lauf bucht nichts doppelt und legt keinen
Posten doppelt an.

Ohne ``--ab`` beginnt der Lauf bei ``HOF_KASSENBUCH_BEGINN`` (leer: beim ersten Umsatz).
"""

from __future__ import annotations

import datetime
from typing import Any

from django.conf import settings
from django.core.management.base import CommandParser

from apps.kassenbuch import services
from apps.kern.laeufe import LaufBefehl


class Command(LaufBefehl):
    help = (
        "Ordnet Umsätze ohne Buchung nach den Regeln ein; Unklares kommt in die Arbeitsliste "
        "„Umsätze einordnen“."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--ab",
            type=datetime.date.fromisoformat,
            help="nur Umsätze ab diesem Tag (JJJJ-MM-TT); Standard HOF_KASSENBUCH_BEGINN",
        )

    def ausfuehren(self, **optionen: Any) -> str:
        ab = optionen.get("ab") or settings.KASSENBUCH_BEGINN
        ergebnis = services.umsaetze_einordnen(ab=ab, lauf=self.lauf)
        self.gebucht(ergebnis.gebucht)
        self.koerbe.arbeitsposten += ergebnis.arbeitsposten
        self.fehler(ergebnis.fehler)
        return (
            f"{ergebnis.gebucht} Umsätze gebucht, {ergebnis.arbeitsposten} neue Arbeitsposten, "
            f"{ergebnis.fehler} Fehler" + (f"; ab {ab:%d.%m.%Y}" if ab else "")
        )
