"""Lauf ``aufgaben_abgleich`` (N1): offene Freigaben als Aufgaben ins Hof-Backlog (Notion).

Je Art aus ``HOF_NOTION_ARTEN`` eine Aufgabe mit Titel aus Anzahl und Art, NW-IDs bzw.
Objekt-Kürzeln, frühester Fälligkeit und Link; ohne Namen und Beträge. Ändern sich die
offenen Posten, wird die Aufgabe aktualisiert; ist keiner mehr offen, steht sie auf
Erledigt. Läuft im Tageslauf (Rang 80) und alle 30 Minuten
(deploy/systemd/laeufe/aufgaben_abgleich.conf). Ohne Notion-Zugang endet er mit
„nicht eingerichtet“ und ohne Fehler.
"""

from __future__ import annotations

from typing import Any

from apps.cockpit import services
from apps.cockpit.notion import NotionNichtEingerichtet, notion_client
from apps.kern.laeufe import LaufBefehl


class Command(LaufBefehl):
    help = (
        "Gleicht die Aufgaben im Hof-Backlog (Notion) mit den offenen Arbeitsposten ab: "
        "anlegen, aktualisieren, auf Erledigt setzen. Nur Zähler, NW-IDs, Fälligkeit, Link."
    )

    def ausfuehren(self, **optionen: Any) -> str:
        try:
            client = notion_client()
        except NotionNichtEingerichtet as fehler:
            return str(fehler)
        ergebnis = services.aufgaben_abgleichen(client)
        self.gebucht(len(ergebnis.angelegt) + len(ergebnis.aktualisiert) + len(ergebnis.erledigt))
        self.fehler(len(ergebnis.fehler))
        return f"Hof-Backlog: {ergebnis.text()}."
