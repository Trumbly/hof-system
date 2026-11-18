"""Lese-Benutzer des Status-MCP (Schritt 11, Aufgabe 5): Er liest nur die Sichten.

Zwei Wege:
- ``SET LOCAL ROLE hof_statusmcp`` in der Transaktion des Tests: Für jede Tabelle, Sicht,
  Sequenz und materialisierte Sicht in ``public`` außer ``sm_*`` scheitert ``SELECT`` mit
  einem Rechtefehler; jede Sicht ``sm_*`` lässt sich lesen.
- Ein echter Login-Benutzer, angelegt mit ``statusmcp_benutzer``, über eine eigene
  Verbindung mit Passwort (festgeschriebene Transaktion, danach wieder entfernt).
"""

from __future__ import annotations

import io
import secrets
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from django.core.management import CommandError, call_command
from django.db import connection
from psycopg import errors, sql

from apps.statusmcp import rechte, werkzeuge
from apps.statusmcp.quelle import DsnQuelle
from testhof.aufbau import STICHTAG

RELATIONSARTEN = ("r", "p", "v", "m", "S", "f")


def _relationen() -> list[tuple[str, str]]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT c.relname, c.relkind FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = ANY(%s) ORDER BY c.relname",
            [list(RELATIONSARTEN)],
        )
        return [(name, str(art)) for name, art in cursor.fetchall()]


def _lesbar_als_rolle(relationen: list[str]) -> dict[str, bool]:
    """Für jede Relation: lässt sie sich mit der Rolle ``hof_statusmcp`` lesen?"""
    connection.ensure_connection()
    roh = connection.connection
    ergebnis = {}
    with roh.cursor() as cursor:
        for name in relationen:
            cursor.execute("SAVEPOINT probe")
            cursor.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(rechte.ROLLE)))
            try:
                cursor.execute(sql.SQL("SELECT 1 FROM {} LIMIT 1").format(sql.Identifier(name)))
                ergebnis[name] = True
            except errors.InsufficientPrivilege:
                ergebnis[name] = False
            finally:
                cursor.execute("ROLLBACK TO SAVEPOINT probe")
                cursor.execute("RELEASE SAVEPOINT probe")
    return ergebnis


