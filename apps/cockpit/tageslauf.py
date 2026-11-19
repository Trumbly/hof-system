"""Register des Tageslaufs: Welche Läufe der Lauf ``tageslauf`` in welcher Reihenfolge ruft.

Jede App meldet ihre täglichen Läufe selbst an, in ``AppConfig.ready()``::

    from django.apps import AppConfig


    class AbgleichConfig(AppConfig):
        name = "apps.abgleich"

        def ready(self) -> None:
            from apps.cockpit import tageslauf

            tageslauf.schritt_anmelden(
                "abgleich",                       # Name des Schritts (und des Befehls)
                reihenfolge=tageslauf.ABGLEICH,   # Rang 40, siehe unten
                abhaengig_von=("bank_abruf", "kassenbuch_einordnen"),
            )

    # Mahnwesen, nur montags:
    tageslauf.schritt_anmelden(
        "mahnlauf", reihenfolge=tageslauf.MAHNWESEN, nur_wochentag=0, abhaengig_von=("abgleich",)
    )

Regeln:

- ``name`` ist eindeutig. ``befehl`` ist der Management-Befehl (Standard: ``name``); er
  muss von ``apps.kern.laeufe.LaufBefehl`` erben, damit jeder Schritt ein eigener Lauf im
  Laufprotokoll ist. Kennt der Befehl ``--stichtag``, gibt der Tageslauf seinen Stichtag
  weiter; ``optionen`` sind feste weitere Optionen (z. B. ``{"ab": ...}``).
- ``reihenfolge`` ist ein Rang; kleiner läuft früher. Die Ränge aus dem Bauplan
  (Abschnitt „Dienste und Oberfläche“) sind unten als Konstanten fest vergeben; eine App
  nimmt ihren Rang oder einen freien Zwischenrang (z. B. 25 nach dem Einordnen, 65 nach den
  Fristen). Bei gleichem Rang entscheidet der Name.
- ``nur_wochentag``: 0 = Montag … 6 = Sonntag, oder eine Menge davon; ``None`` = täglich.
- ``abhaengig_von``: Namen der Schritte, deren Ergebnis dieser Schritt braucht. Ist einer
  davon im selben Tageslauf abgebrochen oder mit Fehlern geendet, läuft der Schritt
  trotzdem, und der Tageslauf vermerkt das in seiner Meldung.
- Ein zweites Anmelden unter demselben Namen ersetzt den Schritt (etwa in Tests).

Diese Datei importiert keine Modelle; sie darf in jedem ``ready()`` geladen werden.
"""

from __future__ import annotations

import datetime
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

# --- Ränge aus dem Bauplan (Reihenfolge des Tageslaufs) ------------------------------

BANK = 10  # 1. Bank abrufen (bank_abruf)
EINORDNEN = 20  # 2. Umsätze einordnen (kassenbuch_einordnen)
SOLL = 30  # 3. Sollstellung prüfen (soll_monat, idempotent, darum täglich)
ABGLEICH = 40  # 4. Zahlungen abgleichen (Schritt 6, meldet sich selbst an)
MAHNWESEN = 50  # 5. Mahnfälle fortschreiben, montags (Schritt 7, meldet sich selbst an)
FRISTEN = 60  # 6. Fristen prüfen (fristen)
VERWALTIO = 70  # 7. Kennzahlen aus Verwaltio holen (verwaltio_kennzahlen; 75 id_abgleich)
PFLEGE = 78  # Arbeitsposten ohne Bezug schließen (arbeitsposten_pflege), vor dem Hof-Backlog
COCKPIT = 80  # 8. Hof-Cockpit aktualisieren: Aufgaben im Hof-Backlog (aufgaben_abgleich)
SICHERUNG = 90  # 9. Sicherung prüfen (sicherung_pruefen; die Sicherung selbst: 3:30)
BENACHRICHTIGEN = 100  # zum Schluss: Benachrichtigung (benachrichtigen)

WOCHENTAGE = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")


@dataclass(frozen=True)
class Schritt:
    name: str
    befehl: str
    reihenfolge: int
    nur_wochentag: frozenset[int] | None = None
    abhaengig_von: tuple[str, ...] = ()
    optionen: Mapping[str, Any] = field(default_factory=dict)

    def laeuft_am(self, tag: datetime.date) -> bool:
        return self.nur_wochentag is None or tag.weekday() in self.nur_wochentag

    @property
    def rhythmus(self) -> str:
        if self.nur_wochentag is None:
            return "täglich"
        return ", ".join(WOCHENTAGE[t] for t in sorted(self.nur_wochentag))


_SCHRITTE: dict[str, Schritt] = {}


def schritt_anmelden(
    name: str,
    befehl: str | None = None,
    *,
    reihenfolge: int,
    nur_wochentag: int | Iterable[int] | None = None,
    abhaengig_von: Iterable[str] = (),
    optionen: Mapping[str, Any] | None = None,
) -> Schritt:
    """Meldet einen Lauf für den Tageslauf an (siehe Modul-Doku)."""
    if nur_wochentag is None:
        tage = None
    else:
        tage = frozenset([nur_wochentag] if isinstance(nur_wochentag, int) else nur_wochentag)
        if not tage or not tage <= set(range(7)):
            raise ValueError(f"Schritt {name}: Wochentage 0 (Montag) bis 6 (Sonntag).")
    schritt = Schritt(
        name=name,
        befehl=befehl or name,
        reihenfolge=reihenfolge,
        nur_wochentag=tage,
        abhaengig_von=tuple(abhaengig_von),
        optionen=dict(optionen or {}),
    )
    _SCHRITTE[name] = schritt
    return schritt


def schritt_abmelden(name: str) -> None:
    _SCHRITTE.pop(name, None)


def schritte() -> list[Schritt]:
    """Alle angemeldeten Schritte in der Reihenfolge des Tageslaufs."""
    return sorted(_SCHRITTE.values(), key=lambda s: (s.reihenfolge, s.name))


def schritte_am(tag: datetime.date) -> list[Schritt]:
    """Die Schritte, die am ``tag`` laufen."""
    return [s for s in schritte() if s.laeuft_am(tag)]
