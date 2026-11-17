"""Liest ``konfiguration.toml`` der Sollstellung: Fälligkeit und Spalten des Vergleichs.

Die Datei ist die eine Stelle für diese Regeln; der Code liest sie nur über diese Funktionen.
"""

from __future__ import annotations

import functools
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from apps.stammdaten.fristrechnung import LANDESFEIERTAGE

DATEI = Path(__file__).with_name("konfiguration.toml")

FAELLIGKEITSARTEN = ("werktag", "kalendertag")
#: Felder des Vergleichs, ohne die er nicht läuft.
VERGLEICH_PFLICHT = ("art", "von", "bis", "betrag")
SOLLARTEN = ("miete", "pauschale", "kaution", "sonstiges")


class KonfigurationFehler(Exception):
    """Die Konfiguration der Sollstellung ist unvollständig oder widersprüchlich."""


@dataclass(frozen=True)
class Faelligkeit:
    art: str
    tag: int
    land: str | None
    samstag_ist_werktag: bool


@dataclass(frozen=True)
class Vergleichsformat:
    trennzeichen: str
    dezimaltrennzeichen: str
    datumsformat: str
    herkunft_praefix: str
    spalten: dict[str, str]
    arten: dict[str, str]


@dataclass(frozen=True)
class Konfiguration:
    faelligkeit: Faelligkeit
    vergleich: Vergleichsformat


def faelligkeit_pruefen(art: str, tag: int) -> None:
    if art not in FAELLIGKEITSARTEN:
        raise KonfigurationFehler(f"Unbekannte Art der Fälligkeit: {art}")
    grenze = 10 if art == "werktag" else 31
    if not 1 <= tag <= grenze:
        raise KonfigurationFehler(f"Fälligkeit {art}: Tag {tag} außerhalb von 1 bis {grenze}")


def _lesen(daten: dict[str, Any]) -> Konfiguration:
    f = daten["faelligkeit"]
    art, tag = str(f["art"]), int(f["tag"])
    faelligkeit_pruefen(art, tag)
    land = str(f.get("land") or "") or None
    if land is not None and land not in LANDESFEIERTAGE:
        raise KonfigurationFehler(f"Feiertage für das Land {land} sind nicht hinterlegt.")

    v = daten["vergleich_hellohousing"]
    spalten = {str(k): str(w) for k, w in v["spalten"].items()}
    fehlend = [feld for feld in VERGLEICH_PFLICHT if feld not in spalten]
    if fehlend:
        raise KonfigurationFehler(f"Vergleich: Spalten fehlen in der Zuordnung: {fehlend}")
    if "mietverhaeltnis" not in spalten and "einheit" not in spalten:
        raise KonfigurationFehler("Vergleich: Mietverhältnis oder Einheit muss zugeordnet sein.")
    arten = {str(k): str(w) for k, w in v["arten"].items()}
    unbekannt = sorted(set(arten.values()) - set(SOLLARTEN))
    if unbekannt:
        raise KonfigurationFehler(f"Vergleich: unbekannte Arten {unbekannt}")
    return Konfiguration(
        faelligkeit=Faelligkeit(
            art=art,
            tag=tag,
            land=land,
            samstag_ist_werktag=bool(f.get("samstag_ist_werktag", False)),
        ),
        vergleich=Vergleichsformat(
            trennzeichen=str(v.get("trennzeichen", ";")),
            dezimaltrennzeichen=str(v.get("dezimaltrennzeichen", ",")),
            datumsformat=str(v["datumsformat"]),
            herkunft_praefix=str(v.get("herkunft_praefix", "")),
            spalten=spalten,
            arten=arten,
        ),
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