@pytest.mark.django_db
def test_rolle_ohne_login_und_sonderrechte() -> None:
    """Schritt 11, Aufgabe 3: Die Rolle hof_statusmcp gibt es, ohne Login und Sonderrechte."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls "
            "FROM pg_roles WHERE rolname = %s",
            [rechte.ROLLE],
        )
        assert cursor.fetchone() == (False, False, False, False, False)


@pytest.mark.django_db
def test_lese_benutzer_liest_nur_die_sichten() -> None:
    """Abnahme Schritt 11: Der Datenbank-Benutzer des MCP kann keine Tabelle außer den
    Sichten lesen. Jede Tabelle (und jede andere Relation) in public scheitert mit einem
    Rechtefehler, jede Sicht sm_* lässt sich lesen."""
    relationen = _relationen()
    tabellen = [name for name, art in relationen if art in ("r", "p")]
    assert len(tabellen) > 50, "Es sollten alle Tabellen des Hof-Systems da sein."
    assert "kern_arbeitsposten" in tabellen and "statusmcp_kennzahl" in tabellen

    lesbar = _lesbar_als_rolle([name for name, _ in relationen])

    sichten = {name for name, art in relationen if art == "v" and name.startswith("sm_")}
    assert len(sichten) == 9
    assert {name for name, ok in lesbar.items() if ok} == sichten


@pytest.mark.django_db
def test_neue_tabellen_bleiben_gesperrt() -> None:
    """Schritt 11, Aufgabe 3 (default privileges): Eine Tabelle, die eine spätere Migration
    anlegt, kann der Lese-Benutzer ebenfalls nicht lesen."""
    with connection.cursor() as cursor:
        cursor.execute("CREATE TABLE statusmcp_probe_neu (id integer)")
    assert _lesbar_als_rolle(["statusmcp_probe_neu"]) == {"statusmcp_probe_neu": False}


@pytest.mark.django_db
def test_rechte_sql_ist_idempotent() -> None:
    """Schritt 11, Aufgabe 3: Die Rechte lassen sich beliebig oft neu setzen."""
    with connection.cursor() as cursor:
        cursor.execute(rechte.RECHTE_SQL)
        cursor.execute(rechte.RECHTE_SQL)
    assert _lesbar_als_rolle(["sm_laeufe", "kern_lauf"]) == {
        "sm_laeufe": True,
        "kern_lauf": False,
    }


# --- echter Login-Benutzer --------------------------------------------------------------


@pytest.fixture
def lese_benutzer(tmp_path: Path) -> Iterator[tuple[str, str]]:
    """Legt einen Login-Benutzer mit ``statusmcp_benutzer`` an und entfernt ihn danach."""
    name = f"hof_statusmcp_test_{secrets.token_hex(4)}"
    passwort = secrets.token_urlsafe(32)
    datei = tmp_path / "passwort"
    datei.write_text(passwort + "\n")
    ausgabe = io.StringIO()
    call_command(
        "statusmcp_benutzer", "--name", name, "--passwort-datei", str(datei), stdout=ausgabe
    )
    assert f"Lese-Benutzer {name} angelegt" in ausgabe.getvalue()
    assert passwort not in ausgabe.getvalue()
    try:
        yield name, passwort
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(name)).as_string()
            )


def _dsn(name: str, passwort: str) -> str:
    einstellungen = connection.settings_dict
    return psycopg.conninfo.make_conninfo(
        host=einstellungen["HOST"] or "localhost",
        port=str(einstellungen["PORT"] or 5432),
        dbname=einstellungen["NAME"],
        user=name,
        password=passwort,
    )


@pytest.mark.django_db(transaction=True)
def test_login_benutzer_liest_nur_sichten(lese_benutzer: tuple[str, str]) -> None:
    """Abnahme Schritt 11: Der Login-Benutzer aus ``statusmcp_benutzer`` liest über eine
    eigene Verbindung die Sichten, aber keine Tabelle; schreiben kann er nichts. Die Quelle
    des Servers (``DsnQuelle``) arbeitet mit ihm."""
    name, passwort = lese_benutzer
    call_command("kern_selbsttest", stdout=io.StringIO())  # ein Lauf, festgeschrieben

    with psycopg.connect(_dsn(name, passwort), autocommit=True) as verbindung:
        assert verbindung.execute("SHOW default_transaction_read_only").fetchone() == ("on",)
        anzahl = verbindung.execute("SELECT count(*) FROM sm_laeufe").fetchone()
        assert anzahl is not None and anzahl[0] >= 1
        for tabelle in ("kern_lauf", "kern_arbeitsposten", "stammdaten_partei", "bank_bankumsatz"):
            with pytest.raises(errors.InsufficientPrivilege):
                verbindung.execute(sql.SQL("SELECT 1 FROM {}").format(sql.Identifier(tabelle)))
        with pytest.raises((errors.InsufficientPrivilege, errors.ReadOnlySqlTransaction)):
            verbindung.execute("CREATE TABLE sm_eigene (id integer)")

    with DsnQuelle(_dsn(name, passwort)).lesen() as leser:
        antwort = werkzeuge.ueberblick(leser, STICHTAG)
    assert "kern_selbsttest" in {lauf["name"] for lauf in antwort["laeufe"]["letzte"]}


@pytest.mark.django_db
def test_benutzer_befehl_prueft_eingaben(tmp_path: Path) -> None:
    """``statusmcp_benutzer`` lehnt kurze Passwörter, fremde Namen und Superuser ab."""
    kurz = tmp_path / "kurz"
    kurz.write_text("zu-kurz\n")
    lang = tmp_path / "lang"
    lang.write_text(secrets.token_urlsafe(32))
    faelle: list[tuple[list[str], str]] = [
        (["--passwort-datei", str(kurz)], "mindestens"),
        (["--name", "Robert'); DROP", "--passwort-datei", str(lang)], "Ungültiger"),
        (["--name", rechte.ROLLE, "--passwort-datei", str(lang)], "Ungültiger"),
        (["--name", connection.settings_dict["USER"], "--passwort-datei", str(lang)], "Superuser"),
        (["--passwort-datei", str(tmp_path / "fehlt")], "nicht lesbar"),
    ]
    for argumente, meldung in faelle:
        with pytest.raises(CommandError, match=meldung):
            call_command("statusmcp_benutzer", *argumente, stdout=io.StringIO())


@pytest.mark.django_db
def test_benutzer_befehl_zweimal_setzt_passwort_neu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein zweiter Aufruf legt nichts doppelt an, sondern setzt das Passwort neu (hier von
    der Standardeingabe); der Benutzer ist Mitglied der Rolle (in der Test-Transaktion)."""
    datei = tmp_path / "passwort"
    datei.write_text(secrets.token_urlsafe(32))
    name = f"hof_statusmcp_test_{secrets.token_hex(4)}"
    monkeypatch.setattr("sys.stdin", io.StringIO(secrets.token_urlsafe(32) + "\n"))
    ausgaben: list[Any] = []
    for quelle in (str(datei), "-"):
        ausgabe = io.StringIO()
        call_command(
            "statusmcp_benutzer", "--name", name, "--passwort-datei", quelle, stdout=ausgabe
        )
        ausgaben.append(ausgabe.getvalue())
    assert "angelegt" in ausgaben[0] and "aktualisiert" in ausgaben[1]
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_has_role(%s, %s, 'USAGE'), rolcanlogin, rolsuper FROM pg_roles "
            "WHERE rolname = %s",
            [name, rechte.ROLLE, name],
        )
        assert cursor.fetchone() == (True, True, False)
