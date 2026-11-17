"""Schnittstelle zum Hof-Backlog in Notion (H6.1, Standardablauf 6 Schritt 4).

Arbeitsposten, die als Aufgabe im Hof-Backlog erscheinen sollen (etwa „Rechnung zahlen“),
meldet die App, die sie anlegt oder schließt, mit::

    from apps.kern import aufgaben

    aufgaben.melden(posten)

``melden`` bekommt den Arbeitsposten in seinem aktuellen Stand: offen (neue Aufgabe anlegen
oder aktualisieren) oder erledigt (Aufgabe schließen). Die Funktion darf nichts am Posten
ändern und wirft keine Fehler nach außen; scheitert die Übertragung, bleibt der Posten im
Hof-System trotzdem gültig.

Vorerst tut ``melden`` nichts. Schritt 9 setzt die Übertragung nach Notion um (Client als
austauschbare Klasse mit Fake, ohne Personendaten: nur Art, Fälligkeit, Link zum Posten).
"""

from __future__ import annotations

from apps.kern.models import Arbeitsposten


def melden(arbeitsposten: Arbeitsposten) -> None:
    """Meldet einen Arbeitsposten an das Hof-Backlog. Bis Schritt 9: ohne Wirkung."""
    return None
