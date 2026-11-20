"""Apple-Design, Bereich Arbeitslisten (PR B): Posteingang mit allen Arten, Umsatz einordnen und
Klärfall in der Zeile, Gruppen und Sammelleiste, dazu die Seiten „Umsatz einordnen“ und
„Zahlung klären“.

Je Seite bei 390 und 1440 px, hell und dunkel: die Prüfung des Smoke-Tests (Überlauf,
Überlappung in Formularen, englische Texte) und Klickflächen mindestens 44 px. Bei 320 px und
bei 360 und 390 px mit Verdana (breitere Schriften unter Linux) dieselbe Prüfung ohne Ziele.
Dazu am Handy: Nach der Wahl einer Art (auch „Alle offenen“) zeigt die Seite die Liste statt
der Arten.

Screenshots unter ``tests/e2e/artefakte/apple-arbeitslisten/<breite>/``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from urllib.parse import urlencode

import pytest
from playwright.sync_api import Browser, BrowserContext, Page

from apps.abgleich.services import ART_KLAEREN
from apps.kassenbuch.services import ART_EINORDNEN
from tests.e2e.conftest import DESKTOP, HANDY
from tests.e2e.server import ARTEFAKTE, Server
from tests.e2e.test_smoke import ZIEL_PX, pruefen

pytestmark = pytest.mark.e2e

BREITEN = {"1440": DESKTOP, "390": HANDY}
SCHEMAS = ("light", "dark")
ORDNER = ARTEFAKTE / "apple-arbeitslisten"

#: Klickflächen des Bereichs: Knöpfe, Felder, Haken, Arten, Wahl der Zeile, Kopf der Gruppe.
ZIELE = (
    ".knopf, .feld-eingabe, .feld-auswahl, .haken-label, .aufklapper > summary, "
    ".arten-liste a, .posten-wahl, .posteingang-zurueck"
)


def _liste(art: str = "", status: str = "") -> str:
    parameter = {k: v for k, v in (("art", art), ("status", status)) if v}
    return "/arbeitslisten/" + (f"?{urlencode(parameter)}" if parameter else "")


def _seiten(adressen: dict[str, Any]) -> dict[str, str]:
    return {
        "arten": _liste(),
        "alle": _liste("alle"),
        "einordnen": _liste(ART_EINORDNEN),
        "klaeren": _liste(ART_KLAEREN),
        "fristen": _liste("Frist fällig"),
        "anfragen": _liste("Buchungsanfrage prüfen"),
        "zurueckgestellt": _liste(status="zurueckgestellt"),
        "umsatz-einordnen": adressen["seiten"]["umsatz_einordnen"],
        "klaerfall": adressen["seiten"]["klaerfall"],
    }


SEITEN = (
    "arten",
    "alle",
    "einordnen",
    "klaeren",
    "fristen",
    "anfragen",
    "zurueckgestellt",
    "umsatz-einordnen",
    "klaerfall",
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
                const text = (e.innerText || e.getAttribute('aria-label') || '').trim()
                    .slice(0, 30);
                klein.push(`${e.tagName.toLowerCase()}.${e.className} „${text}“ `
                    + `${Math.round(r.height)} px`);
            }
        }
        return klein;
    }""",
        [ZIELE, ZIEL_PX],
    )


@pytest.fixture
def kontext_fuer(browser: Browser, anmeldung: Any, server: Server) -> Iterator[Any]:
    offen: list[BrowserContext] = []

    def _neu(viewport: dict[str, int], schema: str = "light") -> Page:
        kontext = browser.new_context(
            base_url=server.basis_url,
            storage_state=str(anmeldung),
            viewport=viewport,  # type: ignore[arg-type]
            color_scheme=schema,  # type: ignore[arg-type]
            locale="en-US",
            timezone_id="Europe/Berlin",
        )
        offen.append(kontext)
        seite = kontext.new_page()
        seite.set_default_timeout(15_000)
        return seite

    yield _neu
    for kontext in offen:
        kontext.close()


