"""Liest ``konfiguration.toml``: Kündigungsregeln, Vertragsarten, Checklisten, Datenstand.

Die Datei ist die eine Stelle für diese Regeln. Der Code liest sie nur über die
Funktionen hier, nie direkt.
"""

from __future__ import annotations

import datetime
import functools
import tomllib
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

DATEI = Path(__file__).with_name("konfiguration.toml")

PERIODEN = ("monat", "quartal")


class KonfigurationFehler(Exception):
    """Die Konfiguration ist unvollständig oder widersprüchlich."""


@dataclass(frozen=True)
class Kuendigungsregel:
    schluessel: str
    titel: str
    periode: str
    perioden: int
    karenz_werktage: int


@dataclass(frozen=True)
class Vertragsart:
    kuerzel: str
    titel: str
    kuendigungsregel: str
    vorlage: str


@dataclass(frozen=True)
class Checkpunktvorlage:
    text: str
    #: Das System hakt den Punkt ab, sobald das Ereignis eintritt (z. B. „vertragsdokument“).
    automatisch: str = ""
    #: Nur bei einer Monatsmiete (netto) ab diesem Betrag (P8: Bonität ab 100 €).
    ab_miete: Decimal | None = None


#: Ereignisse, nach denen ein Punkt automatisch abgehakt wird.
AUTOMATISCH = ("vertragsdokument",)


@dataclass(frozen=True)
class Checklistenvorlage:
    phase: str
    titel: str
    punkte: tuple[str, ...]
    vorlagen: tuple[Checkpunktvorlage, ...] = field(default=())

    def punkte_fuer(self, miete: Decimal | None) -> tuple[Checkpunktvorlage, ...]:
        """Die Punkte für einen Vertrag mit dieser Monatsmiete (ohne Miete: ohne bedingte)."""
        return tuple(
            p
            for p in (self.vorlagen or tuple(Checkpunktvorlage(t) for t in self.punkte))
            if p.ab_miete is None or (miete is not None and miete >= p.ab_miete)
        )


@dataclass(frozen=True)
class Datenstandbereich:
    schluessel: str
    titel: str
    pflicht: tuple[str, ...]
    je_art: dict[str, tuple[str, ...]]
    typen: tuple[str, ...]

    def pflicht_fuer(self, art: str | None) -> tuple[str, ...]:
        if art is not None and art in self.je_art:
            return self.je_art[art]
        return self.pflicht


@dataclass(frozen=True)
class Konfiguration:
    samstag_ist_werktag: bool
    zusaetzliche_feiertage: frozenset[datetime.date]
    kuendigungsregeln: dict[str, Kuendigungsregel]
    vertragsarten: dict[str, Vertragsart]
    kaution_abrechnung_monate: int
    checklisten: dict[str, Checklistenvorlage]
    datenstand: dict[str, Datenstandbereich]
    #: P8: ab dieser Monatsmiete (netto) ist ein Bonitätsnachweis Pflicht.
    bonitaet_ab_miete: Decimal | None = None


def _tabelle(daten: dict[str, Any], schluessel: str) -> dict[str, Any]:
    wert = daten.get(schluessel)
    if not isinstance(wert, dict):
        raise KonfigurationFehler(f"Abschnitt [{schluessel}] fehlt in {DATEI.name}.")
    return wert


def _checkpunkt(phase: str, punkt: Any) -> Checkpunktvorlage:
    if isinstance(punkt, str):
        return Checkpunktvorlage(punkt)
    automatisch = str(punkt.get("automatisch", ""))
    if automatisch and automatisch not in AUTOMATISCH:
        raise KonfigurationFehler(f"Checkliste {phase}: unbekanntes Ereignis {automatisch}")
    ab = punkt.get("ab_miete")
    return Checkpunktvorlage(
        text=str(punkt["text"]),
        automatisch=automatisch,
        ab_miete=Decimal(str(ab)) if ab is not None else None,
    )


