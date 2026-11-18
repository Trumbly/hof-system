"""Server des Status-MCP (Schritt 11, Aufgabe 1): Streamable HTTP, Token, Werkzeuge, Eingaben."""

from __future__ import annotations

import asyncio
import datetime
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx2
import pytest
from mcp import Client

from apps.statusmcp import __main__ as start
from apps.statusmcp import werkzeuge
from apps.statusmcp.server import asgi_app, mcp_server
from testhof.aufbau import STICHTAG
from tests.statusmcp.conftest import TOKEN, LaufenderServer, RollenQuelle, antworttext, aufrufen

pytestmark = pytest.mark.django_db

WERKZEUGE = {"ueberblick", "rueckstaende", "datenstand", "leerstand", "kassenbuch_monat", "fristen"}
INITIALISIEREN = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
KOPF = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _post(server: LaufenderServer, **kopf: str) -> httpx2.Response:
    return httpx2.post(server.url, json=INITIALISIEREN, headers={**KOPF, **kopf}, timeout=10)


def test_ohne_token_kein_zugang(server: LaufenderServer) -> None:
    """Schritt 11: Anmeldung per Bearer-Token. Ohne, mit falschem oder halbem Token kommt
    nur 401 zurück, auch über den MCP-Client des SDK."""
    assert _post(server).status_code == 401
    assert _post(server, Authorization=f"Bearer {TOKEN}x").status_code == 401
    assert _post(server, Authorization=f"Bearer {TOKEN[:-1]}").status_code == 401
    assert _post(server, Authorization=TOKEN).status_code == 401
    antwort = _post(server, Authorization=f"Bearer {TOKEN}")
    assert antwort.status_code == 200, antwort.text
    assert antwort.headers["content-type"].startswith("application/json")

    with pytest.raises(BaseException):  # noqa: B017 – Fehlerart hängt am SDK
        aufrufen(server, [("ueberblick", {})], token="falsch")


def test_gesund_ohne_token(server: LaufenderServer) -> None:
    """Der Healthcheck ist ohne Token erreichbar und sagt nur „ok“."""
    antwort = httpx2.get(f"{server.basis}/gesund", timeout=10)
    assert (antwort.status_code, antwort.text) == (200, "ok")


def test_fremder_host_abgewiesen(server: LaufenderServer) -> None:
    """Schutz gegen DNS-Rebinding: Ein Host-Header außerhalb der Liste kommt nicht durch,
    auch mit Token."""
    antwort = _post(server, Authorization=f"Bearer {TOKEN}", Host="angreifer.invalid")
    assert antwort.status_code in (400, 403, 421)


def test_werkzeuge_alle_nur_lesend(quelle: RollenQuelle) -> None:
    """Schritt 11, Aufgabe 3: Die sechs Werkzeuge, alle als nur lesend markiert (in-process
    über den Client des SDK)."""

    async def liste() -> Any:
        async with Client(mcp_server(quelle, heute=lambda: STICHTAG)) as client:
            return await client.list_tools()

    werkzeugliste = asyncio.run(liste()).tools
    assert {w.name for w in werkzeugliste} == WERKZEUGE
    for werkzeug in werkzeugliste:
        assert werkzeug.annotations is not None and werkzeug.annotations.read_only_hint
        assert werkzeug.annotations.destructive_hint is False


@pytest.mark.parametrize(
    ("name", "argumente", "meldung"),
    [
        ("kassenbuch_monat", {"monat": "2026-13"}, "Monat 01 bis 12"),
        ("kassenbuch_monat", {"monat": "Oktober"}, "JJJJ-MM"),
        ("kassenbuch_monat", {"monat": "1999-01"}, "Jahr 2000 bis 2100"),
        ("fristen", {"von": "2026-12-01", "bis": "2026-11-01"}, "bis liegt vor von"),
        ("fristen", {"von": "gestern"}, "JJJJ-MM-TT"),
        ("fristen", {"von": "2020-01-01", "bis": "2026-01-01"}, "höchstens"),
    ],
)
def test_eingaben_werden_geprueft(
    server: LaufenderServer, name: str, argumente: dict[str, Any], meldung: str
) -> None:
    """Schritt 11, Aufgabe 3: Eingaben validieren. Eine falsche Eingabe gibt eine Meldung
    an Claude (is_error), nie einen Absturz mit Daten."""
    (ergebnis,) = aufrufen(server, [(name, argumente)])
    assert ergebnis.is_error
    assert meldung in antworttext(ergebnis)


