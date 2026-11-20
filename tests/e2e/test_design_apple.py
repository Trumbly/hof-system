"""Experiment „Apple-Design“ (prompts/15): Startseite und Arbeitslisten im Apple-Design bei
1440 und 390 px, hell und dunkel, durch die Prüfung des Smoke-Tests (``pruefen``: keine
waagrechte Scrollleiste, keine englischen Standardtexte, Ziele am Handy mindestens 44 px,
keine überlappenden Elemente).

Der E2E-Server läuft wie der Server mit ``DEBUG = False`` und der bisherigen Gestaltung; dort
wirkt ``?design=apple`` absichtlich nicht. Dieser Fall startet deshalb einen zweiten
``runserver`` auf derselben Datenbank mit ``HOF_DESIGN=apple``. Die Anmeldung gilt auch dort
(Cookies hängen nicht am Port).

Zusätzlich: Im Rahmen und auf der Startseite sind alle Klickflächen mindestens 44 px hoch,
auch am Desktop.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from playwright.sync_api import Browser

from tests.e2e import server as e2e_server
from tests.e2e.conftest import DESKTOP, HANDY
from tests.e2e.server import ARTEFAKTE, Server
from tests.e2e.test_smoke import ZIEL_PX, pruefen

pytestmark = pytest.mark.e2e

SEITEN = ("cockpit", "arbeitslisten")
BREITEN = {"1440": DESKTOP, "390": HANDY}
SCHEMAS = ("light", "dark")
ORDNER = ARTEFAKTE / "design-apple"

#: Klickflächen in Rahmen und Startseite, die im Apple-Design 44 px hoch sein müssen.
ZIELE_RAHMEN = (
    ".leiste .nav-neu a, .leiste .suche-eingabe, .leiste-fuss a, .leiste-fuss button, "
    ".kopf-handy .marke, .kopf-handy .suche-eingabe, .unten a, .unten button, "
    ".cockpit-seite .knopf, .cockpit-seite a.liste-zeile, .cockpit-seite a.karte-link, "
    ".cockpit-seite a.kennzahl, .cockpit-raster a.kachel"
)


@pytest.fixture(scope="module")
def apple_server(server: Server) -> Iterator[str]:
    """Zweiter Server auf der Datenbank des ersten, mit HOF_DESIGN=apple."""
    umgebung = {**server.umgebung, "HOF_DESIGN": "apple"}
    port = e2e_server._freier_port()
    log = ARTEFAKTE / "server-apple.log"
    log.write_bytes(b"")
    zweiter = Server(
        basis_url=f"http://127.0.0.1:{port}",
        adressen=server.adressen,
        prozess=e2e_server._runserver(umgebung, port, log),
        log=log,
        umgebung=umgebung,
    )
    try:
        e2e_server._warten(zweiter)
        yield zweiter.basis_url
    finally:
        e2e_server.beenden(zweiter)


def _kleine_ziele_rahmen(seite: Any) -> list[str]:
    return seite.evaluate(
        """([selektor, ziel]) => {
        const klein = [];
        for (const e of document.querySelectorAll(selektor)) {
            const r = e.getBoundingClientRect();
            if (r.width === 0 || r.height === 0) continue;
            if (getComputedStyle(e).visibility === 'hidden') continue;
            if (r.height < ziel - 0.5) {
                const text = (e.innerText || e.getAttribute('aria-label') || e.placeholder
                    || '').trim().slice(0, 30);
                klein.push(`${e.tagName.toLowerCase()}.${e.className} „${text}“ `
                    + `${Math.round(r.height)} px`);
            }
        }
        return klein;
    }""",
        [ZIELE_RAHMEN, ZIEL_PX],
    )


@pytest.mark.parametrize("schema", SCHEMAS)
@pytest.mark.parametrize("breite", list(BREITEN))
@pytest.mark.parametrize("schluessel", SEITEN)
def test_apple_design_seite(
    browser: Browser,
    anmeldung: Any,
    apple_server: str,
    adressen: dict[str, Any],
    schluessel: str,
    breite: str,
    schema: str,
) -> None:
    """Experiment Apple-Design: Startseite und Arbeitslisten bestehen bei 390 und 1440 px
    (hell und dunkel) die Prüfung des Smoke-Tests (Überlauf, Ziele, Überlappung)."""
    kontext = browser.new_context(
        base_url=apple_server,
        storage_state=str(anmeldung),
        viewport=BREITEN[breite],
        color_scheme=schema,  # type: ignore[arg-type]
        locale="en-US",
        timezone_id="Europe/Berlin",
    )
    seite = kontext.new_page()
    seite.set_default_timeout(15_000)
    try:
        antwort = seite.goto(adressen["seiten"][schluessel], wait_until="networkidle")
        assert antwort is not None and antwort.status == 200
        assert seite.locator("html[data-design=apple]").count() == 1
        # Kein Schalter: Der Server läuft ohne DEBUG.
        assert seite.locator(".design-schalter").count() == 0

        ziel = ORDNER / breite / f"{schluessel}-{schema}.png"
        ziel.parent.mkdir(parents=True, exist_ok=True)
        seite.screenshot(path=ziel, full_page=True)

        befunde = pruefen(seite, handy=breite == "390")
        if klein := _kleine_ziele_rahmen(seite):
            befunde.append(f"Klickflächen unter {ZIEL_PX} px: {', '.join(klein[:6])}")
        assert not befunde, f"{schluessel} bei {breite} px ({schema}):\n- " + "\n- ".join(befunde)
    finally:
        kontext.close()
