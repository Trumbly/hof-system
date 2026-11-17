"""Lauf ``kassenbuch_monatsabschluss`` (H6.6, Standardablauf 7): am 3. jedes Monats um 7 Uhr.

Schließt je Mandant mit Bankkonten die offenen Monate bis zum Vormonat der Reihe nach ab:
alle Umsätze eingeordnet und der Saldo im Buch gleich dem Saldo der Bank (``bank.Kontostand``).
Was fehlt, wird ein Arbeitsposten „Monatsabschluss offen“; spätere Monate bleiben dann offen.
Ist ein Monat für alle Mandanten abgeschlossen, entstehen die Monatsberichte je Objekt als
PDF (H10.2). Ein abgeschlossener Monat ist gesperrt.

    manage.py kassenbuch_monatsabschluss                    bis zum Vormonat
    manage.py kassenbuch_monatsabschluss --bis 2026-12      bis Dezember 2026
    manage.py kassenbuch_monatsabschluss --ab 2026-01       erster Monat, falls noch keiner

Ohne ``--ab`` beginnt der erste Abschluss bei ``HOF_KASSENBUCH_BEGINN`` (leer: beim Monat des
ersten Umsatzes). Gibt es schon Abschlüsse, geht es mit dem Monat danach weiter.
"""

from __future__ import annotations

import datetime
from typing import Any

from django.conf import settings
from django.core.management.base import CommandError, CommandParser
from django.utils import timezone

from apps.kassenbuch import auswertung, pdf, services
from apps.kassenbuch.forms import monat_aus_text
from apps.kern.laeufe import LaufBefehl


def _monat(text: str) -> datetime.date:
    monat = monat_aus_text(text)
    if monat is None:
        raise CommandError(f"Monat als JJJJ-MM angeben, nicht {text!r}.")
    return monat


class Command(LaufBefehl):
    help = (
        "Schließt die offenen Monate bis zum Vormonat ab, sperrt sie und erzeugt die "
        "Monatsberichte je Objekt; Lücken kommen in die Arbeitsliste."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--bis", type=_monat, help="letzter Monat (JJJJ-MM), Standard Vormonat")
        parser.add_argument(
            "--ab", type=_monat, help="erster Monat, falls es noch keinen Abschluss gibt (JJJJ-MM)"
        )

    def ausfuehren(self, **optionen: Any) -> str:
        bis = optionen.get("bis") or auswertung.vormonat(timezone.localdate())
        ab = optionen.get("ab") or settings.KASSENBUCH_BEGINN
        ergebnis = services.monatsabschluesse_ausfuehren(bis, ab=ab, lauf=self.lauf)
        self.gebucht(len(ergebnis.abgeschlossen))
        self.koerbe.arbeitsposten += ergebnis.arbeitsposten
        # Ein Bericht ohne PDF ist ein Fehler des Laufs, aber kein Grund zum Abbruch.
        self.fehler(len(ergebnis.berichte_ohne_pdf))
        teile = [
            f"{len(ergebnis.abgeschlossen)} Monate abgeschlossen",
            f"{len(ergebnis.offen)} offen",
            f"{len(ergebnis.berichte)} Monatsberichte",
        ]
        teile += [
            f"Mandant #{o.abschluss.mandant_id} {o.abschluss.monat:%m/%Y} offen"
            for o in ergebnis.offen
        ]
        if ergebnis.berichte_ohne_pdf:
            teile.append(f"{len(ergebnis.berichte_ohne_pdf)} Berichte ohne PDF ({pdf.grund()})")
        return "; ".join(teile)