def test_unbekannte_argumente_abgewiesen(server: LaufenderServer) -> None:
    """Argumente, die ein Werkzeug nicht kennt, oder ein falscher Typ führen zu einer Meldung."""
    (ergebnis,) = aufrufen(server, [("kassenbuch_monat", {"monat": 202610})])
    assert ergebnis.is_error


def test_ohne_kennzahlen_ein_hinweis(server: LaufenderServer) -> None:
    """Vor dem ersten Lauf ``statusmcp_aktualisieren`` sagen die Werkzeuge das, statt
    leere Zahlen als Stand auszugeben."""
    ergebnisse = aufrufen(server, [("rueckstaende", {}), ("datenstand", {}), ("leerstand", {})])
    for ergebnis in ergebnisse:
        assert not ergebnis.is_error
        assert ergebnis.structured_content["hinweis"] == werkzeuge.KEINE_KENNZAHLEN
        assert ergebnis.structured_content["stand"] is None


def test_fristen_standardzeitraum(server: LaufenderServer) -> None:
    """``fristen`` ohne Angaben: heute bis heute + 90 Tage."""
    (ergebnis,) = aufrufen(server, [("fristen", {})])
    daten = ergebnis.structured_content
    assert daten["von"] == STICHTAG.isoformat()
    assert daten["bis"] == (STICHTAG + datetime.timedelta(days=90)).isoformat()
    assert daten["anzahl"] == 0


def test_kurzes_token_verweigert(quelle: RollenQuelle) -> None:
    """Ein Token unter 32 Zeichen ist kein Token."""
    with pytest.raises(ValueError, match="32 Zeichen"):
        asgi_app(quelle, "zu-kurz")


def test_ohne_einrichtung_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ohne Datenbank-URL oder Token startet der Dienst, antwortet aber überall mit 503."""
    from starlette.testclient import TestClient

    monkeypatch.delenv("HOF_STATUSMCP_DATABASE_URL", raising=False)
    monkeypatch.setenv("HOF_STATUSMCP_TOKEN", TOKEN)
    with TestClient(start.app_aus_umgebung()) as client:
        assert client.get("/gesund").status_code == 503
        assert client.post("/mcp", json=INITIALISIEREN).status_code == 503

    monkeypatch.setenv("HOF_STATUSMCP_DATABASE_URL", "postgres://leser@db:5432/hof")
    monkeypatch.setenv("HOF_STATUSMCP_TOKEN", "kurz")
    with TestClient(start.app_aus_umgebung()) as client:
        assert client.get("/gesund").text == "nicht eingerichtet: Token"


def test_quelle_liest_nur() -> None:
    """Die Quelle nimmt nur SELECT an (zusätzlich zur Rolle in der Datenbank)."""
    from apps.statusmcp.quelle import CursorLeser

    with pytest.raises(ValueError, match="liest nur"):
        CursorLeser(None).abfragen("DELETE FROM sm_laeufe")  # type: ignore[arg-type]


def test_server_prozess_ohne_django() -> None:
    """Entscheidung Schritt 11: Der Server-Prozess lädt kein Django (kein ORM, keine
    Einstellungen der App); er kennt nur die Sichten über den Lese-Benutzer."""
    wurzel = Path(__file__).resolve().parents[2]
    pruefung = (
        "import sys, apps.statusmcp.__main__, apps.statusmcp.server; "
        "print(sorted(m for m in sys.modules if m == 'django' or m.startswith('django.')))"
    )
    umgebung = {k: v for k, v in os.environ.items() if not k.startswith(("DJANGO", "DATABASE"))}
    ergebnis = subprocess.run(  # noqa: S603 – fester Befehl
        [sys.executable, "-c", pruefung],
        cwd=wurzel,
        env=umgebung,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    assert ergebnis.stdout.strip() == "[]"
