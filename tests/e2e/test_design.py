"""Apple-Design (PR A): Die Oberflächentests laufen im Apple-Design (``hof.settings.e2e``:
``HOF_DESIGN_STANDARD = "apple"``); Smoke-Test, alle Seiten, Formulare und Suche prüfen es
damit schon. Dieses Modul prüft zusätzlich:

- Das Apple-Design ist voreingestellt, der Server hat keinen Schalter (ohne ``DEBUG``).
- Seitenleiste bei 1440 × 900 mit Maus: Zeilen 36 px, alle Einträge sichtbar ohne Scrollen.
- Klickflächen in Rahmen, Startseite und auf ``/stil/`` mindestens 44 px, bei 390 und
  1440 px, hell und dunkel; dazu die Prüfung des Smoke-Tests (Überlauf, Überlappung).
- Die bisherige Gestaltung („standard“), solange es sie gibt: ein zweiter ``runserver`` auf
  derselben Datenbank mit ``HOF_DESIGN_STANDARD=standard``, Startseite und Arbeitslisten bei
  390 und 1440 px durch dieselbe Prüfung. Die Anmeldung gilt auch dort (Cookies hängen nicht
  am Port).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from playwright.sync_api import Browser, Page

from tests.e2e import server as e2e_server
from tests.e2e.conftest import DESKTOP, HANDY
from tests.e2e.server import ARTEFAKTE, Server
from tests.e2e.test_smoke import ZIEL_PX, pruefen

pytestmark = pytest.mark.e2e

BREITEN = {"1440": DESKTOP, "390": HANDY}
SCHEMAS = ("light", "dark")
ORDNER = ARTEFAKTE / "design"

#: Klickflächen, die im Apple-Design mindestens 44 px hoch sein müssen: Rahmen (außer den
#: Zeilen der Seitenleiste mit Maus, 36 px), Knöpfe, Listenzeilen, Kartenlinks, Kennzahlen,
#: Kacheln, Reiter, Felder, Menüeinträge.
ZIELE = (
    ".leiste .suche-eingabe, .leiste-fuss button, .leiste .marke, "
    ".kopf-handy .marke, .kopf-handy .suche-eingabe, .unten a, .unten button, "
    ".knopf, a.liste-zeile, a.karte-link, a.kennzahl, .cockpit-raster a.kachel, "
    ".reiter a, .feld-eingabe, .feld-auswahl, .haken-label, .aufklapper > summary"
)


def _kleine_ziele(seite: Page) -> list[str]:
    return seite.evaluate(
        """([selektor, ziel]) => {
        const klein = [];
        for (const e of document.querySelectorAll(selektor)) {
            const r = e.getBoundingClientRect();
            if (r.width === 0 || r.height === 0) continue;
            if (getComputedStyle(e).visibility === 'hidden') continue;
            if (e.closest('[hidden], dialog:not([open]), .menue:not([open]) .menue-liste'))
                continue;
            if (r.height < ziel - 0.5) {
                const text = (e.innerText || e.getAttribute('aria-label') || e.placeholder
                    || '').trim().slice(0, 30);
                klein.push(`${e.tagName.toLowerCase()}.${e.className} „${text}“ `
                    + `${Math.round(r.height)} px`);
            }
        }
        return klein;
    }""",
        [ZIELE, ZIEL_PX],
    )


def test_apple_ist_voreingestellt(seite: Page, adressen: dict[str, Any]) -> None:
    """Apple-Design (PR A): Ohne Parameter zeigt der E2E-Server das Apple-Design; ohne DEBUG
    gibt es keinen Schalter, und ?design=standard wirkt nicht."""
    seite.goto(adressen["seiten"]["cockpit"] + "?design=standard")
    assert seite.locator("html[data-design=apple]").count() == 1
    assert seite.locator(".design-schalter").count() == 0


def test_seitenleiste_passt_bei_1440_mal_900(seite: Page, adressen: dict[str, Any]) -> None:
    """Apple-Design (PR A, Punkt 3): Mit Maus sind die Zeilen der Seitenleiste 36 px hoch,
    und bei 1440 × 900 sind alle Einträge ohne Scrollen sichtbar."""
    seite.set_viewport_size({"width": 1440, "height": 900})
    seite.goto(adressen["seiten"]["cockpit"], wait_until="networkidle")
    mass = seite.evaluate(
        """() => {
        const nav = document.querySelector('.leiste .nav-neu');
        const links = [...nav.querySelectorAll('a')];
        const unten = nav.getBoundingClientRect().bottom;
        return {
            hoehen: [...new Set(links.map(a => Math.round(a.getBoundingClientRect().height)))],
            scroll: nav.scrollHeight, sichtbar: nav.clientHeight,
            letzter: links[links.length - 1].getBoundingClientRect().bottom, unten,
            fenster: window.innerHeight,
        };
    }"""
    )
    assert mass["hoehen"] == [36], mass
    assert mass["scroll"] <= mass["sichtbar"] + 1, mass
    assert mass["letzter"] <= min(mass["unten"], mass["fenster"]), mass


@pytest.mark.parametrize("schema", SCHEMAS)
@pytest.mark.parametrize("breite", list(BREITEN))
@pytest.mark.parametrize("schluessel", ("cockpit", "arbeitslisten", "stil"))
def test_apple_seite(
    browser: Browser,
    anmeldung: Any,
    server: Server,
    adressen: dict[str, Any],
    schluessel: str,
    breite: str,
    schema: str,
) -> None:
    """Apple-Design (PR A): Startseite, Arbeitslisten und /stil/ bei 390 und 1440 px, hell und
    dunkel: Prüfung des Smoke-Tests (Überlauf, Überlappung) und Klickflächen mindestens
    44 px in Rahmen und Bausteinen."""
    kontext = browser.new_context(
        base_url=server.basis_url,
        storage_state=str(anmeldung),
        viewport=BREITEN[breite],
        color_scheme=schema,  # type: ignore[arg-type]
        locale="en-US",
        timezone_id="Europe/Berlin",
    )
    seite = kontext.new_page()
    seite.set_default_timeout(15_000)
    try:
        pfad = (
            adressen["seiten_r2_8"]["stil"]
            if schluessel == "stil"
            else adressen["seiten"][schluessel]
        )
        antwort = seite.goto(pfad, wait_until="networkidle")
        assert antwort is not None and antwort.status == 200
        ziel = ORDNER / breite / f"{schluessel}-{schema}.png"
        ziel.parent.mkdir(parents=True, exist_ok=True)
        seite.screenshot(path=ziel, full_page=True)

        befunde = pruefen(seite, handy=breite == "390")
        if klein := _kleine_ziele(seite):
            befunde.append(f"Klickflächen unter {ZIEL_PX} px: {', '.join(klein[:6])}")
        assert not befunde, f"{schluessel} bei {breite} px ({schema}):\n- " + "\n- ".join(befunde)
    finally:
        kontext.close()


# --- Bisherige Gestaltung ---------------------------------------------------------------


@pytest.fixture(scope="module")
def standard_server(server: Server) -> Iterator[str]:
    """Zweiter Server auf der Datenbank des ersten, mit HOF_DESIGN_STANDARD=standard."""
    umgebung = {**server.umgebung, "HOF_DESIGN_STANDARD": "standard"}
    port = e2e_server._freier_port()
    log = ARTEFAKTE / "server-standard.log"
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


@pytest.mark.parametrize("breite", list(BREITEN))
@pytest.mark.parametrize("schluessel", ("cockpit", "arbeitslisten"))
def test_standard_design_seite(
    browser: Browser,
    anmeldung: Any,
    standard_server: str,
    adressen: dict[str, Any],
    schluessel: str,
    breite: str,
) -> None:
    """Apple-Design (PR A): Die bisherige Gestaltung bleibt nutzbar, solange es sie gibt:
    Startseite und Arbeitslisten bei 390 und 1440 px ohne Befund des Smoke-Tests."""
    kontext = browser.new_context(
        base_url=standard_server,
        storage_state=str(anmeldung),
        viewport=BREITEN[breite],
        locale="en-US",
        timezone_id="Europe/Berlin",
    )
    seite = kontext.new_page()
    seite.set_default_timeout(15_000)
    try:
        antwort = seite.goto(adressen["seiten"][schluessel], wait_until="networkidle")
        assert antwort is not None and antwort.status == 200
        assert seite.locator("html[data-design]").count() == 0
        assert seite.locator("link[href*='css/apple/']").count() == 0
        befunde = pruefen(seite, handy=breite == "390")
        assert not befunde, f"{schluessel} bei {breite} px (standard):\n- " + "\n- ".join(befunde)
    finally:
        kontext.close()
