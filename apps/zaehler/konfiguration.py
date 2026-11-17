"""Liest ``konfiguration.toml`` der Zähler: Stichtag, Ablesefenster, Vorlauf der Eichfrist.

Die Datei ist die eine Stelle für diese Werte. Der Code liest sie nur über ``laden()``.
"""

from __future__ import annotations

import datetime
import functools
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DATEI = Path(__file__).with_name("konfiguration.toml")


class KonfigurationFehler(Exception):
    """Die Konfiguration ist unvollständig oder widersprüchlich."""


@dataclass(frozen=True)
class MonatTag:
    monat: int
    tag: int

    def im_jahr(self, jahr: int) -> datetime.date:
        return datetime.date(jahr, self.monat, self.tag)


@dataclass(frozen=True)
class Konfiguration:
    stichtag: MonatTag
    erinnern_ab: MonatTag
    nachlauf_tage: int
    eichfrist_vorlauf_tage: int


def _monat_tag(text: Any, schluessel: str) -> MonatTag:
    try:
        monat_text, tag_text = str(text).split("-")
        wert = MonatTag(int(monat_text), int(tag_text))
        # 2001 ist kein Schaltjahr: Der 29.02. gibt es nicht jedes Jahr und fällt hier auf.
        wert.im_jahr(2001)
    except ValueError as fehler:
        raise KonfigurationFehler(
            f"{DATEI.name}: {schluessel} = {text!r} ist kein Tag im Format MM-TT."
        ) from fehler
    return wert


def _lesen(daten: dict[str, Any]) -> Konfiguration:
    stichtag = daten.get("stichtag")
    eichfrist = daten.get("eichfrist")
    if not isinstance(stichtag, dict) or not isinstance(eichfrist, dict):
        raise KonfigurationFehler(f"[stichtag] oder [eichfrist] fehlt in {DATEI.name}.")
    nachlauf = int(stichtag["nachlauf_tage"])
    vorlauf = int(eichfrist["vorlauf_tage"])
    if not 0 <= nachlauf <= 90 or vorlauf < 0:
        raise KonfigurationFehler(f"{DATEI.name}: Tage außerhalb des Bereichs.")
    return Konfiguration(
        stichtag=_monat_tag(stichtag["datum"], "stichtag.datum"),
        erinnern_ab=_monat_tag(stichtag["erinnern_ab"], "stichtag.erinnern_ab"),
        nachlauf_tage=nachlauf,
        eichfrist_vorlauf_tage=vorlauf,
    )


@functools.cache
def laden() -> Konfiguration:
    """Die Konfiguration, einmal gelesen und geprüft."""
    with DATEI.open("rb") as datei:
        daten = tomllib.load(datei)
    try:
        return _lesen(daten)
    except (KeyError, TypeError, ValueError) as fehler:
        raise KonfigurationFehler(f"{DATEI.name} ist unvollständig: {fehler}") from fehler
