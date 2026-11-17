"""Legt ein eigenes Bankkonto an (einmalig bei der Einrichtung, siehe docs/betrieb.md)."""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.bank import services
from apps.bank.models import Bankkonto
from apps.kern.models import Mandant


class Command(BaseCommand):
    help = "Legt ein eigenes Bankkonto an und nennt seine ID."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--mandant", required=True, help="Kürzel des Mandanten, z. B. GBR")
        parser.add_argument("--bank", required=True, help="z. B. Vivid oder VR")
        parser.add_argument("--bezeichnung", required=True)
        parser.add_argument("--iban", required=True, help="IBAN des eigenen Kontos")
        parser.add_argument("--zugang", required=True, choices=Bankkonto.Zugang.values)
        parser.add_argument(
            "--eb-bank-name", default="", help="Name der Bank bei Enable Banking (ASPSP)"
        )

    def handle(self, *args: Any, **optionen: Any) -> None:
        mandant = Mandant.objects.filter(kuerzel=optionen["mandant"]).first()
        if mandant is None:
            raise CommandError(f"Mandant {optionen['mandant']} gibt es nicht.")
        try:
            konto = services.bankkonto_anlegen(
                mandant,
                bank=optionen["bank"],
                bezeichnung=optionen["bezeichnung"],
                iban=optionen["iban"],
                zugang=optionen["zugang"],
                eb_bank_name=optionen["eb_bank_name"],
            )
        except (ValidationError, services.BankFehler) as fehler:
            meldung = "; ".join(getattr(fehler, "messages", [str(fehler)]))
            raise CommandError(meldung) from fehler
        self.stdout.write(self.style.SUCCESS(f"Bankkonto #{konto.pk} angelegt."))
