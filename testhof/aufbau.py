"""Aufbau und Leeren des Testhofs über alle angemeldeten Module.

Gleicher Seed ergibt exakt dieselben Daten. Dafür
- bekommt jedes Modul einen eigenen Faker (``de_DE``) und einen eigenen
  ``random.Random``, beide aus Seed und Modulname abgeleitet. Ein neues Modul ändert
  damit nicht die Daten der Module, die es schon gibt;
- gibt es eine feste Uhrzeit ``ZEITPUNKT`` statt ``now()``. Module rechnen Daten und
  Zeitpunkte von ihr aus (z. B. 24 Monate Umsätze bis zum Stichtag);
- laufen die Module in fester Reihenfolge (``register.reihenfolge``).
"""

from __future__ import annotations

import datetime
import random
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

import pghistory
from django.db import transaction
from faker import Faker

from testhof import register, sperre

STANDARD_SEED = 42

#: Feste „Jetzt“-Zeit des Testhofs. Module nutzen sie statt ``timezone.now()``.
ZEITPUNKT = datetime.datetime(2026, 11, 1, 8, 0, tzinfo=ZoneInfo("Europe/Berlin"))
STICHTAG = ZEITPUNKT.date()


class TesthofFehler(Exception):
    """Der Testhof lässt sich so nicht aufbauen (z. B. Datenbank nicht leer)."""


@dataclass
class Kontext:
    """Was ein Modul beim Aufbau bekommt."""

    seed: int
    modul: str
    faker: Faker
    zufall: random.Random
    #: Ergebnisse der Module, die schon gelaufen sind, z. B. ``ablage["kern"]``.
    ablage: dict[str, Any]
    zeitpunkt: datetime.datetime = ZEITPUNKT
    stichtag: datetime.date = STICHTAG

    @classmethod
    def fuer(cls, seed: int, modul: str, ablage: dict[str, Any]) -> Kontext:
        faker = Faker("de_DE")
        # Ein Text als Startwert ist in Python stabil (SHA-512, kein Hash-Zufall).
        faker.seed_instance(f"testhof:{seed}:{modul}:faker")
        zufall = random.Random(f"testhof:{seed}:{modul}:zufall")
        return cls(seed=seed, modul=modul, faker=faker, zufall=zufall, ablage=ablage)


@dataclass
class Testhof:
    """Ergebnis eines Aufbaus. ``hof.kern`` ist die Ablage des Moduls ``kern``."""

    __test__ = False  # keine Testklasse für pytest, trotz des Namens

    seed: int
    module: list[str]
    ablage: dict[str, Any] = field(default_factory=dict)

    def __getattr__(self, name: str) -> Any:
        ablage = self.__dict__.get("ablage", {})
        if name in ablage:
            return ablage[name]
        raise AttributeError(name)


def aufbauen(seed: int = STANDARD_SEED) -> Testhof:
    """Baut den Testhof in einer Transaktion auf. In der Produktion gesperrt."""
    sperre.pruefen()
    module = register.reihenfolge()
    hof = Testhof(seed=seed, module=[m.name for m in module])
    with transaction.atomic(), pghistory.context(testhof_seed=seed):
        for modul in module:
            kontext = Kontext.fuer(seed, modul.name, hof.ablage)
            hof.ablage[modul.name] = modul.aufbauen(kontext)
    return hof


def leeren() -> None:
    """Entfernt die Daten aller Module, in umgekehrter Reihenfolge. In der Produktion gesperrt.

    Ausnahme vom einen Schreibweg (CLAUDE.md, Regel 4.1): Die Module löschen direkt über
    das ORM. Löschen gehört nicht in die Services des Systems (dort wird storniert);
    hier betrifft es nur erfundene Daten in Entwicklung und Tests.
    """
    sperre.pruefen()
    module = register.reihenfolge()
    with transaction.atomic(), pghistory.context(testhof_leeren=True):
        for modul in reversed(module):
            modul.leeren()
