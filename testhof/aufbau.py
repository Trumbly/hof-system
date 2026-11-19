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
from django.apps import apps
from django.db import connection, models, transaction
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


#: Apps, deren Tabellen beim Leeren bleiben:
#: - ``contenttypes``, ``auth``: Modelltypen (Arbeitsposten und Protokoll zeigen darauf),
#:   Rechte und Gruppen. Das legt ``migrate`` an, nicht der Testhof.
#: - ``account``, ``mfa``, ``sessions``: Anmeldedaten und Sitzungen der Benutzer, die bleiben
#:   (siehe ``kern.Benutzer``). Die des Testbenutzers gehen mit ihm.
BEHALTEN_APPS = frozenset({"contenttypes", "auth", "account", "mfa", "sessions"})
#: Einzelne Tabellen, die bleiben:
#: - ``kern.Benutzer``: Benutzer außerhalb des Testhofs (etwa ein eigener Entwicklungs-
#:   zugang) bleiben samt Anmeldedaten (``account``, ``mfa``) und Sitzungen; den
#:   Testbenutzer löscht ``leeren`` gezielt.
#: - ``mahnwesen.Mahnstufe``: Konfiguration aus der Datenmigration ``0002_startdaten``.
BEHALTEN_MODELLE = frozenset({"kern.Benutzer", "mahnwesen.Mahnstufe"})


def _behalten(modell: type[models.Model]) -> bool:
    meta = modell._meta
    if meta.app_label in BEHALTEN_APPS or meta.label in BEHALTEN_MODELLE:
        return True
    # Hilfstabellen von ManyToMany-Feldern gehören zum Modell, das sie anlegt.
    erzeuger = meta.auto_created
    return isinstance(erzeuger, type) and erzeuger._meta.label in BEHALTEN_MODELLE


def geleerte_modelle() -> list[type[models.Model]]:
    """Alle Tabellen, die ``leeren`` vollständig leert: jede verwaltete Tabelle aller Apps
    (auch Protokoll, Läufe, Arbeitsposten, Fristen, Schreiben, Aufgaben im Hof-Backlog),
    außer ``BEHALTEN_APPS`` und ``BEHALTEN_MODELLE``."""
    modelle = [
        m
        for m in apps.get_models(include_auto_created=True)
        if m._meta.managed and not m._meta.proxy and not _behalten(m)
    ]
    return sorted(modelle, key=lambda m: m._meta.db_table)


def _dateien_loeschen(modelle: list[type[models.Model]]) -> None:
    """Dateien der Zeilen aus dem Speicher löschen; ``TRUNCATE`` sieht sie nicht."""
    for modell in modelle:
        for feld in modell._meta.concrete_fields:
            if not isinstance(feld, models.FileField):
                continue
            namen = modell._default_manager.exclude(**{feld.attname: ""}).values_list(
                feld.attname, flat=True
            )
            for name in namen:
                if name:
                    feld.storage.delete(name)


def leeren() -> None:
    """Entfernt alle Daten des Testhofs. In der Produktion gesperrt (Review 1, Punkt 3).

    Generisch statt je Modul: Ein ``TRUNCATE`` über alle Tabellen aus ``geleerte_modelle``
    in *einer* Anweisung. Warum so:

    - **Vollständig.** Was eine App anlegt, ist weg, auch Arbeitsposten, Läufe, Fristen,
      Schreiben, Aufgaben im Hof-Backlog und das Protokoll des alten Testhofs. Eine neue
      Tabelle ist ohne weitere Arbeit dabei; ein vergessenes ``delete()`` je Modul, wie bei
      den Arbeitsposten zu Konto #1, kann es nicht mehr geben.
    - **Reihenfolge egal.** Postgres leert alle genannten Tabellen zugleich; Verweise
      zwischen ihnen (auch im Kreis, auch über ``PROTECT``) stören nicht. Eine umgekehrte
      Abhängigkeitsreihenfolge ist damit überflüssig.
    - **Ohne ``CASCADE``.** Zeigt eine behaltene Tabelle auf eine geleerte, bricht Postgres
      ab, statt sie still mitzuleeren. So verschwindet Konfiguration nie aus Versehen.
    - **Keine neuen IDs.** Die Sequenzen laufen weiter (kein ``RESTART IDENTITY``): Ein alter
      Link oder eine alte Nummer zeigt danach ins Leere, nie auf ein neues, anderes Objekt.
    - **Protokoll.** ``TRUNCATE`` löst keine Zeilen-Trigger aus; das Protokoll (pghistory)
      wird mitgeleert, statt Tausende Löschungen erfundener Daten aufzuschreiben.

    Ausnahme vom einen Schreibweg (CLAUDE.md, Regel 4.1): Löschen gehört nicht in die
    Services des Systems (dort wird storniert); hier betrifft es nur erfundene Daten in
    Entwicklung und Tests.
    """
    sperre.pruefen()
    from apps.cockpit.notion import FakeNotion
    from apps.kern.models import Benutzer
    from testhof import kern

    modelle = geleerte_modelle()
    with transaction.atomic():
        for modul in reversed(register.reihenfolge()):
            if modul.leeren is not None:
                modul.leeren()
        _dateien_loeschen(modelle)
        tabellen = ", ".join(connection.ops.quote_name(m._meta.db_table) for m in modelle)
        with connection.cursor() as cursor:
            # Offene Prüfungen aufgeschobener Fremdschlüssel (aus derselben Transaktion)
            # verbieten ein TRUNCATE; erst prüfen, danach wieder aufschieben wie üblich.
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            cursor.execute(f"TRUNCATE {tabellen}")
            cursor.execute("SET CONSTRAINTS ALL DEFERRED")
        Benutzer.objects.filter(email=kern.EMAIL).delete()
    # Der Fake des Hof-Backlogs merkt sich Seiten im Speicher dieses Prozesses.
    FakeNotion.zuruecksetzen()
