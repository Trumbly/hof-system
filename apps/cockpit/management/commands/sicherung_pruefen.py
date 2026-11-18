"""Lauf ``sicherung_pruefen`` (Tageslauf, Schritt 9 „Sichern“): Ist die letzte Sicherung gut?

Liest die Statusdatei von scripts/backup.sh (``HOF_BACKUP_STATUS``, Standard
/var/lib/hof/backup-status.json). Fehlt sie, steht ``ergebnis`` nicht auf „ok“ oder ist
sie älter als ``HOF_BACKUP_HOECHSTALTER_STUNDEN`` (26), zählt das als Fehler, und es
entsteht ein Arbeitsposten „Sicherung prüfen“ (höchstens einer offen). Ist die Sicherung
wieder in Ordnung, schließt sich der Posten.
"""

from __future__ import annotations

from typing import Any

from django.utils import timezone

from apps.cockpit import services
from apps.kern.laeufe import LaufBefehl


class Command(LaufBefehl):
    help = "Prüft die Statusdatei der letzten Sicherung (Alter höchstens 26 Stunden, Ergebnis ok)."

    def ausfuehren(self, **optionen: Any) -> str:
        pruefung = services.sicherung_pruefen(lauf=self.lauf)
        stand = pruefung.stand
        if pruefung.neuer_posten is not None:
            self.koerbe.arbeitsposten += 1
        if not stand.ok:
            self.fehler()
            meldung = f"Sicherung nicht in Ordnung: {stand.grund}."
            if pruefung.neuer_posten is not None:
                meldung += f" Arbeitsposten #{pruefung.neuer_posten.pk}."
            return meldung
        zeit = timezone.localtime(stand.zeit) if stand.zeit else None
        meldung = (
            f"Sicherung vom {zeit:%d.%m.%Y %H:%M} in Ordnung "
            f"({stand.alter_stunden or 0:.0f} Stunden alt)."
        )
        if pruefung.geschlossen:
            meldung += f" {pruefung.geschlossen} Arbeitsposten geschlossen."
        return meldung
