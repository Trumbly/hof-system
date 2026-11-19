"""Smoke-Prüfung der Seiten aus R2-8 (Review 2, B9 bis B13) bei 1440 und 390 px.

Dieselben Prüfungen wie ``test_smoke.py`` (kein Serverfehler, keine waagrechte Scrollleiste,
keine englischen Standardtexte) für die Seiten, die nicht unter den 15 wichtigsten stehen:
Beleg, Belegmappe, Monatsbericht, Katalog, Datenstand, Schreiben, Serienmitteilung, Stil.
Screenshots unter ``tests/e2e/artefakte/seiten-r2-8/<breite>/``.
"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.sync_api import Page, Response

from tests.e2e.server import ARTEFAKTE, Server
from tests.e2e.test_smoke import BREITEN, pruefen

pytestmark = pytest.mark.e2e

SEITEN = (
    "beleg",
    "ablage",
    "belegmappe",
    "fristen",
    "kassenbuch",
    "monatsbericht",
    "katalog",
    "datenstand",
    "schreiben_liste",
    "schreiben",
    "serie_neu",
    "stil",
)
ZIEL = ARTEFAKTE / "seiten-r2-8"


@pytest.mark.parametrize("breite", list(BREITEN))
@pytest.mark.parametrize("schluessel", SEITEN)
def test_seite_r2_8(
    seite: Page, server: Server, adressen: dict[str, Any], schluessel: str, breite: str
) -> None:
    """Review 2, C5 für die Seiten aus R2-8: ohne Serverfehler, ohne waagrechte
    Scrollleiste, ohne englische Standardtexte; Screenshot als Artefakt."""
    pfad = adressen["seiten_r2_8"].get(schluessel)
    if pfad is None:
        pytest.skip(f"Keine Seite {schluessel} im Testhof.")
    seite.set_viewport_size(BREITEN[breite])
    serverfehler: list[str] = []

    def _antwort(antwort: Response) -> None:
        if antwort.status >= 500 and antwort.url.startswith(server.basis_url):
            serverfehler.append(f"{antwort.status} bei {antwort.url}")

    seite.on("response", _antwort)
    antwort = seite.goto(pfad, wait_until="networkidle")
    ziel = ZIEL / breite / f"{SEITEN.index(schluessel) + 1:02d}-{schluessel}.png"
    ziel.parent.mkdir(parents=True, exist_ok=True)
    seite.screenshot(path=ziel, full_page=True)
    befunde = [f"Status {antwort.status}"] if antwort is not None and antwort.status >= 500 else []
    befunde += serverfehler + pruefen(seite)
    assert not befunde, f"{schluessel} ({pfad}) bei {breite} px:\n- " + "\n- ".join(befunde)
