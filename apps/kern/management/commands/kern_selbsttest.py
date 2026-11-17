"""Beispiel-Lauf: prüft das Lauf-Gerüst und legt genau einen Arbeitsposten an."""

from __future__ import annotations

from typing import Any

from apps.kern.laeufe import LaufBefehl

ART = "Selbsttest"


class Command(LaufBefehl):
    help = "Selbsttest des Lauf-Gerüsts: öffnet einen Lauf und legt einen Arbeitsposten an."

    def ausfuehren(self, **optionen: Any) -> str:
        posten = self.arbeitsposten_anlegen(
            ART,
            bezug=self.lauf,
            hinweis=(
                "Selbsttest: Lauf und Arbeitsliste funktionieren. Bitte als erledigt markieren."
            ),
        )
        return f"Arbeitsposten #{posten.pk} angelegt."
