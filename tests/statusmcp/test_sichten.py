"""Sichten des Status-MCP (Schritt 11, Aufgabe 2): keine Spalte aus einem Personenfeld.

Die Prüfung geht über ``information_schema.view_column_usage``: Postgres nennt dort jede
Spalte jeder Basistabelle, die eine Sicht liest (auch in WHERE, JOIN und GROUP BY). Jede
davon wird über ``db_table`` und ``column`` dem Modellfeld zugeordnet und gegen das Register
``kern.personenfelder`` geprüft.
"""

from __future__ import annotations

import pytest
from django.db import connection, models

from apps.kern import personenfelder
from apps.statusmcp import rechte

pytestmark = pytest.mark.django_db

SICHTEN = {
    "sm_arbeitsposten",
    "sm_laeufe",
    "sm_fristen",
    "sm_kassenbuch_monat",
    "sm_rueckstaende",
    "sm_rueckstaende_einheiten",
    "sm_datenstand",
    "sm_datenstand_kennungen",
    "sm_leerstand",
}


def sichten() -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_name FROM information_schema.views WHERE table_schema = 'public' "
            "AND left(table_name, 3) = %s",
            [rechte.PRAEFIX],
        )
        return {zeile[0] for zeile in cursor.fetchall()}


def _felder_je_spalte() -> dict[tuple[str, str], tuple[type[models.Model], str]]:
    zuordnung = {}
    for modell in personenfelder.eigene_modelle():
        for feld in modell._meta.concrete_fields:
            zuordnung[(modell._meta.db_table, feld.column)] = (modell, feld.name)
    return zuordnung


def personenspalten_in_sichten() -> list[str]:
    """``Sicht: Tabelle.Spalte`` für jede gelesene Spalte, die ein Personenfeld ist oder
    keinem Modell unter ``apps/`` gehört."""
    zuordnung = _felder_je_spalte()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT view_name, table_name, column_name FROM information_schema.view_column_usage "
            "WHERE view_schema = 'public' AND left(view_name, 3) = %s "
            "ORDER BY view_name, table_name, column_name",
            [rechte.PRAEFIX],
        )
        zeilen = cursor.fetchall()
    assert zeilen, "information_schema.view_column_usage ist leer"
    funde = []
    for sicht, tabelle, spalte in zeilen:
        eintrag = zuordnung.get((tabelle, spalte))
        if eintrag is None:
            funde.append(f"{sicht}: {tabelle}.{spalte} (kein Modell unter apps/)")
        elif personenfelder.ist_personenfeld(*eintrag):
            funde.append(f"{sicht}: {tabelle}.{spalte} (Personenfeld)")
    return funde


def test_alle_sichten_da() -> None:
    """Schritt 11, Aufgabe 2: Die Sichten des Status-MCP gibt es nach der Migration."""
    assert sichten() == SICHTEN


def test_keine_sicht_liest_ein_personenfeld() -> None:
    """Abnahme Schritt 11: Die Sichten enthalten keine registrierten Personenfelder. Jede
    Spalte, die eine Sicht aus einer Tabelle liest, ist ein unbedenkliches Feld."""
    assert personenspalten_in_sichten() == []


def test_pruefung_schlaegt_bei_einem_personenfeld_an() -> None:
    """Gegenprobe: Eine Sicht, die den Hinweis eines Arbeitspostens liest, fällt auf."""
    with connection.cursor() as cursor:
        cursor.execute("CREATE VIEW sm_probe AS SELECT art, hinweis FROM kern_arbeitsposten")
    assert personenspalten_in_sichten() == ["sm_probe: kern_arbeitsposten.hinweis (Personenfeld)"]


def test_sichten_laufen_mit_den_rechten_des_eigentuemers() -> None:
    """Schritt 11, Aufgabe 3: Keine Sicht hat ``security_invoker``; der Leser braucht kein
    Recht an den Basistabellen."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT relname, reloptions FROM pg_class WHERE relkind = 'v' "
            "AND left(relname, 3) = %s",
            [rechte.PRAEFIX],
        )
        optionen = {name: opt for name, opt in cursor.fetchall()}
    assert set(optionen) == SICHTEN
    assert all(not opt or "security_invoker=true" not in opt for opt in optionen.values())
