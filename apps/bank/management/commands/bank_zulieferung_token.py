"""Erzeugt den Token, mit dem der hof-mac Umsätze eines Kontos zuliefert.

    manage.py bank_zulieferung_token --konto <id>

Der Token gilt nur für dieses eine Konto und erscheint genau einmal auf dem Bildschirm;
gespeichert wird nur sein Hash. Ein früherer Token des Kontos gilt danach nicht mehr.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.bank import services
from apps.bank.models import Bankkonto


class Command(BaseCommand):
    help = (
        "Erzeugt einen neuen Zulieferungs-Token für ein Konto und gibt ihn einmal aus. "
        "Ein früherer Token des Kontos gilt danach nicht mehr."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--konto", type=int, required=True, help="ID des Bankkontos")

    def handle(self, *args: Any, **optionen: Any) -> None:
        konto = Bankkonto.objects.filter(pk=optionen["konto"]).first()
        if konto is None:
            raise CommandError(f"Konto #{optionen['konto']} gibt es nicht.")
        try:
            token = services.zulieferung_token_erzeugen(konto)
        except services.BankFehler as fehler:
            raise CommandError(str(fehler)) from fehler
        self.stdout.write(
            f"Token für Konto #{konto.pk}. Er erscheint nur jetzt; auf dem hof-mac in die "
            "Token-Datei schreiben (chmod 600):"
        )
        self.stdout.write(token)