@pytest.mark.parametrize("schema", SCHEMAS)
@pytest.mark.parametrize("breite", list(BREITEN))
@pytest.mark.parametrize("schluessel", SEITEN)
def test_apple_arbeitslisten_seite(
    kontext_fuer: Any, adressen: dict[str, Any], schluessel: str, breite: str, schema: str
) -> None:
    """Apple-Design, Bereich Arbeitslisten: Seite bei 390 und 1440 px, hell und dunkel, ohne
    Befund des Smoke-Tests und mit Klickflächen von mindestens 44 px."""
    seite = kontext_fuer(BREITEN[breite], schema)
    antwort = seite.goto(_seiten(adressen)[schluessel], wait_until="networkidle")
    assert antwort is not None and antwort.status == 200
    ziel = ORDNER / breite / f"{schluessel}{'' if schema == 'light' else '-dunkel'}.png"
    ziel.parent.mkdir(parents=True, exist_ok=True)
    seite.screenshot(path=ziel, full_page=True)

    befunde = pruefen(seite, handy=breite == "390")
    if klein := _kleine_ziele(seite):
        befunde.append(f"Klickflächen unter {ZIEL_PX} px: {', '.join(klein[:6])}")
    assert not befunde, f"{schluessel} bei {breite} px ({schema}):\n- " + "\n- ".join(befunde)


#: Schmale Breiten: 320 px, dazu 360 und 390 px mit Verdana als Ersatz für die breiteren
#: Schriften der Pipeline unter Linux (wie ``test_seiten_r2_8.test_seite_schmal``).
VERDANA = "* { font-family: Verdana, sans-serif !important; }"
SCHMAL_BREITEN = {"320": (320, ""), "360-breit": (360, VERDANA), "390-breit": (390, VERDANA)}


@pytest.mark.parametrize("breite", list(SCHMAL_BREITEN))
@pytest.mark.parametrize("schluessel", SEITEN)
def test_apple_arbeitslisten_schmal(
    kontext_fuer: Any, adressen: dict[str, Any], schluessel: str, breite: str
) -> None:
    """Apple-Design, Bereich Arbeitslisten: bei 320 px und bei 360 und 390 px mit breiter
    Schrift (Verdana) keine waagrechte Scrollleiste und keine Überlappung im Formular."""
    px, stil = SCHMAL_BREITEN[breite]
    seite = kontext_fuer({"width": px, "height": 700})
    seite.goto(_seiten(adressen)[schluessel], wait_until="networkidle")
    if stil:
        seite.add_style_tag(content=stil)
    ziel = ORDNER / breite / f"{schluessel}.png"
    ziel.parent.mkdir(parents=True, exist_ok=True)
    seite.screenshot(path=ziel, full_page=True)
    befunde = pruefen(seite)
    assert not befunde, f"{schluessel} bei {breite} px:\n- " + "\n- ".join(befunde)


@pytest.mark.parametrize("art", ("Alle offenen", ART_EINORDNEN))
def test_handy_zeigt_nach_wahl_der_art_die_liste(kontext_fuer: Any, art: str) -> None:
    """Apple-Design, Bereich Arbeitslisten: Am Handy zeigt die Seite erst die Arten; nach dem
    Tippen auf eine Art (auch „Alle offenen“) die Liste mit „← Arten“ und der Art als Titel.
    „← Arten“ führt zurück."""
    seite = kontext_fuer(HANDY)
    seite.goto("/arbeitslisten/", wait_until="networkidle")
    assert seite.locator(".posteingang-arten").is_visible()
    assert not seite.locator(".posteingang-liste").is_visible()

    seite.locator(".arten-liste a", has_text=art).first.click()
    seite.wait_for_selector(".posteingang-gewaehlt")
    assert seite.locator(".posteingang-liste").is_visible()
    assert not seite.locator(".posteingang-arten").is_visible()
    titel = seite.locator("#posteingang-titel")
    assert titel.is_visible() and titel.inner_text().startswith(art.split()[0])
    assert seite.locator(".posten-zeile").first.is_visible()

    seite.locator(".posteingang-zurueck").click()
    seite.wait_for_selector(".posteingang-arten", state="visible")
    assert not seite.locator(".posteingang-liste").is_visible()
