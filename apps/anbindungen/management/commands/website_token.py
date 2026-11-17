"""Erzeugt den Token, mit dem die Website Katalog und Bilder liest und Ereignisse meldet.

    manage.py website_token [--bezeichnung "Website-Server"] [--behalten] [--sperren]

Der Token erscheint genau einmal auf dem Bildschirm; gespeichert wird nur sein Hash. Ohne
``--behalten`` gelten frühere Tokens danach nicht mehr. ``--sperren`` sperrt alle Tokens,
ohne einen neuen zu erzeugen.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.anbindungen import services


class Command(BaseCommand):
    help = (
        "Erzeugt einen neuen Token für die Website und gibt ihn einmal aus. Frühere Tokens "
        "gelten danach nicht mehr (außer mit --behalten)."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--bezeichnung", default="Website", help="z. B. „Website-Server“")
        parser.add_argument(
            "--behalten",
            action="store_true",
            help="Frühere Tokens bleiben gültig (Wechsel ohne Ausfall).",
        )
        parser.add_argument(
            "--sperren", action="store_true", help="Alle Tokens sperren, keinen neuen erzeugen."
        )

    def handle(self, *args: Any, **optionen: Any) -> None:
        if optionen["sperren"]:
            anzahl = services.website_token_sperren()
            self.stdout.write(f"{anzahl} Website-Token gesperrt.")
            return
        token = services.website_token_erzeugen(
            optionen["bezeichnung"], ersetzen=not optionen["behalten"]
        )
        self.stdout.write(
            "Token für die Website. Er erscheint nur jetzt; auf dem Website-Server als "
            "HOF_API_TOKEN hinterlegen:"
        )
        self.stdout.write(token)
