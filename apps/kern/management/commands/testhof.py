"""Baut den erfundenen Testhof auf: ``manage.py testhof --seed 42 [--leeren]``.

Nur für Entwicklung, Tests und Pipeline. Mit ``DJANGO_ENV=prod`` bricht der Befehl
ab, bevor er etwas liest oder schreibt.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from testhof import sperre


class Command(BaseCommand):
    help = (
        "Baut den erfundenen Testhof auf (nur Entwicklung und Tests). Gleicher Seed ergibt "
        "dieselben Daten. Mit --leeren werden die Daten des Testhofs vorher entfernt."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--seed", type=int, default=42, help="Startwert (Standard: 42)")
        parser.add_argument(
            "--leeren",
            action="store_true",
            help="Daten des Testhofs vorher entfernen und neu aufbauen",
        )

    def handle(self, *args: Any, **optionen: Any) -> None:
        # Zuerst die Sperre, noch vor dem Import des Generators (braucht Faker).
        try:
            sperre.pruefen()
        except sperre.TesthofGesperrt as fehler:
            raise CommandError(str(fehler)) from fehler

        from testhof import aufbau, kern, pruefsumme

        seed: int = optionen["seed"]
        try:
            with transaction.atomic():
                if optionen["leeren"]:
                    aufbau.leeren()
                hof = aufbau.aufbauen(seed)
        except aufbau.TesthofFehler as fehler:
            raise CommandError(str(fehler)) from fehler

        self.stdout.write(
            self.style.SUCCESS(f"Testhof aus Seed {seed} aufgebaut: {', '.join(hof.module)}")
        )
        for label, anzahl in pruefsumme.zeilen_je_tabelle().items():
            self.stdout.write(f"  {label}: {anzahl}")
        self.stdout.write(f"Prüfsumme: {pruefsumme.pruefsumme()}")
        self.stdout.write(
            f"Anmeldung: {kern.EMAIL} mit Passwort {kern.PASSWORT}; "
            f"TOTP-Geheimnis für die Authenticator-App: {kern.TOTP_GEHEIMNIS}"
        )
