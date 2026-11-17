"""Legt den Benutzer des Hof-Systems an. Eine Registrierung über die Oberfläche gibt es nicht."""

from __future__ import annotations

import getpass
from typing import Any

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.kern import services
from apps.kern.models import Benutzer


class Command(BaseCommand):
    help = (
        "Legt den Benutzer an (E-Mail und Passwort). Den zweiten Faktor (TOTP) richtet "
        "er bei der ersten Anmeldung ein; vorher kommt er nicht über die Anmeldung hinaus."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--email", required=True, help="E-Mail-Adresse für die Anmeldung")

    def handle(self, *args: Any, **optionen: Any) -> None:
        email: str = optionen["email"]
        if Benutzer.objects.filter(email__iexact=email.strip()).exists():
            raise CommandError("Zu dieser E-Mail gibt es schon einen Benutzer.")
        passwort = getpass.getpass("Passwort: ")
        if passwort != getpass.getpass("Passwort wiederholen: "):
            raise CommandError("Die Passwörter stimmen nicht überein.")
        try:
            benutzer = services.benutzer_anlegen(email, passwort)
        except ValidationError as fehler:
            raise CommandError(" ".join(fehler.messages)) from fehler
        self.stdout.write(
            self.style.SUCCESS(
                f"Benutzer #{benutzer.pk} angelegt. Bei der ersten Anmeldung den zweiten "
                "Faktor einrichten."
            )
        )
