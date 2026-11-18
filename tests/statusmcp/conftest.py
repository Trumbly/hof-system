"""Fixtures des Status-MCP: Quelle mit der Rolle ``hof_statusmcp``, Server per HTTP, Aufrufe
über den MCP-Client des SDK.

Die Tests laufen in der Transaktion von pytest-django (der Testhof ist nicht festgeschrieben).
Deshalb liest die ``RollenQuelle`` über die Verbindung des Tests, aber mit
``SET LOCAL ROLE hof_statusmcp`` in einem Sicherungspunkt: Es gelten genau die Rechte des
Lese-Benutzers, und nach jeder Abfrage ist die Rolle wieder weg. Die echte Verbindung mit
Login prüft ``test_rechte.py`` (eigene, festgeschriebene Transaktion).
"""

from __future__ import annotations

import asyncio
import datetime
import io
import json
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import httpx2
import pytest
import uvicorn
from django.core.management import call_command
from django.db import connection, transaction
from django.test import override_settings
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from psycopg.rows import dict_row

from apps.abgleich import services as abgleich
from apps.statusmcp import rechte
from apps.statusmcp.quelle import CursorLeser, Leser
from apps.statusmcp.server import asgi_app
from testhof import abgleich as testhof_abgleich
from testhof.aufbau import STICHTAG, Testhof

TOKEN = "t" * 20 + "-nur-fuer-tests-0123456789"


class RollenQuelle:
    """Liest über die Verbindung des Tests mit den Rechten der Rolle ``hof_statusmcp``."""

    def __init__(self) -> None:
        connection.ensure_connection()
        self._roh = connection.connection

    @contextmanager
    def lesen(self) -> Iterator[Leser]:
        with self._roh.cursor(row_factory=dict_row) as cursor:
            cursor.execute("SAVEPOINT statusmcp_lesen")
            try:
                cursor.execute(f"SET LOCAL ROLE {rechte.ROLLE}")
                cursor.execute("SET LOCAL TimeZone = 'Europe/Berlin'")
                yield CursorLeser(cursor)
            finally:
                # Hebt auch SET LOCAL ROLE auf.
                cursor.execute("ROLLBACK TO SAVEPOINT statusmcp_lesen")
                cursor.execute("RELEASE SAVEPOINT statusmcp_lesen")


@pytest.fixture
def quelle(db: None) -> RollenQuelle:
    return RollenQuelle()


def _freier_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


@dataclass
class LaufenderServer:
    url: str
    basis: str


@pytest.fixture
def server(quelle: RollenQuelle) -> Iterator[LaufenderServer]:
    """Der Status-MCP als echter HTTP-Dienst (uvicorn) auf einem freien Port."""
    port = _freier_port()
    app = asgi_app(quelle, TOKEN, heute=lambda: STICHTAG)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    dienst = uvicorn.Server(config)
    faden = threading.Thread(target=dienst.run, daemon=True)
    faden.start()
    ende = time.monotonic() + 10
    while not dienst.started:
        if time.monotonic() > ende or not faden.is_alive():
            raise RuntimeError("Status-MCP startet nicht")
        time.sleep(0.02)
    basis = f"http://127.0.0.1:{port}"
    yield LaufenderServer(url=f"{basis}/mcp", basis=basis)
    dienst.should_exit = True
    faden.join(10)


async def _aufrufe(url: str, token: str, aufrufe: list[tuple[str, dict[str, Any]]]) -> list[Any]:
    async with (
        httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as http,
        Client(streamable_http_client(url, http_client=http)) as client,
    ):
        return [await client.call_tool(name, argumente) for name, argumente in aufrufe]


def aufrufen(
    server: LaufenderServer, aufrufe: list[tuple[str, dict[str, Any]]], token: str = TOKEN
) -> list[Any]:
    """Ruft Werkzeuge über den MCP-Client des SDK per Streamable HTTP auf."""
    return asyncio.run(_aufrufe(server.url, token, aufrufe))


def antworttext(ergebnis: Any) -> str:
    """Alles, was beim Client ankommt: Textinhalt und strukturierte Antwort."""
    teile = [getattr(inhalt, "text", "") for inhalt in ergebnis.content]
    teile.append(json.dumps(ergebnis.structured_content, ensure_ascii=False, default=str))
    return "\n".join(teile)


ALLE_WERKZEUGE: list[tuple[str, dict[str, Any]]] = [
    ("ueberblick", {}),
    ("rueckstaende", {}),
    ("datenstand", {}),
    ("leerstand", {}),
    ("kassenbuch_monat", {"monat": "2026-10"}),
    ("kassenbuch_monat", {"monat": "2025-12"}),
    ("fristen", {"von": "2024-11-01", "bis": "2027-10-31"}),
]


# --- Testhof mit Läufen ------------------------------------------------------------------


@pytest.fixture(scope="module")
def _betrieb_modul(_testhof_sitzung: Testhof, django_db_blocker: Any) -> Iterator[None]:
    """Der Testhof nach einem Betriebstag: Einordnen, Abgleich, Fristen, Sicherungsprüfung
    und Kennzahlen des Status-MCP (Stichtag 01.11.2026). Einmal je Modul in einem
    Sicherungspunkt, danach zurückgerollt."""
    with (
        django_db_blocker.unblock(),
        pytest.MonkeyPatch.context() as patch,
        override_settings(ABGLEICH_BEGINN=testhof_abgleich.BEGINN),
    ):
        patch.setattr(abgleich, "heute", lambda: STICHTAG)
        punkt = transaction.savepoint()
        try:
            stichtag = f"--stichtag={STICHTAG.isoformat()}"
            for befehl, argumente in (
                ("kassenbuch_einordnen", []),
                ("abgleich", []),
                ("fristen", [stichtag]),
                ("sicherung_pruefen", []),
                ("statusmcp_aktualisieren", [stichtag]),
            ):
                call_command(befehl, *argumente, stdout=io.StringIO())
            yield
        finally:
            transaction.savepoint_rollback(punkt)


@pytest.fixture
def betrieb(_betrieb_modul: None, testhof: Testhof) -> Testhof:
    return testhof


def heute() -> datetime.date:
    return STICHTAG
