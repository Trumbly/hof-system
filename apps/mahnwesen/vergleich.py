"""Vergleich mit dem alten Mahnlauf (``mahnwesen_vergleich``, Abnahme Schritt 7).

Der neue Mahnlauf soll vier Wochen lang dieselben Fälle und Stufen vorschlagen wie der alte
(Standardablauf 3). Der Bericht nennt je Woche (Montag bis Sonntag)

- die Vorschläge des Laufs: je Stufe die Anzahl und die NW-IDs,
- die offenen Fälle je aktueller Stufe und die gesperrten (NW-IDs),
- mit ``--alt`` die Abweichungen zur Exportdatei des alten Laufs: je NW-ID die Stufe neu
  und alt.

Nur Zähler, Stufen und NW-IDs, keine Namen und keine Beträge (CLAUDE.md, Abschnitt 1). Der
Vergleich ändert nichts.

**Format der Exportdatei (Annahme, bis Max das echte Format nennt):** CSV mit Semikolon und
Kopfzeile, eine Zeile je vorgeschlagener Mahnung: Woche (Datum eines Tages der Woche,
``JJJJ-MM-TT`` oder ``TT.MM.JJJJ``), NW-ID, Stufe (Erinnerung, Mahnung, Abmahnung,
Kündigung). Die Spaltenköpfe und die Namen der Stufen stehen an einer Stelle: ``SPALTEN``
und ``STUFEN_ALT``. Beispiel: ``tests/fixtures/mahnwesen/alter_mahnlauf.csv``.
"""

from __future__ import annotations

import csv
import datetime
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from apps.mahnwesen import services
from apps.mahnwesen.models import Mahnschritt

#: Spaltenköpfe der Exportdatei des alten Laufs (eine Zuordnung an einer Stelle).
SPALTEN = {"woche": "Woche", "nw_id": "NW-ID", "stufe": "Stufe"}
TRENNZEICHEN = ";"
#: Stufen des alten Laufs (klein geschrieben) → Schlüssel der Mahnstufe.
STUFEN_ALT = {
    "erinnerung": "erinnerung",
    "zahlungserinnerung": "erinnerung",
    "mahnung": "mahnung",
    "abmahnung": "abmahnung",
    "kündigung": "kuendigung",
    "kuendigung": "kuendigung",
}
EIN_TAG = datetime.timedelta(days=1)


class VergleichFehler(Exception):
    """Die Exportdatei passt nicht. Die Meldung nennt nur Spaltenköpfe und Zeilennummern."""


def montag(tag: datetime.date) -> datetime.date:
    return tag - datetime.timedelta(days=tag.weekday())


@dataclass
class Woche:
    montag: datetime.date
    #: Stufe → NW-IDs der Vorschläge dieser Woche
    vorschlaege: dict[str, list[str]] = field(default_factory=dict)
    #: aktuelle Stufe der offenen Fälle am Sonntag → Anzahl
    je_stufe: Counter[str] = field(default_factory=Counter)
    offen: int = 0
    gesperrt: list[str] = field(default_factory=list)

    @property
    def sonntag(self) -> datetime.date:
        return self.montag + datetime.timedelta(days=6)

    def nach_nw_id(self) -> dict[str, str]:
        """NW-ID → vorgeschlagene Stufe (die höchste, falls mehrere)."""
        ergebnis: dict[str, str] = {}
        for stufe, ids in self.vorschlaege.items():
            for nw_id in ids:
                ergebnis[nw_id] = stufe
        return ergebnis


