"""Lauf ``tageslauf`` (Standardablauf 10): ruft die angemeldeten Läufe in fester Reihenfolge.

Die Schritte stehen im Register ``apps.cockpit.tageslauf`` (jede App meldet ihre an, siehe
dort). Jeder Schritt ist ein eigener Lauf im Laufprotokoll. Der Tageslauf selbst

- läuft ohne eigene Transaktion: Jeder Schritt schreibt für sich fest;
- macht weiter, wenn ein Schritt abbricht oder mit Fehlern endet, und zählt das als Fehler
  mit dem Namen des Schritts in seiner Meldung (dann steht er auf „mit Fehlern“, und die
  Benachrichtigung meldet ihn);
- lässt abhängige Schritte trotzdem laufen und vermerkt, welcher Vorgänger gescheitert
  ist („lief trotz Fehler bei …“);
- gibt seinen Stichtag an Schritte weiter, deren Befehl ``--stichtag`` kennt;
- addiert „gebucht“ und „Arbeitsposten“ der Schritte.

    manage.py tageslauf                           heute, alle Schritte des Tages
    manage.py tageslauf --stichtag 2026-11-16     wie an diesem Tag (z. B. Montag)
    manage.py tageslauf --ohne bank_abruf         einen Schritt auslassen (mehrfach möglich)

Zeitplan: deploy/systemd/laeufe/tageslauf.conf.
"""

from __future__ import annotations

import datetime
from io import StringIO
from typing import Any

from django.core.management import call_command, get_commands, load_command_class
from django.core.management.base import BaseCommand, CommandParser

from apps.cockpit import tageslauf
from apps.kern import zeit
from apps.kern.laeufe import DEV_UEBERSPRUNGEN, LaufBefehl, dev_ueberspringen
from apps.kern.models import Lauf

GESCHEITERT = (Lauf.Status.ABGEBROCHEN, Lauf.Status.MIT_FEHLERN)


def _kennt_stichtag(befehl: BaseCommand, name: str) -> bool:
    parser = befehl.create_parser("manage.py", name)
    return any(aktion.dest == "stichtag" for aktion in parser._actions)


class Command(LaufBefehl):
    help = (
        "Tageslauf: Bank, Einordnen, Soll, Abgleich, Mahnwesen (montags), Fristen, Verwaltio, "
        "Hof-Backlog, Sicherung prüfen, Benachrichtigung. Ein Abbruch hält die anderen nicht auf."
    )
    in_transaktion = False

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--stichtag",
            type=datetime.date.fromisoformat,
            default=None,
            help="Stichtag im Format JJJJ-MM-TT (Standard: heute).",
        )
        parser.add_argument(
            "--ohne", action="append", default=[], metavar="SCHRITT", help="Schritt auslassen"
        )

    def ausfuehren(self, **optionen: Any) -> str:
        stichtag: datetime.date = optionen.get("stichtag") or zeit.heute()
        ohne = set(optionen.get("ohne") or [])
        gescheitert: set[str] = set()
        uebersprungen: list[str] = []
        zeilen = []
        for schritt in tageslauf.schritte_am(stichtag):
            if schritt.name in ohne:
                zeilen.append(f"{schritt.name}: ausgelassen")
                continue
            status, zeile = self._schritt(schritt, stichtag)
            vorgaenger = [n for n in schritt.abhaengig_von if n in gescheitert]
            if vorgaenger:
                zeile += f" – lief trotz Fehler bei {', '.join(vorgaenger)}"
            if status in GESCHEITERT or status is None:
                gescheitert.add(schritt.name)
                self.fehler()
            elif status == Lauf.Status.UEBERSPRUNGEN and self._dev_uebersprungen(schritt.befehl):
                uebersprungen.append(schritt.name)
            zeilen.append(zeile)
        kopf = f"Stichtag {stichtag:%d.%m.%Y}, {len(zeilen)} Schritte"
        if gescheitert:
            kopf += f", gescheitert: {', '.join(sorted(gescheitert))}"
        elif uebersprungen and dev_ueberspringen():
            # Review 3, Punkt 10: Ist in der Entwicklung ein Schritt an einem echten Fehler
            # gescheitert und deshalb „übersprungen (dev)“ (etwa sicherung_pruefen ohne
            # Statusdatei), ist der Tageslauf nicht „mit Fehlern“, sondern ebenfalls
            # „übersprungen (dev)“. Andere „übersprungen“ (benachrichtigen ohne Kanal) zählen
            # nicht. Die übrigen Schritte stehen mit ihrem Ergebnis in der Meldung.
            self.ueberspringen()
            kopf = f"{DEV_UEBERSPRUNGEN}: {', '.join(uebersprungen)}. {kopf}"
        return kopf + ". " + "; ".join(zeilen) + "."

    def _dev_uebersprungen(self, name: str) -> bool:
        """Endete der letzte Lauf dieses Schritts „übersprungen (dev)“ (ein echter Fehler, den
        die Entwicklung überspringt)?"""
        lauf = Lauf.objects.filter(name=name, pk__gt=self.lauf.pk).order_by("-pk").first()
        return lauf is not None and lauf.meldung.startswith(DEV_UEBERSPRUNGEN)

    def _schritt(
        self, schritt: tageslauf.Schritt, stichtag: datetime.date
    ) -> tuple[str | None, str]:
        """Führt einen Schritt aus. Rückgabe: Status seines Laufs (None: kein Lauf) und Zeile."""
        try:
            app = get_commands()[schritt.befehl]
            befehl = load_command_class(app, schritt.befehl)
        except KeyError:
            return None, f"{schritt.name}: Befehl {schritt.befehl} fehlt"
        optionen = dict(schritt.optionen)
        if "stichtag" not in optionen and _kennt_stichtag(befehl, schritt.befehl):
            optionen["stichtag"] = stichtag
        fehlerart = ""
        try:
            call_command(befehl, stdout=StringIO(), stderr=StringIO(), **optionen)
        except Exception as fehler:
            # Nur die Art des Fehlers, nie seinen Text (er könnte Daten nennen).
            fehlerart = type(fehler.__cause__ or fehler).__name__
        lauf: Lauf | None = getattr(befehl, "lauf", None)
        if lauf is None or lauf.pk is None:
            return None, f"{schritt.name}: abgebrochen ohne Lauf ({fehlerart or 'kein LaufBefehl'})"
        lauf.refresh_from_db()
        self.koerbe.gebucht += lauf.gebucht
        self.koerbe.arbeitsposten += lauf.arbeitsposten
        zeile = f"{schritt.name}: {lauf.get_status_display()} (Lauf #{lauf.pk}"
        zeile += f", {fehlerart})" if fehlerart else ")"
        return lauf.status, zeile
