"""Verweise an Arbeitsposten: ein Link je Art des Bezugs.

Ein Arbeitsposten zeigt neben seinen Aktionen einen Link zu der Seite, auf der man ihn
bearbeitet, etwa „Freigabe erneuern“ bei einer ablaufenden Bankfreigabe. Apps melden je
Modell des Bezugs an, wohin der Link führt::

    verweise.registrieren(Bankfreigabe, "Freigabe erneuern", lambda f: reverse(...))
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


_VERWEISE: dict[type[models.Model], tuple[str, Callable[[Any], str]]] = {}


def registrieren(modell: type[models.Model], titel: str, url_fuer: Callable[[Any], str]) -> None:
    _VERWEISE[modell] = (titel, url_fuer)


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
    titel, url_fuer = _VERWEISE[modell]
    return Verweis(titel=titel, url=url_fuer(bezug))
