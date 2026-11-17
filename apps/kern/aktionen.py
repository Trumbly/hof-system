"""Aktionen an Arbeitsposten.

Ein Arbeitsposten nennt in ``aktionen`` die Schlüssel der Aktionen, die die
Arbeitsliste als Knöpfe zeigt. Der Kern bringt die allgemeinen mit (erledigen,
zurückstellen, wieder öffnen); spätere Apps melden ihre eigenen an, etwa
„zuordnen“ oder „senden“:

    aktionen.registrieren("zuordnen", "Zuordnen", meine_funktion)

Die Funktion bekommt den Posten und den Benutzer und schreibt über die Services
ihrer App.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from apps.kern import services
from apps.kern.models import Arbeitsposten, Benutzer

Ausfuehrung = Callable[[Arbeitsposten, Benutzer], object]


@dataclass(frozen=True)
class Aktion:
    schluessel: str
    titel: str
    ausfuehren: Ausfuehrung


_AKTIONEN: dict[str, Aktion] = {}

# Wieder öffnen steht an jedem zurückgestellten Posten, auch ohne Eintrag in ``aktionen``.
WIEDER_OEFFNEN = "wieder_oeffnen"


def registrieren(schluessel: str, titel: str, ausfuehren: Ausfuehrung) -> None:
    if schluessel in _AKTIONEN and _AKTIONEN[schluessel].ausfuehren is not ausfuehren:
        raise ValueError(f"Aktion {schluessel} ist schon registriert.")
    _AKTIONEN[schluessel] = Aktion(schluessel, titel, ausfuehren)


def ist_registriert(schluessel: str) -> bool:
    return schluessel in _AKTIONEN


def holen(schluessel: str) -> Aktion:
    return _AKTIONEN[schluessel]


def fuer_posten(posten: Arbeitsposten) -> list[Aktion]:
    """Die Aktionen, die ein Posten in seinem Status gerade anbietet."""
    if posten.status == Arbeitsposten.Status.ZURUECKGESTELLT:
        return [_AKTIONEN[WIEDER_OEFFNEN]]
    if posten.status != Arbeitsposten.Status.OFFEN:
        return []
    return [_AKTIONEN[s] for s in posten.aktionen if s in _AKTIONEN]


def erlaubt(posten: Arbeitsposten, schluessel: str) -> bool:
    return any(a.schluessel == schluessel for a in fuer_posten(posten))


registrieren("erledigen", "Erledigt", lambda p, b: services.arbeitsposten_erledigen(p))
registrieren(
    "zurueckstellen", "Zurückstellen", lambda p, b: services.arbeitsposten_zurueckstellen(p)
)
registrieren(WIEDER_OEFFNEN, "Wieder öffnen", lambda p, b: services.arbeitsposten_wieder_oeffnen(p))
