"""Screenshots zum Abschluss von Review 3 (``prompts/14-review-3.md``, „Offen, bitte selbst
prüfen“: Handy-Ansicht).

Läuft nur mit ``HOF_SCREENSHOTS=1`` (sonst übersprungen), z. B.::

    HOF_SCREENSHOTS=1 uv run pytest -m e2e tests/e2e/test_screenshots_review3.py

Je Seite und Breite (1440 und 390 px) ein Screenshot der ganzen Seite unter
``HOF_SCREENSHOTS_ZIEL`` (Standard ``tests/e2e/artefakte/screens-review3/``), darin
``<breite>/<nr>-<seite>.png``. Bei 390 px stehen in ``befunde.json`` die Befunde der
Prüfungen aus ``test_smoke.pruefen`` (waagrechter Überlauf, Überlappung in Formularen,
Ziele unter 44 px, englische Texte). Dazu ``/stil/`` und ein Schreiben bei 320 px.

Die Seiten: Cockpit, Arbeitslisten, Vertrag V-0005 (laufend), Übergabe Schritt 1 bis 7
(Vertrag im Entwurf für die freie Garage NW-G-0012, wie die Klickmessung ihn anlegt),
Zähler-Ablesung, Beleg erfassen.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, Page

from tests.e2e.klickmessung import ablauf_uebergabe_ohne_kaution as uebergabe_ablauf
from tests.e2e.klickmessung.messung import Ergebnis, Messung
from tests.e2e.server import ARTEFAKTE, Server
from tests.e2e.test_smoke import pruefen

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("HOF_SCREENSHOTS") != "1",
        reason="Screenshots nur mit HOF_SCREENSHOTS=1",
    ),
]

ZIEL = Path(os.environ.get("HOF_SCREENSHOTS_ZIEL") or ARTEFAKTE / "screens-review3")
BREITEN = {
    "1440": {"width": 1440, "height": 900},
    "390": {"width": 390, "height": 844},
}
SCHMAL = {"width": 320, "height": 700}
#: Schritte der Übergabe (Vorab, Zähler, Fotos, Schlüssel, Kaution, Unterschrift, Abschluss).
UEBERGABE_SCHRITTE = 7

_BEFUNDE: list[dict[str, Any]] = []


def _seiten(adressen: dict[str, Any], uebergabe: str) -> list[tuple[str, str]]:
    seiten = [
        ("01-cockpit", adressen["seiten"]["cockpit"]),
        ("02-arbeitslisten", adressen["seiten"]["arbeitslisten"]),
        ("03-vertrag-laufend", adressen["seiten"]["vertrag"]),
    ]
    for nr in range(1, UEBERGABE_SCHRITTE + 1):
        seiten.append(
            (
                f"{nr + 3:02d}-uebergabe-schritt-{nr}",
                uebergabe.replace("/schritt/1/", f"/schritt/{nr}/"),
            )
        )
    seiten += [
        ("11-zaehler-ablesung", adressen["seiten"]["zaehler_ablesung"]),
        ("12-beleg-erfassen", adressen["beleg"]),
    ]
    return seiten


@pytest.fixture(scope="module")
def uebergabe_url(browser: Browser, anmeldung: Any, server: Server) -> str:
    """Legt einen Vertrag im Entwurf an und beginnt die Übergabe; gibt Schritt 1."""
    kontext = browser.new_context(base_url=server.basis_url, storage_state=str(anmeldung))
    seite = kontext.new_page()
    seite.set_default_timeout(15_000)
    m = Messung(seite, Ergebnis(ablauf="screenshots", titel="Vorbereitung"))
    vertrag = uebergabe_ablauf.vorbereiten(
        m, server.adressen["einheiten_frei"][0], server.adressen["vertrag_neu"]
    )
    seite.goto(vertrag)
    seite.click(uebergabe_ablauf.SELEKTOREN["starten"])
    seite.wait_for_url(re.compile(r"/schritt/1/"))
    url = seite.url.removeprefix(server.basis_url)
    kontext.close()
    return url


@pytest.fixture(scope="module", autouse=True)
def _befunde_schreiben() -> Iterator[None]:
    _BEFUNDE.clear()
    yield
    ZIEL.mkdir(parents=True, exist_ok=True)
    (ZIEL / "befunde.json").write_text(
        json.dumps(_BEFUNDE, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _aufnehmen(seite: Page, name: str, pfad: str, breite: str) -> None:
    antwort = seite.goto(pfad, wait_until="networkidle")
    assert antwort is not None and antwort.status < 500, f"{pfad}: Status {antwort}"
    if breite != "1440":
        # Ganz nach unten gescrollt: Die klebende Aktionsleiste (Zurück, Speichern und weiter)
        # steht dann an ihrem Platz im Fluss und liegt nicht über den letzten Feldern.
        seite.evaluate("() => window.scrollTo(0, document.documentElement.scrollHeight)")
        _BEFUNDE.append(
            {"seite": name, "pfad": pfad, "breite": breite, "befunde": pruefen(seite, True)}
        )
        seite.evaluate("() => window.scrollTo(0, 0)")
    ziel = ZIEL / breite / f"{name}.png"
    ziel.parent.mkdir(parents=True, exist_ok=True)
    # Fenster so hoch wie die Seite: Feste Leisten (Navigation unten) stehen im Bild unten
    # und nicht mitten auf der Seite.
    fenster = seite.viewport_size or BREITEN["1440"]
    hoehe = seite.evaluate("() => document.documentElement.scrollHeight")
    seite.set_viewport_size({"width": fenster["width"], "height": max(hoehe, fenster["height"])})
    seite.screenshot(path=ziel, full_page=True)
    seite.set_viewport_size(fenster)


@pytest.mark.parametrize("breite", list(BREITEN))
def test_screenshots(
    seite: Page, adressen: dict[str, Any], uebergabe_url: str, breite: str
) -> None:
    """Review 3, Abschluss: Screenshots der wichtigsten Seiten bei 1440 und 390 px."""
    seite.set_viewport_size(BREITEN[breite])
    for name, pfad in _seiten(adressen, uebergabe_url):
        _aufnehmen(seite, name, pfad, breite)


def test_screenshots_320(seite: Page, adressen: dict[str, Any]) -> None:
    """Review 3, Abschluss: ``/stil/`` und ein Schreiben bei 320 px (Reiter)."""
    seite.set_viewport_size(SCHMAL)
    seiten = [("stil", adressen["seiten_r2_8"]["stil"])]
    if "schreiben" in adressen["seiten_r2_8"]:
        seiten.append(("schreiben", adressen["seiten_r2_8"]["schreiben"]))
    for name, pfad in seiten:
        _aufnehmen(seite, name, pfad, "320")
