"""Lauf ``verwaltio_kennzahlen`` (nachts, V12): Kennzahlen je Wohneinheit aus Verwaltio.

Holt je Wohneinheit des Hof-Systems offene Posten (Summe), Mahnfälle (Anzahl), Stand der
NKA und Fristen und speichert sie als ``Kennzahl`` mit NW-ID und Datum, ohne Namen.
Idempotent je Tag. Ist Verwaltio nicht eingerichtet, endet der Lauf mit einer Meldung und
ohne Fehler; scheitert der Abruf einzelner Einheiten, zählen sie als Fehler.
"""

from __future__ import annotations

import datetime
from typing import Any

from django.core.management.base import CommandParser

from apps.anbindungen import services
from apps.anbindungen.verwaltio import VerwaltioNichtEingerichtet, verwaltio_client
from apps.kern import zeit
from apps.kern.laeufe import LaufBefehl


class Command(LaufBefehl):
    help = (
        "Holt je Wohneinheit offene Posten, Mahnfälle, Stand der NKA und Fristen aus Verwaltio "
        "und speichert sie als Kennzahl mit NW-ID und Datum (einmal je Tag)."
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
        try:
            client = verwaltio_client()
        except VerwaltioNichtEingerichtet as fehler:
            return f"Stichtag {stichtag:%d.%m.%Y}: {fehler}"
        ergebnis = services.kennzahlen_abrufen(client, stichtag)
        self.gebucht(ergebnis.neu + ergebnis.aktualisiert)
        self.fehler(len(ergebnis.fehler))
        meldung = (
            f"Stichtag {stichtag:%d.%m.%Y}: {ergebnis.neu} Kennzahlen neu, "
            f"{ergebnis.aktualisiert} aktualisiert, {len(ergebnis.fehler)} Fehler"
        )
        if ergebnis.fehler:
            meldung += f" ({', '.join(ergebnis.fehler)})"
        return meldung + "."
