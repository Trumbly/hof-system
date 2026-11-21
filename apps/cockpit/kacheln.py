"""Register der Kacheln auf der Startseite (Hof-Cockpit, H10.1).

Jede App meldet ihre Kachel selbst an, in ``AppConfig.ready()``::

    from apps.cockpit import kacheln


    def _rueckstaende() -> dict[str, Any]:
        # liest direkt aus der Datenbank, keine Kopien
        return {"rueckstaende": services.rueckstaende()}


    kacheln.kachel_anmelden(
        "rueckstaende",                       # Name, eindeutig
        "abgleich/_kachel_rueckstaende.html",  # Vorlage
        _rueckstaende,                        # liefert den Kontext der Vorlage
        reihenfolge=kacheln.RUECKSTAENDE,     # Rang 30, siehe unten
    )

Die Vorlage ist genau ein ``<li id="kachel-<name>">`` mit ``{% karte "Titel" zahl=…
ziel=… %}``: Titel ohne Doppelpunkt, die Zahl als Kapsel, die ganze Karte ein Link auf die
Liste, zu der die Kachel gehört; darunter die Zeilen (``.kachel-zeilen``). Sie zeigt nur
Zähler, Summen, NW-IDs und Daten, nie Namen von Personen. Die Kontextfunktion bekommt keine
Argumente und liest bei jedem Aufruf der Startseite neu aus der Datenbank.

Platzhalter: Eine Kachel mit ``platzhalter=True`` hält einen Platz frei, bis die App, die
ihn füllt, ihre Kachel unter demselben Namen anmeldet. Ein Platzhalter ersetzt nie eine
echte Kachel, eine echte Kachel ersetzt immer den Platzhalter; die Reihenfolge der
``ready()``-Aufrufe spielt keine Rolle. So steht „Rückstände“ schon da, bevor Schritt 6
(``abgleich``) seine Kachel anmeldet.

Arten: Zeigt eine Kachel selbst offene Arbeitsposten einer Art (z. B. „Belege zuordnen“),
meldet sie diese mit ``arten=(…)`` an. Die Kachel „Offene Freigaben“ lässt diese Arten
dann weg, damit nichts doppelt steht (Review 1, Punkt 7).

Schlägt die Kontextfunktion fehl, zeigt das Cockpit an ihrer Stelle einen Hinweis; die
übrigen Kacheln bleiben sichtbar.

Diese Datei importiert keine Modelle; sie darf in jedem ``ready()`` geladen werden.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# --- Ränge der Kacheln (kleiner steht weiter oben) -----------------------------------

FREIGABEN = 10  # offene Arbeitsposten je Art (cockpit)
BELEGE = 15  # Belege zuordnen (dokumente)
FRISTEN = 20  # Fristen der nächsten 14 Tage (fristen)
RUECKSTAENDE = 30  # Rückstände (Platzhalter, bis abgleich anmeldet)
MAHNFAELLE = 35  # Mahnfälle je Stufe und gesperrte (mahnwesen)
OFFENE_POSTEN = 40  # offene Sollposten (soll)
EINGANGSRECHNUNGEN = 45  # offene Eingangsrechnungen, fällig in 14 Tagen (dokumente)
LEERSTAND = 50  # Leerstand (stammdaten)
DATENSTAND = 60  # Lücken im Datenstand (stammdaten, auch Zähler)
WOHNRAUM = 70  # Kennzahlen aus Verwaltio (anbindungen)
LAEUFE = 80  # Zustand der Läufe (cockpit)
SICHERUNG = 90  # letzte Sicherung (cockpit)

Kontext = Callable[[], dict[str, Any]]


@dataclass(frozen=True)
class Kachel:
    name: str
    template: str
    kontext_funktion: Kontext
    reihenfolge: int
    platzhalter: bool = False
    #: Arten offener Arbeitsposten, die diese Kachel selbst zeigt.
    arten: tuple[str, ...] = ()


_KACHELN: dict[str, Kachel] = {}


def kachel_anmelden(
    name: str,
    template: str,
    kontext_funktion: Kontext,
    reihenfolge: int,
    *,
    platzhalter: bool = False,
    arten: tuple[str, ...] = (),
) -> Kachel:
    """Meldet eine Kachel für die Startseite an (siehe Modul-Doku)."""
    kachel = Kachel(
        name=name,
        template=template,
        kontext_funktion=kontext_funktion,
        reihenfolge=reihenfolge,
        platzhalter=platzhalter,
        arten=tuple(arten),
    )
    vorhanden = _KACHELN.get(name)
    if platzhalter and vorhanden is not None and not vorhanden.platzhalter:
        return vorhanden
    _KACHELN[name] = kachel
    return kachel


def kachel_abmelden(name: str) -> None:
    _KACHELN.pop(name, None)


def kacheln() -> list[Kachel]:
    """Alle Kacheln in der Reihenfolge der Startseite."""
    return sorted(_KACHELN.values(), key=lambda k: (k.reihenfolge, k.name))


def eigene_arten() -> set[str]:
    """Arten offener Arbeitsposten, die eine eigene Kachel zeigt."""
    return {art for kachel in _KACHELN.values() for art in kachel.arten}
