"""Arbeitslisten als Posteingang (Review 2, B2; Review 1, Punkte 5 und 8).

Links stehen die Arten mit ihrer Zahl, rechts die Posten. Jeder Posten lässt sich direkt in
der Liste bearbeiten: Eine App meldet für ihre Art an, was in der Zeile steht (Vorlage und
Kontext), wie „Übernehmen“ den Vorschlag bucht und welche Posten dieselbe Ursache haben
(Gruppen mit „für alle anwenden“)::

    posteingang.registrieren(
        posteingang.Bearbeitung(
            art="Umsätze einordnen",
            vorlage="kassenbuch/_einordnen_zeile.html",
            kontext=zeilen_kontext,
            uebernehmen=uebernehmen,
            gruppen=gruppen,
            gruppe_anwenden=gruppe_anwenden,
            bearbeiten=lambda posten: reverse(...),
        )
    )

Die Felder einer Zeile heißen ``p<ID>-<feld>``, die einer Gruppe ``g<Nr>-<feld>``; ``werte()``
liest sie ohne Vorsilbe. Jede Zeile trägt ``p<ID>-da``: So weiß ``uebernehmen``, ob die Werte
aus der Zeile kommen oder ob es den Vorschlag ohne Änderung nehmen soll.

Namen aus dem Bezug (Gegenpartei, Mieter) liest der Kontext beim Anzeigen aus dem Bezug. Der
Hinweistext eines Postens bleibt ohne Personendaten, denn er geht nach Notion und ins Log.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from apps.kern.models import Arbeitsposten, Benutzer


class PostenFehler(ValueError):
    """Der Vorschlag lässt sich nicht übernehmen; der Text ist ein ganzer deutscher Satz."""


@dataclass
class Ergebnis:
    """Was nach einer Aktion gemeldet wird: was passiert ist, mit Link auf das Ergebnis."""

    text: str
    link: str = ""
    link_text: str = "ansehen"
    #: Weitere Posten haben sich dabei geändert: Die Liste lädt neu.
    weitere: bool = False


@dataclass
class Gruppe:
    """Posten mit derselben Ursache (Review 1, Punkt 5), etwa dieselbe Gegenpartei ohne
    Regel. ``vorlage`` zeigt das Formular „für alle anwenden“ im Kopf der Gruppe."""

    schluessel: str
    titel: str
    posten: list[Arbeitsposten]
    hinweis: str = ""
    vorlage: str = ""
    kontext: dict[str, Any] = field(default_factory=dict)
    #: Vorsilbe der Felder im Formular der Gruppe (vergibt die Liste: g1, g2, …)
    praefix: str = ""


Kontext = Callable[[Sequence[Arbeitsposten]], dict[int, dict[str, Any]]]
Uebernehmen = Callable[[Arbeitsposten, Benutzer, Mapping[str, str]], Ergebnis]
Gruppieren = Callable[[Sequence[Arbeitsposten], Mapping[int, dict[str, Any]]], list[Gruppe]]
GruppeAnwenden = Callable[[Sequence[Arbeitsposten], Benutzer, Mapping[str, str]], Ergebnis]


@dataclass(frozen=True)
class Bearbeitung:
    """Wie eine Art in der Liste bearbeitet wird.

    - ``vorlage``: die Arbeitsfläche in der Zeile; sie bekommt ``posten``, ``p`` (Vorsilbe
      der Felder) und den Kontext des Postens.
    - ``kontext``: je Posten ein Wörterbuch für die Vorlage. ``titel`` ersetzt die Art als
      Überschrift der Zeile, ``kann_uebernehmen`` schaltet „Übernehmen“ und Enter frei.
    - ``uebernehmen``: übernimmt den Vorschlag (oder die Werte der Zeile).
    - ``gruppen`` und ``gruppe_anwenden``: Bündeln und „für alle anwenden“.
    - ``bearbeiten``: Adresse der ausführlichen Bearbeitung (Taste e). Antwortet sie auf
      HTMX mit einem Dialog, öffnet sie sich als Dialog.
    """

    art: str
    vorlage: str = ""
    kontext: Kontext | None = None
    uebernehmen: Uebernehmen | None = None
    gruppen: Gruppieren | None = None
    gruppe_anwenden: GruppeAnwenden | None = None
    bearbeiten: Callable[[Arbeitsposten], str] | None = None
    #: Öffnet ``bearbeiten`` einen Dialog (hx-get nach #dialog-inhalt) oder eine Seite?
    bearbeiten_als_dialog: bool = False


_ARTEN: dict[str, Bearbeitung] = {}


def registrieren(bearbeitung: Bearbeitung) -> None:
    _ARTEN[bearbeitung.art] = bearbeitung


def fuer_art(art: str) -> Bearbeitung | None:
    return _ARTEN.get(art)


def praefix(posten: Arbeitsposten) -> str:
    return f"p{posten.pk}"


def werte(daten: Mapping[str, Any], vorsilbe: str) -> dict[str, str]:
    """Die Felder mit ``<vorsilbe>-`` aus den Formulardaten, ohne Vorsilbe. Leer, wenn die
    Zeile nicht mitgeschickt wurde (dann gilt der Vorschlag)."""
    kopf = f"{vorsilbe}-"
    ergebnis: dict[str, str] = {}
    for schluessel in daten:
        if schluessel.startswith(kopf):
            wert = daten[schluessel]
            ergebnis[schluessel.removeprefix(kopf)] = str(wert)
    return ergebnis


def aus_zeile(werte_: Mapping[str, str]) -> bool:
    """Kommen die Werte aus der Zeile (Marke ``da``)? Sonst gilt der Vorschlag."""
    return werte_.get("da") == "1"
