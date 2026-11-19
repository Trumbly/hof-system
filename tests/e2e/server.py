"""Eigener Server für die Oberflächentests: eigene Datenbank, frischer Testhof, ``runserver``.

Warum nicht ``live_server`` von pytest-django: Der läuft als Thread im Testprozess und sieht
nur, was festgeschrieben ist. Der Testhof der übrigen Tests liegt aber in einer offenen
Transaktion (``tests/conftest.py``), und ``transactional_db`` würde ihn nach jedem Test
löschen. Außerdem soll ein Absturz des Servers (Review 2, A1) als Absturz sichtbar werden und
nicht den Testprozess mitreißen. Deshalb baut dieses Modul je Testlauf

1. eine eigene Datenbank (``HOF_E2E_DATABASE_URL``, Standard ``hof_e2e``) auf, migriert sie,
2. baut den Testhof aus Seed 42 neu auf und lässt den Tageslauf zum Stichtag 02.11.2026
   laufen (Arbeitslisten, Klärfälle und Mahnfälle wie im Durchgang von Max),
3. liest die Adressen der Seiten (``tests/e2e/adressen.py``) und
4. startet ``manage.py runserver --insecure`` mit ``hof.settings.e2e`` (``DEBUG = False``)
   auf einem freien Port (oder ``HOF_E2E_PORT``).

Die Datenbanken ``hof`` und ``test_hof`` bleiben unberührt.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import psycopg

WURZEL = Path(__file__).resolve().parents[2]
ARTEFAKTE = Path(__file__).resolve().parent / "artefakte"

STANDARD_DATENBANK = "postgres://hof:hof@localhost:5432/hof_e2e"
#: Stichtag des Tageslaufs nach dem Aufbau (ein Montag: mit Mahnlauf).
STICHTAG = "2026-11-02"


@dataclass
class Server:
    basis_url: str
    adressen: dict[str, Any]
    prozess: subprocess.Popen[bytes]
    log: Path
    umgebung: dict[str, str] = field(repr=False)

    def url(self, pfad: str) -> str:
        return self.basis_url + pfad

    def laeuft(self) -> bool:
        return self.prozess.poll() is None

    def neu_starten(self) -> None:
        """Startet den Server nach einem Absturz neu (gleiche Datenbank)."""
        if self.laeuft():
            return
        port = int(urlsplit(self.basis_url).port or 0)
        self.prozess = _runserver(self.umgebung, port, self.log)
        _warten(self)


def _datenbank_url() -> str:
    return os.environ.get("HOF_E2E_DATABASE_URL", STANDARD_DATENBANK)


def _datenbank_anlegen(url: str) -> None:
    """Legt die Datenbank an, wenn es sie noch nicht gibt (über die Datenbank „postgres“)."""
    teile = urlsplit(url)
    name = teile.path.lstrip("/")
    if name in {"hof", "test_hof"}:
        raise RuntimeError(f"Die Oberflächentests verwenden nie die Datenbank {name!r}.")
    verwaltung = urlunsplit(teile._replace(path="/postgres"))
    with psycopg.connect(verwaltung, autocommit=True) as verbindung:
        vorhanden = verbindung.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (name,)
        ).fetchone()
        if not vorhanden:
            verbindung.execute(f'CREATE DATABASE "{name}"')


def _umgebung(datenbank: str, medien: Path) -> dict[str, str]:
    umgebung = dict(os.environ)
    umgebung.update(
        DJANGO_SETTINGS_MODULE="hof.settings.e2e",
        DATABASE_URL=datenbank,
        DJANGO_MEDIA_ROOT=str(medien),
        # Nichts geht hinaus: Mails nur auf die Konsole (im Server-Log).
        EMAIL_URL="consolemail://",
        PYTHONUNBUFFERED="1",
    )
    return umgebung


def _manage(umgebung: dict[str, str], *argumente: str) -> str:
    ergebnis = subprocess.run(  # noqa: S603 – feste Befehle, keine Eingaben von außen
        [sys.executable, "manage.py", *argumente],
        cwd=WURZEL,
        env=umgebung,
        capture_output=True,
        text=True,
        check=False,
    )
    if ergebnis.returncode != 0:
        befehl = " ".join(argumente)
        raise RuntimeError(
            f"manage.py {befehl} ist gescheitert:\n{ergebnis.stdout}\n{ergebnis.stderr}"
        )
    return ergebnis.stdout


def _freier_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _runserver(umgebung: dict[str, str], port: int, log: Path) -> subprocess.Popen[bytes]:
    with log.open("ab") as ausgabe:
        return subprocess.Popen(  # noqa: S603
            [
                sys.executable,
                "manage.py",
                "runserver",
                f"127.0.0.1:{port}",
                "--noreload",
                "--insecure",
            ],
            cwd=WURZEL,
            env=umgebung,
            stdout=ausgabe,
            stderr=subprocess.STDOUT,
        )


def _warten(server: Server, sekunden: float = 60) -> None:
    ende = time.monotonic() + sekunden
    while time.monotonic() < ende:
        if not server.laeuft():
            raise RuntimeError(f"Der Server ist beim Start beendet worden, siehe {server.log}.")
        try:
            with urllib.request.urlopen(server.url(server.adressen["gesund"]), timeout=2):  # noqa: S310
                return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(0.3)
    raise RuntimeError(f"Der Server antwortet nicht, siehe {server.log}.")


def adressen_lesen(umgebung: dict[str, str]) -> dict[str, Any]:
    ausgabe = _manage(
        umgebung, "shell", "-c", "from tests.e2e.adressen import ausgeben; ausgeben()"
    )
    for zeile in ausgabe.splitlines():
        if zeile.startswith("ADRESSEN "):
            return json.loads(zeile.removeprefix("ADRESSEN "))  # type: ignore[no-any-return]
    raise RuntimeError(f"Keine Adressen gelesen:\n{ausgabe}")


def starten(medien: Path) -> Server:
    """Baut Datenbank und Testhof auf und startet den Server (siehe Moduldoku)."""
    ARTEFAKTE.mkdir(exist_ok=True)
    datenbank = _datenbank_url()
    _datenbank_anlegen(datenbank)
    umgebung = _umgebung(datenbank, medien)
    _manage(umgebung, "migrate", "--no-input")
    _manage(umgebung, "testhof", "--seed", "42", "--leeren")
    _manage(umgebung, "tageslauf", "--stichtag", STICHTAG)
    adressen = adressen_lesen(umgebung)

    port = int(os.environ.get("HOF_E2E_PORT") or _freier_port())
    log = ARTEFAKTE / "server.log"
    log.write_bytes(b"")
    server = Server(
        basis_url=f"http://127.0.0.1:{port}",
        adressen=adressen,
        prozess=_runserver(umgebung, port, log),
        log=log,
        umgebung=umgebung,
    )
    _warten(server)
    return server


def beenden(server: Server) -> None:
    if server.laeuft():
        server.prozess.terminate()
        try:
            server.prozess.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.prozess.kill()
