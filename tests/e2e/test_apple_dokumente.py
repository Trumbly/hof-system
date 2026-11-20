"""Apple-Design, Bereich Dokumente (PR E): die Seiten der Ablage, der Eingangsrechnungen,
Lieferanten, Vorlagen und Schreiben im Apple-Design.

Je Seite bei 390 und 1440 px, hell und dunkel: Prüfung des Smoke-Tests (kein Serverfehler,
keine waagrechte Scrollleiste, keine englischen Standardtexte, keine Überlappung in
Formularen) und Klickflächen der Bausteine mindestens 44 px (wie ``test_design.py``). Dazu
jede Seite bei 320 px und mit breiter Schrift (Verdana) bei 360 und 390 px ohne Überlauf.

Die Adressen der Detailseiten sucht der Test über die Links der Listen (Dokument, Rechnung,
Lieferant, Vorlage im Entwurf, Briefkopf). Eine Serie gibt es im frischen Testhof nicht; der
Test erzeugt eine über „Serienmitteilung“ (nur erzeugt, nichts gesendet).
Screenshots unter ``tests/e2e/artefakte/apple-dokumente/<breite>/``.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, Response

from tests.e2e.conftest import DESKTOP, HANDY
from tests.e2e.server import ARTEFAKTE, Server
from tests.e2e.test_design import _kleine_ziele
from tests.e2e.test_smoke import pruefen

pytestmark = pytest.mark.e2e

BREITEN = {"1440": DESKTOP, "390": HANDY}
SCHEMAS = ("light", "dark")
ORDNER = ARTEFAKTE / "apple-dokumente"

#: Seiten des Bereichs (Schlüssel in der Reihenfolge der Screenshots)
SEITEN = (
    "ablage",
    "dokument",
    "beleg",
    "beleg_hochladen",
    "beleg_aufteilen",
    "belegmappe",
    "rechnungen",
    "rechnung",
    "lieferanten",
    "lieferant",
    "vorlagen",
    "vorlage",
    "briefkopf",
    "vorlage_neu",
    "schreiben_liste",
    "schreiben",
    "serie_neu",
    "serie",
)


def _erster_link(seite: Page, pfad: str, selektor: str) -> str | None:
    seite.goto(pfad, wait_until="domcontentloaded")
    link = seite.locator(selektor).first
    if link.count() == 0:
        return None
    return link.get_attribute("href")


def _serie_erzeugen(seite: Page, pfad: str) -> str | None:
    """Erzeugt über die Serienmitteilung eine Serie (nichts wird gesendet)."""
    seite.goto(pfad, wait_until="domcontentloaded")
    vorlage = seite.locator("select[name=vorlage]")
    werte = [w for w in vorlage.locator("option").evaluate_all("os => os.map(o => o.value)") if w]
    if not werte:
        return None
    vorlage.select_option(werte[0])
    haken = seite.locator("input[name=auswahl]")
    for i in range(min(2, haken.count())):
        haken.nth(i).check()
    seite.click("button[value=vorschau]")
    seite.wait_for_load_state("domcontentloaded")
    erzeugen = seite.locator("button[value=erzeugen]")
    if erzeugen.count() == 0:
        return None
    erzeugen.click()
    seite.wait_for_load_state("domcontentloaded")
    treffer = re.search(r"/dokumente/serien/[0-9a-f-]{36}/", seite.url)
    return treffer.group(0) if treffer else None


@pytest.fixture(scope="module")
def seiten(browser: Browser, anmeldung: Any, server: Server) -> dict[str, str | None]:
    """Adressen aller Seiten des Bereichs; ``None``, wenn es die Seite im Testhof nicht gibt."""
    r2_8 = server.adressen["seiten_r2_8"]
    kontext = browser.new_context(base_url=server.basis_url, storage_state=str(anmeldung))
    seite = kontext.new_page()
    seite.set_default_timeout(30_000)
    try:
        # Rechnung → ihr (bestätigter) Beleg → dessen Dokument
        rechnung = _erster_link(
            seite, "/dokumente/rechnungen/", "a.liste-zeile[href*='/rechnungen/']"
        )
        beleg_bestaetigt = (
            _erster_link(seite, rechnung, "dl a[href*='/belege/']") if rechnung else None
        )
        dokument = (
            _erster_link(seite, beleg_bestaetigt, "dl a[href*='/dokumente/dokument/']")
            if beleg_bestaetigt
            else None
        )
        vorlagen = "/dokumente/vorlagen/"
        adressen = {
            "ablage": r2_8["ablage"],
            "dokument": dokument,
            "beleg": r2_8["beleg"],
            "beleg_hochladen": "/dokumente/belege/hochladen/",
            "beleg_aufteilen": f"{beleg_bestaetigt}aufteilen/" if beleg_bestaetigt else None,
            "belegmappe": r2_8["belegmappe"],
            "rechnungen": "/dokumente/rechnungen/",
            "rechnung": rechnung,
            "lieferanten": "/dokumente/lieferanten/",
            "lieferant": _erster_link(
                seite,
                "/dokumente/lieferanten/",
                "td a[href*='/lieferanten/']:not([href$='/lieferanten/'])",
            ),
            "vorlagen": vorlagen,
            "vorlage": _erster_link(seite, vorlagen, "a.liste-zeile:has-text('Entwurf')"),
            "briefkopf": _erster_link(seite, vorlagen, "a.liste-zeile:has-text('Briefkopf')"),
            "vorlage_neu": "/dokumente/vorlagen/neu/",
            "schreiben_liste": r2_8["schreiben_liste"],
            "schreiben": r2_8.get("schreiben"),
            "serie_neu": r2_8["serie_neu"],
            "serie": _serie_erzeugen(seite, r2_8["serie_neu"]),
        }
    finally:
        kontext.close()
    return adressen


def _oeffnen(browser: Browser, anmeldung: Any, server: Server, viewport: Any, schema: str) -> Any:
    kontext = browser.new_context(
        base_url=server.basis_url,
        storage_state=str(anmeldung),
        viewport=viewport,
        color_scheme=schema,  # type: ignore[arg-type]
        locale="en-US",
        timezone_id="Europe/Berlin",
    )
    seite = kontext.new_page()
    seite.set_default_timeout(15_000)
    return kontext, seite


def _laden(seite: Page, server: Server, pfad: str) -> list[str]:
    serverfehler: list[str] = []

    def _antwort(antwort: Response) -> None:
        if antwort.status >= 500 and antwort.url.startswith(server.basis_url):
            serverfehler.append(f"{antwort.status} bei {antwort.url}")

    seite.on("response", _antwort)
    antwort = seite.goto(pfad, wait_until="networkidle")
    assert antwort is not None and antwort.status == 200, f"{pfad}: {antwort and antwort.status}"
    return serverfehler


@pytest.mark.parametrize("schema", SCHEMAS)
@pytest.mark.parametrize("breite", list(BREITEN))
@pytest.mark.parametrize("schluessel", SEITEN)
def test_apple_dokumente(
    browser: Browser,
    anmeldung: Any,
    server: Server,
    seiten: dict[str, str | None],
    schluessel: str,
    breite: str,
    schema: str,
) -> None:
    """Apple-Design, Bereich Dokumente: jede Seite bei 390 und 1440 px, hell und dunkel, ohne
    Befund des Smoke-Tests und mit Klickflächen von mindestens 44 px."""
    pfad = seiten[schluessel]
    if pfad is None:
        pytest.skip(f"Keine Seite {schluessel} im Testhof.")
    kontext, seite = _oeffnen(browser, anmeldung, server, BREITEN[breite], schema)
    try:
        befunde = _laden(seite, server, pfad)
        assert seite.locator("html[data-design=apple]").count() == 1
        ziel = ORDNER / breite / f"{SEITEN.index(schluessel) + 1:02d}-{schluessel}-{schema}.png"
        ziel.parent.mkdir(parents=True, exist_ok=True)
        seite.screenshot(path=ziel, full_page=True)
        befunde += pruefen(seite, handy=breite == "390")
        if klein := _kleine_ziele(seite):
            befunde.append(f"Klickflächen unter 44 px: {', '.join(klein[:6])}")
        assert not befunde, f"{schluessel} bei {breite} px ({schema}):\n- " + "\n- ".join(befunde)
    finally:
        kontext.close()


#: Schmale Breiten: 320 px wie gesehen; 360 und 390 px mit Verdana als Ersatz für die
#: breiteren Schriften unter Linux (wie ``test_seiten_r2_8.test_seite_schmal``).
VERDANA = "* { font-family: Verdana, sans-serif !important; }"
SCHMAL = {"320": (320, ""), "360-breit": (360, VERDANA), "390-breit": (390, VERDANA)}


@pytest.mark.parametrize("breite", list(SCHMAL))
@pytest.mark.parametrize("schluessel", SEITEN)
def test_apple_dokumente_schmal(
    browser: Browser,
    anmeldung: Any,
    server: Server,
    seiten: dict[str, str | None],
    schluessel: str,
    breite: str,
) -> None:
    """Apple-Design, Bereich Dokumente: jede Seite auch bei 320 px und mit breiter Schrift
    bei 360 und 390 px ohne Überlauf."""
    pfad = seiten[schluessel]
    if pfad is None:
        pytest.skip(f"Keine Seite {schluessel} im Testhof.")
    px, stil = SCHMAL[breite]
    kontext, seite = _oeffnen(browser, anmeldung, server, {"width": px, "height": 700}, "light")
    try:
        befunde = _laden(seite, server, pfad)
        if stil:
            seite.add_style_tag(content=stil)
        befunde += pruefen(seite)
        assert not befunde, f"{schluessel} bei {breite} px:\n- " + "\n- ".join(befunde)
    finally:
        kontext.close()
