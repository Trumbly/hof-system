"""Datenquelle des Status-MCP: rohes SQL über psycopg, nur auf die Sichten ``sm_*``.

Entscheidung (Schritt 11): kein Django im Server-Prozess, auch kein ORM-Alias mit
unmanaged Modellen. Gründe:

- Der Prozess bekommt nur ``HOF_STATUSMCP_DATABASE_URL`` (Lese-Benutzer) und das Token. Er
  braucht weder ``DJANGO_SECRET_KEY`` noch die ``DATABASE_URL`` der App; was er nicht hat,
  kann er nicht verraten.
- Ohne Django gibt es keinen zweiten, versehentlich nutzbaren Weg auf die echten Tabellen
  (kein ``default``-Alias, keine Modelle der Fach-Apps im Prozess).
- Die Abfragen sind wenige, kurze ``SELECT`` auf Sichten; ein ORM bringt dafür nichts.

Die Sperre selbst liegt in der Datenbank: Der Lese-Benutzer hat nur die Rolle
``hof_statusmcp`` mit ``SELECT`` auf die Sichten. Jede Verbindung ist zusätzlich nur lesend
und bricht Anfragen nach 10 Sekunden ab.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from typing import Any, Protocol

import psycopg
from psycopg.rows import dict_row

ZEITZONE = "Europe/Berlin"


class Leser(Protocol):
    def abfragen(self, abfrage: str, parameter: Sequence[Any] = ()) -> list[dict[str, Any]]: ...


class Quelle(Protocol):
    def lesen(self) -> AbstractContextManager[Leser]: ...


class CursorLeser:
    """Liest über einen psycopg-Cursor. Nur ``SELECT``."""

    def __init__(self, cursor: psycopg.Cursor[Any]) -> None:
        self._cursor = cursor

    def abfragen(self, abfrage: str, parameter: Sequence[Any] = ()) -> list[dict[str, Any]]:
        if not abfrage.lstrip().upper().startswith("SELECT"):
            raise ValueError("Der Status-MCP liest nur.")
        self._cursor.execute(abfrage.encode(), parameter)
        return list(self._cursor.fetchall())


class DsnQuelle:
    """Eine Verbindung je Werkzeug-Aufruf mit dem Lese-Benutzer (wenige Aufrufe am Tag)."""

    def __init__(self, dsn: str, *, zeitlimit_ms: int = 10_000) -> None:
        self._dsn = dsn
        self._optionen = (
            f"-c default_transaction_read_only=on -c statement_timeout={zeitlimit_ms} "
            f"-c TimeZone={ZEITZONE}"
        )

    @contextmanager
    def lesen(self) -> Iterator[Leser]:
        with psycopg.connect(
            self._dsn,
            connect_timeout=5,
            options=self._optionen,
            application_name="hof-statusmcp",
            autocommit=False,
        ) as verbindung:
            verbindung.read_only = True
            with verbindung.cursor(row_factory=dict_row) as cursor:
                yield CursorLeser(cursor)
            verbindung.rollback()
