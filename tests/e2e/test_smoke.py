"""Smoke-Test der Oberfläche (Review 2, C5): die 15 wichtigsten Seiten bei 1440 und 390 px.

Je Seite und Breite:
- kein Status ab 500 (auch nicht bei Nachladungen der Seite selbst),
- keine waagrechte Scrollleiste (``scrollWidth > clientWidth``); die Meldung nennt die
  Elemente, die über den Rand ragen,
- keine englischen Standardtexte: im Text der Seite („Page not found“, „Choose file“,
  Monatsnamen wie „October“) und in eingebauten Feldern des Browsers, die ihren Text selbst
  zeichnen (Datei- und Monatsfelder, siehe ``EINGEBAUTE_FELDER``),
- ein Screenshot der ganzen Seite unter ``tests/e2e/artefakte/smoke/<breite>/``.

Die Seiten stehen in ``SEITEN``; ihre Adressen kommen aus den URL-Namen des Codes
(``tests/e2e/adressen.py``).
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Iterator
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, Response

from tests.e2e.conftest import DESKTOP, HANDY
from tests.e2e.server import ARTEFAKTE, Server

pytestmark = pytest.mark.e2e

#: (Schlüssel, Titel). Der Schlüssel ist ein Eintrag in ``adressen["seiten"]``, außer der
#: Übergabe: Die öffnet ``uebergabe_url`` über den Knopf am Vertrag.
SEITEN: tuple[tuple[str, str], ...] = (
    ("cockpit", "Cockpit"),
    ("arbeitslisten", "Arbeitslisten"),
    ("einheiten", "Einheiten"),
    ("einheit", "Einheit-Detail"),
    ("vertraege", "Verträge"),
    ("vertrag", "Vertrag-Detail"),
    ("kassenbuch", "Kassenbuch"),
    ("umsatz_einordnen", "Umsatz einordnen"),
    ("klaerfall", "Klärfall"),
    ("fristen", "Fristen"),
    ("dokumente", "Belege und Dokumente"),
    ("schreiben", "Schreiben"),
    ("uebergabe", "Übergabe (Schritt Fotos)"),
    ("zaehler_ablesung", "Zähler-Ablesung"),
    ("protokoll", "Protokoll"),
)
BREITEN = {"1440": DESKTOP, "390": HANDY}

#: Knopf am Vertrag, der den Assistenten startet, und der Schritt, den der Test zeigt.
UEBERGABE_STARTEN = "button:has-text('Rückgabe am Handy'), button:has-text('Übergabe am Handy')"
UEBERGABE_SCHRITT = 3  # Fotos

ENGLISCH = re.compile(
    r"Choose files?|No files? chosen|Page not found|Not Found|Server Error|"
    r"\b(?:January|February|March|May|June|July|October|December)\b",
    re.IGNORECASE,
)
#: Eingebaute Felder, deren Text der Browser in seiner Sprache zeichnet (nicht im DOM):
#: Dateifeld „Choose file / No file chosen“, Monatsfeld „October 2026“.
EINGEBAUTE_FELDER = {
    "input[type=file]": "Dateifeld zeigt „Choose file / No file chosen“",
    "input[type=month]": "Monatsfeld zeigt englische Monatsnamen",
}

SMOKE = ARTEFAKTE / "smoke"


@pytest.fixture(scope="module", autouse=True)
def _artefakte_leeren() -> Iterator[None]:
    shutil.rmtree(SMOKE, ignore_errors=True)
    SMOKE.mkdir(parents=True)
    yield


@pytest.fixture(scope="module")
def uebergabe_url(browser: Browser, anmeldung: Any, server: Server) -> str:
    """Beginnt am Vertrag des Smoke-Tests den Assistenten und gibt den Schritt „Fotos“."""
    kontext = browser.new_context(base_url=server.basis_url, storage_state=str(anmeldung))
    seite = kontext.new_page()
    seite.goto(server.adressen["vertrag_smoke"])
    seite.click(UEBERGABE_STARTEN)
    seite.wait_for_url(re.compile(r"/schritt/1/"))
    url = re.sub(r"/schritt/1/", f"/schritt/{UEBERGABE_SCHRITT}/", seite.url)
    kontext.close()
    return url.removeprefix(server.basis_url)


def _ueberstehende_elemente(seite: Page) -> list[str]:
    """Die (äußersten) Elemente, die rechts über die Breite des Fensters ragen."""
    return seite.evaluate(
        """() => {
        const breite = document.documentElement.clientWidth;
        const treffer = [];
        for (const e of document.body.querySelectorAll('*')) {
            const r = e.getBoundingClientRect();
            if (r.width === 0 || r.right <= breite) continue;
            if (e.parentElement && e.parentElement.getBoundingClientRect().right > breite
                && e.parentElement !== document.body) continue;
            const name = e.tagName.toLowerCase() + (e.id ? '#' + e.id : '')
                + (e.className && typeof e.className === 'string'
                   ? '.' + e.className.trim().split(/\\s+/).join('.') : '');
            treffer.push(`${name} (bis ${Math.round(r.right)} px)`);
            if (treffer.length >= 5) break;
        }
        return treffer;
    }"""
    )


def pruefen(seite: Page) -> list[str]:
    """Die Befunde einer geladenen Seite (leer: in Ordnung)."""
    befunde = []
    breiten = seite.evaluate(
        "() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]"
    )
    if breiten[0] > breiten[1]:
        befunde.append(
            f"waagrechte Scrollleiste: scrollWidth {breiten[0]} > clientWidth {breiten[1]}; "
            f"ragen heraus: {', '.join(_ueberstehende_elemente(seite)) or '?'}"
        )
    text = seite.evaluate("() => document.body.innerText")
    englisch = sorted({m.group(0) for m in ENGLISCH.finditer(text)})
    if englisch:
        befunde.append(f"englischer Text auf der Seite: {', '.join(englisch)}")
    for selektor, hinweis in EINGEBAUTE_FELDER.items():
        sichtbar = [f for f in seite.locator(selektor).all() if f.is_visible()]
        if sichtbar:
            befunde.append(f"{len(sichtbar)}× {hinweis} ({selektor})")
    return befunde


@pytest.mark.parametrize("breite", list(BREITEN))
@pytest.mark.parametrize(("schluessel", "titel"), SEITEN, ids=[s for s, _ in SEITEN])
def test_seite(
    seite: Page,
    server: Server,
    adressen: dict[str, Any],
    request: pytest.FixtureRequest,
    schluessel: str,
    titel: str,
    breite: str,
) -> None:
    """Review 2, C5: Seite ohne Serverfehler, ohne waagrechte Scrollleiste, ohne englische
    Standardtexte; Screenshot als Artefakt."""
    seite.set_viewport_size(BREITEN[breite])
    pfad = (
        request.getfixturevalue("uebergabe_url")
        if schluessel == "uebergabe"
        else adressen["seiten"][schluessel]
    )
    serverfehler: list[str] = []

    def _antwort(antwort: Response) -> None:
        if antwort.status >= 500 and antwort.url.startswith(server.basis_url):
            serverfehler.append(f"{antwort.status} bei {antwort.url}")

    seite.on("response", _antwort)
    antwort = seite.goto(pfad, wait_until="networkidle")
    nummer = [s for s, _ in SEITEN].index(schluessel) + 1
    ziel = SMOKE / breite / f"{nummer:02d}-{schluessel}.png"
    ziel.parent.mkdir(parents=True, exist_ok=True)
    seite.screenshot(path=ziel, full_page=True)

    befunde = [f"Status {antwort.status}"] if antwort is not None and antwort.status >= 500 else []
    befunde += serverfehler + pruefen(seite)
    with (SMOKE / "ergebnis.jsonl").open("a", encoding="utf-8") as datei:
        zeile = {"seite": titel, "pfad": pfad, "breite": breite, "befunde": befunde}
        datei.write(json.dumps(zeile, ensure_ascii=False) + "\n")
    assert not befunde, f"{titel} ({pfad}) bei {breite} px:\n- " + "\n- ".join(befunde)
