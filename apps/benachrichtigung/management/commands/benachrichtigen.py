"""Lauf ``benachrichtigen``: meldet gescheiterte Läufe und wartende Freigaben (H11.1)."""

from __future__ import annotations

from typing import Any

from apps.benachrichtigung import services
from apps.kern.laeufe import LaufBefehl


class Command(LaufBefehl):
    help = (
        "Meldet sich per Mail und ntfy, wenn ein Lauf gescheitert ist oder Freigaben warten "
        "(nur Zähler und Link). Sonst bleibt es still; dieselbe Lage höchstens einmal am Tag."
    )

    def ausfuehren(self, **optionen: Any) -> str:
        ergebnis = services.benachrichtigen(lauf=self.lauf)
        self.fehler(len(ergebnis.fehler))
        lage = ergebnis.lage
        zaehler = (
            f"{sum(lage.gescheitert.values())} Läufe gescheitert, "
            f"{sum(lage.freigaben.values())} Freigaben offen"
        )
        if ergebnis.status == "still":
            return "Nichts zu melden."
        if ergebnis.status == "schon_gemeldet":
            return f"{zaehler}; heute schon gemeldet."
        if ergebnis.status == "gemeldet" and ergebnis.benachrichtigung is not None:
            meldung = (
                f"{zaehler}; Benachrichtigung #{ergebnis.benachrichtigung.pk} über "
                f"{', '.join(ergebnis.gesendet)}."
            )
            if ergebnis.fehler:
                meldung += f" Gescheitert: {', '.join(ergebnis.fehler)}."
            return meldung
        return f"{zaehler}; nicht gemeldet, gescheitert: {', '.join(ergebnis.fehler)}."
