"""Fixtures der Oberflächentests (Playwright): Server, Anmeldung, Browser.

Jedes Testmodul hier trägt die Marke ``e2e`` (``pytestmark``), die Klickmessung zusätzlich
``klickmessung``. Sie laufen nur mit ``make e2e`` bzw. ``make klickmessung``; ``make test``
überspringt sie (``-m "not e2e"`` in pyproject.toml).
Sie greifen nicht selbst auf die Datenbank zu, sondern nur über den Browser auf den Server
aus ``tests/e2e/server.py``.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, Page

from testhof import kern as testhof_kern
from tests.conftest import totp_code
from tests.e2e import server as e2e_server
from tests.e2e.server import ARTEFAKTE, Server

#: Breiten aus Review 2: Desktop und Handy.
DESKTOP = {"width": 1440, "height": 900}
HANDY = {"width": 390, "height": 844}


@pytest.fixture(scope="session")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Server]:
    laufender = e2e_server.starten(tmp_path_factory.mktemp("e2e-medien"))
    try:
        yield laufender
    finally:
        e2e_server.beenden(laufender)


@pytest.fixture(scope="session")
def base_url(server: Server) -> str:
    """Überschreibt ``base_url`` von pytest-base-url: ``page.goto("/…")`` geht an den Server."""
    return server.basis_url


@pytest.fixture(scope="session")
def adressen(server: Server) -> dict[str, Any]:
    return server.adressen


def _frischer_code() -> str:
    """TOTP-Code mit mindestens drei Sekunden Restlaufzeit im 30-Sekunden-Fenster."""
    if 30 - time.time() % 30 < 3:
        time.sleep(30 - time.time() % 30 + 0.2)
    return totp_code(testhof_kern.TOTP_GEHEIMNIS)


@pytest.fixture(scope="session")
def anmeldung(browser: Browser, server: Server) -> Path:
    """Meldet den Testbenutzer einmal je Lauf an (Passwort und TOTP) und merkt sich die
    Sitzung. allauth nimmt denselben Code nur einmal an, deshalb nicht je Test."""
    kontext = browser.new_context(base_url=server.basis_url)
    seite = kontext.new_page()
    seite.goto(server.adressen["login"])
    seite.fill("input[name=login]", testhof_kern.EMAIL)
    seite.fill("input[name=password]", testhof_kern.PASSWORT)
    seite.click("button[type=submit]")
    seite.fill("input[name=code]", _frischer_code())
    seite.click("button[type=submit]")
    seite.wait_for_url(server.basis_url + "/")
    pfad = ARTEFAKTE / "sitzung.json"
    kontext.storage_state(path=pfad)
    kontext.close()
    return pfad


@pytest.fixture
def browser_context_args(browser_context_args: dict[str, Any], anmeldung: Path) -> dict[str, Any]:
    """Angemeldet, Desktop-Breite. Sprache des Browsers Englisch wie im Durchgang von Max:
    So zeigen eingebaute Felder (Datei, Monat) ihre englischen Standardtexte."""
    return {
        **browser_context_args,
        "storage_state": str(anmeldung),
        "viewport": DESKTOP,
        "locale": "en-US",
        "timezone_id": "Europe/Berlin",
    }


@pytest.fixture
def seite(page: Page, server: Server) -> Iterator[Page]:
    """Die Seite von pytest-playwright; scheitert der Test, steht im Fehler, ob der Server
    noch läuft."""
    page.set_default_timeout(15_000)
    yield page
    if not server.laeuft():
        server.neu_starten()
        pytest.fail(f"Der Server ist während des Tests abgestürzt, siehe {server.log}.")
