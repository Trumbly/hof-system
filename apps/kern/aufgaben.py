"""Aufgaben im Hof-Backlog (Notion): Schnittstelle ``melden(arbeitsposten)``.

``kern.services`` ruft ``melden`` bei jedem Anlegen, Erledigen, Zurückstellen und Wieder-
öffnen eines Arbeitspostens auf (zentral, kein Dienst muss selbst daran denken). Andere
Stellen, die einen Posten auf anderem Weg ändern, rufen ``melden`` ebenfalls auf.

``melden`` schreibt nichts nach Notion. Es gibt den Posten an die angemeldeten Empfänger
weiter; das Cockpit (``apps.cockpit.services.aufgabe_vormerken``) merkt die Aufgabe seiner
Art zum Abgleich vor, wenn die Art im Hof-Backlog geführt wird. Den Abgleich mit Notion
macht der Lauf ``aufgaben_abgleich`` (im Tageslauf und alle 30 Minuten). So scheitert
keine Buchung und kein Lauf daran, dass Notion nicht antwortet.

Ein Empfänger läuft in der Transaktion des Aufrufers: Wird die Änderung am Posten
zurückgerollt, verschwindet auch die Vormerkung. Ein Fehler im Empfänger wird nur
geloggt (mit der ID des Postens) und hält den Aufrufer nicht auf.

Einen Empfänger meldet eine App in ``AppConfig.ready()`` an::

    from apps.kern import aufgaben

    aufgaben.empfaenger_anmelden(meine_funktion)   # meine_funktion(posten) -> None
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from django.db import transaction

from apps.kern.models import Arbeitsposten

logger = logging.getLogger(__name__)

Empfaenger = Callable[[Arbeitsposten], None]

_EMPFAENGER: list[Empfaenger] = []


def empfaenger_anmelden(funktion: Empfaenger) -> None:
    """Meldet eine Funktion an, die jeden gemeldeten Arbeitsposten bekommt (einmal je
    Funktion, ein zweites Anmelden ändert nichts)."""
    if funktion not in _EMPFAENGER:
        _EMPFAENGER.append(funktion)


def melden(arbeitsposten: Arbeitsposten) -> None:
    """Ein Arbeitsposten ist neu, erledigt, zurückgestellt oder wieder offen."""
    for funktion in list(_EMPFAENGER):
        try:
            # Eigener Sicherungspunkt: Ein Fehler des Empfängers rollt nur dessen
            # Änderungen zurück, nicht die des Aufrufers.
            with transaction.atomic():
                funktion(arbeitsposten)
        except Exception:
            logger.exception(
                "Aufgabe zu Arbeitsposten #%s nicht vorgemerkt (%s)",
                arbeitsposten.pk,
                getattr(funktion, "__qualname__", funktion),
            )