def wochen(von: datetime.date, bis: datetime.date) -> list[Woche]:
    """Je Woche von ``von`` bis ``bis`` die Vorschläge und Fälle des neuen Laufs."""
    ergebnis = []
    tag = montag(von)
    while tag <= bis:
        woche = Woche(montag=tag)
        schritte = list(services.schritte_im_zeitraum(tag, woche.sonntag))
        for schritt in sorted(schritte, key=lambda s: s.stufe.reihenfolge):
            ids = woche.vorschlaege.setdefault(schritt.stufe.schluessel, [])
            ids.extend(services.nw_ids([schritt.fall.vertrag]))
        for ids in woche.vorschlaege.values():
            ids.sort()
        gesperrt = services.gesperrte_vertraege(tag, woche.sonntag)
        for fall in services.faelle_offen_zwischen(tag, woche.sonntag):
            if fall.vertrag_id in gesperrt:
                woche.gesperrt.extend(services.nw_ids([fall.vertrag]))
                continue
            letzter = (
                Mahnschritt.objects.filter(fall=fall, stichtag__lte=woche.sonntag)
                .select_related("stufe")
                .order_by("-stichtag", "-pk")
                .first()
            )
            woche.offen += 1
            woche.je_stufe[letzter.stufe.schluessel if letzter else "ohne Stufe"] += 1
        woche.gesperrt.sort()
        ergebnis.append(woche)
        tag += datetime.timedelta(days=7)
    return ergebnis


def _datum(text: str) -> datetime.date:
    text = text.strip()
    for format_ in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.datetime.strptime(text, format_).date()
        except ValueError:
            continue
    raise ValueError(text)


def alt_lesen(pfad: Path) -> dict[datetime.date, dict[str, str]]:
    """Liest die Exportdatei des alten Laufs: Montag → {NW-ID: Stufe}."""
    with pfad.open(encoding="utf-8-sig", newline="") as datei:
        leser = csv.DictReader(datei, delimiter=TRENNZEICHEN)
        fehlend = [k for k in SPALTEN.values() if k not in (leser.fieldnames or [])]
        if fehlend:
            raise VergleichFehler(f"Spalten fehlen in der Exportdatei: {', '.join(fehlend)}")
        ergebnis: dict[datetime.date, dict[str, str]] = {}
        for nummer, zeile in enumerate(leser, start=2):
            try:
                tag = montag(_datum(zeile[SPALTEN["woche"]]))
            except ValueError as fehler:
                raise VergleichFehler(f"Zeile {nummer}: Datum unlesbar.") from fehler
            stufe = STUFEN_ALT.get(zeile[SPALTEN["stufe"]].strip().casefold())
            if stufe is None:
                raise VergleichFehler(f"Zeile {nummer}: unbekannte Stufe.")
            nw_id = zeile[SPALTEN["nw_id"]].strip().upper()
            ergebnis.setdefault(tag, {})[nw_id] = stufe
    return ergebnis


@dataclass(frozen=True)
class Abweichung:
    montag: datetime.date
    nw_id: str
    neu: str
    alt: str


FEHLT = "–"


def abweichungen(liste: list[Woche], alt: dict[datetime.date, dict[str, str]]) -> list[Abweichung]:
    """Je Woche und NW-ID: Stufe neu gegen alt (fehlt auf einer Seite: „–“)."""
    ergebnis = []
    for woche in liste:
        neu = woche.nach_nw_id()
        vorher = alt.get(woche.montag, {})
        for nw_id in sorted(set(neu) | set(vorher)):
            if neu.get(nw_id) != vorher.get(nw_id):
                ergebnis.append(
                    Abweichung(woche.montag, nw_id, neu.get(nw_id, FEHLT), vorher.get(nw_id, FEHLT))
                )
    return ergebnis


def text(liste: list[Woche], abw: list[Abweichung] | None = None) -> str:
    zeilen = []
    for woche in liste:
        zeilen.append(
            f"Woche ab {woche.montag:%d.%m.%Y}: {woche.offen} Fälle offen, "
            f"{len(woche.gesperrt)} gesperrt"
            + (f" ({', '.join(woche.gesperrt)})" if woche.gesperrt else "")
        )
        if woche.je_stufe:
            zeilen.append(
                "  Stand: " + ", ".join(f"{s} {n}" for s, n in sorted(woche.je_stufe.items()))
            )
        if not woche.vorschlaege:
            zeilen.append("  Vorschläge: keine")
        for stufe, ids in woche.vorschlaege.items():
            zeilen.append(f"  Vorschlag {stufe}: {len(ids)} ({', '.join(ids)})")
        if abw is not None:
            eigene = [a for a in abw if a.montag == woche.montag]
            zeilen.append(f"  Abweichungen zum alten Lauf: {len(eigene)}")
            zeilen.extend(f"    {a.nw_id}: neu {a.neu}, alt {a.alt}" for a in eigene)
    return "\n".join(zeilen)
