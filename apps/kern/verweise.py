"""Verweise an Arbeitsposten: ein Link je Art des Bezugs.

Ein Arbeitsposten zeigt neben seinen Aktionen einen Link zu der Seite, auf der man ihn
bearbeitet, etwa „Freigabe erneuern“ bei einer ablaufenden Bankfreigabe. Apps melden je
Modell des Bezugs an, wohin der Link führt::

    verweise.registrieren(Bankfreigabe, "Freigabe erneuern", lambda f: reverse(...))

Mit ``beschreibung`` nennt der Posten in der Arbeitsliste seinen Bezug in Worten, etwa
„Vertrag V-0005 · Tom Beispiel“. Die Beschreibung entsteht beim Anzeigen aus dem Bezug und
darf Namen enthalten (Max' Oberfläche); in den Hinweistext des Postens gehören sie nie.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.db import models

from apps.kern.models import Arbeitsposten


@dataclass(frozen=True)
class Verweis:
    titel: str
    url: str
    #: Der Bezug in Worten (nur für die Oberfläche), sonst leer.
    text: str = ""


_VERWEISE: dict[
    type[models.Model], tuple[str, Callable[[Any], str], Callable[[Any], str] | None]
] = {}


def registrieren(
    modell: type[models.Model],
    titel: str,
    url_fuer: Callable[[Any], str],
    *,
    beschreibung: Callable[[Any], str] | None = None,
) -> None:
    _VERWEISE[modell] = (titel, url_fuer, beschreibung)


def fuer_posten(posten: Arbeitsposten) -> Verweis | None:
    """Der Link zum Bezug des Postens, falls dessen App einen angemeldet hat."""
    typ = posten.bezug_typ
    if typ is None or posten.bezug_id is None:
        return None
    modell = typ.model_class()
    if modell not in _VERWEISE:
        return None
    bezug = posten.bezug
    if bezug is None:
        return None
    titel, url_fuer, beschreibung = _VERWEISE[modell]
    return Verweis(
        titel=titel, url=url_fuer(bezug), text=beschreibung(bezug) if beschreibung else ""
    )
