"""Aktionen an Arbeitsposten.

Ein Arbeitsposten nennt in ``aktionen`` die Schlüssel der Aktionen, die die
Arbeitsliste als Knöpfe zeigt. Der Kern bringt die allgemeinen mit (erledigen,
zurückstellen, wieder öffnen); spätere Apps melden ihre eigenen an, etwa
„zuordnen“ oder „senden“:

    aktionen.registrieren("zuordnen", "Zuordnen", meine_funktion)

Die Funktion bekommt den Posten und den Benutzer und schreibt über die Services
ihrer App. Braucht eine Aktion eine Eingabe (etwa Kategorie und Objekt), meldet sie statt
der Funktion eine Seite an; der Knopf führt dann dorthin::

    aktionen.registrieren("umsatz_zuordnen", "Zuordnen", seite=lambda p: reverse(...))
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from apps.kern import services
from apps.kern.models import Arbeitsposten, Benutzer

Ausfuehrung = Callable[[Arbeitsposten, Benutzer], object]
Seite = Callable[[Arbeitsposten], str]


def _nur_ueber_seite(posten: Arbeitsposten, benutzer: Benutzer) -> object:
    raise RuntimeError("Diese Aktion läuft über ihre Seite.")


@dataclass(frozen=True)
class Aktion:
    schluessel: str
    titel: str
    ausfuehren: Ausfuehrung
    #: Adresse der Seite (oder des Formulars) für Aktionen mit Eingabe, sonst None.
    seite: Seite | None = None


_AKTIONEN: dict[str, Aktion] = {}

# Wieder öffnen steht an jedem zurückgestellten Posten, auch ohne Eintrag in ``aktionen``.
WIEDER_OEFFNEN = "wieder_oeffnen"


def registrieren(
    schluessel: str,
    titel: str,
    ausfuehren: Ausfuehrung | None = None,
    *,
    seite: Seite | None = None,
) -> None:
    """Meldet eine Aktion an: entweder mit ``ausfuehren`` (sofort) oder mit ``seite``."""
    if (ausfuehren is None) == (seite is None):
        raise ValueError(f"Aktion {schluessel}: genau eins von ausfuehren und seite angeben.")
    aktion = Aktion(schluessel, titel, ausfuehren or _nur_ueber_seite, seite)
    vorhanden = _AKTIONEN.get(schluessel)
    if vorhanden is not None and (vorhanden.ausfuehren, vorhanden.seite) != (
        aktion.ausfuehren,
        aktion.seite,
    ):
        raise ValueError(f"Aktion {schluessel} ist schon registriert.")
    _AKTIONEN[schluessel] = aktion


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


def _verweise_des_kerns() -> None:
    """Review 1, Punkt 8: Ein Posten mit einem Lauf als Bezug (etwa der Selbsttest) verlinkt
    auf den Lauf."""
    from django.urls import reverse

    from apps.kern import verweise
    from apps.kern.models import Lauf

    verweise.registrieren(
        Lauf,
        "Lauf ansehen",
        lambda lauf: f"{reverse('kern:laeufe')}#lauf-{lauf.pk}",
        beschreibung=lambda lauf: f"Lauf #{lauf.pk} {lauf.name}",
    )


_verweise_des_kerns()
