"""Lauf ``arbeitsposten_pflege`` (Review 1, Punkt 3): schließt Posten ohne Bezug.

Ein offener oder zurückgestellter Arbeitsposten, dessen Bezug es nicht mehr gibt (etwa
nach ``testhof --leeren`` oder einem gelöschten Testobjekt), wird erledigt. Der Hinweis
bekommt einen Vermerk mit Bezug und Lauf, das Protokoll hält die Änderung mit dem Lauf als
Kontext fest. Läuft im Tageslauf vor dem Hof-Backlog und der Benachrichtigung.
"""

from __future__ import annotations

from typing import Any

from apps.kern import services
from apps.kern.laeufe import LaufBefehl


class Command(LaufBefehl):
    help = "Schließt Arbeitsposten, deren Bezug fehlt, und protokolliert das (nur IDs)."

    def ausfuehren(self, **optionen: Any) -> str:
        geschlossen = services.verwaiste_arbeitsposten_schliessen(lauf=self.lauf)
        if not geschlossen:
            return "Kein Arbeitsposten ohne Bezug."
        nummern = ", ".join(f"#{p.pk}" for p in geschlossen)
        return f"{len(geschlossen)} Arbeitsposten ohne Bezug geschlossen: {nummern}."