def _lesen(daten: dict[str, Any]) -> Konfiguration:
    kuendigung = _tabelle(daten, "kuendigung")
    regeln: dict[str, Kuendigungsregel] = {}
    for schluessel, regel in _tabelle(kuendigung, "regeln").items():
        periode = str(regel["periode"])
        if periode not in PERIODEN:
            raise KonfigurationFehler(f"Kündigungsregel {schluessel}: unbekannte Periode {periode}")
        perioden = int(regel["perioden"])
        karenz = int(regel["karenz_werktage"])
        if perioden < 1 or karenz < 0:
            raise KonfigurationFehler(f"Kündigungsregel {schluessel}: Werte außerhalb des Bereichs")
        regeln[schluessel] = Kuendigungsregel(
            schluessel=schluessel,
            titel=str(regel["titel"]),
            periode=periode,
            perioden=perioden,
            karenz_werktage=karenz,
        )

    arten: dict[str, Vertragsart] = {}
    for kuerzel, art in _tabelle(daten, "vertragsarten").items():
        regel_name = str(art["kuendigungsregel"])
        if regel_name not in regeln:
            raise KonfigurationFehler(
                f"Vertragsart {kuerzel}: unbekannte Kündigungsregel {regel_name}"
            )
        arten[kuerzel] = Vertragsart(
            kuerzel=kuerzel,
            titel=str(art["titel"]),
            kuendigungsregel=regel_name,
            vorlage=str(art["vorlage"]),
        )

    checklisten = {}
    for phase, liste in _tabelle(daten, "checklisten").items():
        vorlagen = tuple(_checkpunkt(phase, p) for p in liste["punkte"])
        checklisten[phase] = Checklistenvorlage(
            phase=phase,
            titel=str(liste["titel"]),
            punkte=tuple(p.text for p in vorlagen),
            vorlagen=vorlagen,
        )

    datenstand: dict[str, Datenstandbereich] = {}
    for schluessel, bereich in _tabelle(daten, "datenstand").items():
        je_art = {
            art: tuple(str(p) for p in werte["pflicht"])
            for art, werte in bereich.get("je_art", {}).items()
        }
        datenstand[schluessel] = Datenstandbereich(
            schluessel=schluessel,
            titel=str(bereich["titel"]),
            pflicht=tuple(str(p) for p in bereich["pflicht"]),
            je_art=je_art,
            typen=tuple(str(t) for t in bereich.get("typen", ())),
        )

    return Konfiguration(
        samstag_ist_werktag=bool(kuendigung.get("samstag_ist_werktag", False)),
        zusaetzliche_feiertage=frozenset(
            datetime.date.fromisoformat(str(t))
            for t in kuendigung.get("zusaetzliche_feiertage", [])
        ),
        kuendigungsregeln=regeln,
        vertragsarten=arten,
        kaution_abrechnung_monate=int(_tabelle(daten, "kaution")["abrechnung_monate"]),
        checklisten=checklisten,
        datenstand=datenstand,
        bonitaet_ab_miete=(
            Decimal(str(daten["bonitaet"]["ab_miete"])) if "bonitaet" in daten else None
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


def kuendigungsregel(schluessel: str) -> Kuendigungsregel:
    regeln = laden().kuendigungsregeln
    if schluessel not in regeln:
        raise KonfigurationFehler(f"Unbekannte Kündigungsregel {schluessel}")
    return regeln[schluessel]


def vertragsart(kuerzel: str) -> Vertragsart:
    arten = laden().vertragsarten
    if kuerzel not in arten:
        raise KonfigurationFehler(f"Vertragsart {kuerzel} fehlt in {DATEI.name}")
    return arten[kuerzel]


def checkliste(phase: str) -> Checklistenvorlage:
    listen = laden().checklisten
    if phase not in listen:
        raise KonfigurationFehler(f"Checkliste für Phase {phase} fehlt in {DATEI.name}")
    return listen[phase]
