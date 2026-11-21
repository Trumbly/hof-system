"""Review 3, Punkte 7 und 10 im Browser: Hinweis am Betrag vor dem Bestätigen, Cockpit
ohne große Lücken (1440, 1024 und 1920 px). Screenshots unter
tests/e2e/artefakte/review3-r3-5/."""

from __future__ import annotations

from typing import Any

import pytest
from playwright.sync_api import Page

from tests.e2e.server import ARTEFAKTE
from tests.e2e.test_smoke import BREITEN, pruefen

pytestmark = pytest.mark.e2e

ZIEL = ARTEFAKTE / "review3-r3-5"
#: Größte erlaubte Lücke über einer Kachel (Abstand zwischen Kacheln ist 16 px, dazu bis zu
#: einer Zeile des Mauerwerks).
LUECKE_HOECHSTENS = 32

LUECKEN_JS = """() => {
  const raster = document.querySelector("[data-mauerwerk]");
  const kinder = Array.from(raster.children);
  const kacheln = kinder.map(k => k.getBoundingClientRect());
  const oben = raster.getBoundingClientRect().top;
  return Object.fromEntries(kacheln.map((r, i) => {
    const darueber = kacheln.filter((a, j) => j !== i && a.bottom <= r.top + 1
      && a.left < r.right - 1 && a.right > r.left + 1);
    const grenze = darueber.length ? Math.max(...darueber.map(a => a.bottom)) : oben;
    return [kinder[i].id, Math.round(r.top - grenze)];
  }));
}"""


@pytest.mark.parametrize("breite", [1440, 1024, 1920])
def test_cockpit_ohne_grosse_luecken(seite: Page, adressen: dict[str, Any], breite: int) -> None:
    """Review 3, Punkt 10: Im Cockpit bleiben unter kurzen Kacheln keine großen Lücken: Über
    jeder Kachel liegt höchstens der übliche Abstand zur Kachel darüber.

    Design-Review nachher, Befund B (PR H): Das gilt auch für die Karte „Objekte“. Sie steht
    als letzte Kachel im Mauerwerk, nicht mehr darunter, wo bei 1440 px über ihr eine Lücke
    bis zur längsten Spalte blieb."""
    seite.set_viewport_size({"width": breite, "height": 900})
    seite.goto(adressen["seiten"]["cockpit"], wait_until="networkidle")
    seite.wait_for_function("document.querySelector('[data-mauerwerk].mauerwerk-an')")
    ZIEL.mkdir(parents=True, exist_ok=True)
    seite.screenshot(path=ZIEL / f"cockpit-{breite}.png", full_page=True)
    luecken = seite.evaluate(LUECKEN_JS)
    assert "kachel-objekte" in luecken, list(luecken)
    assert seite.locator("[data-mauerwerk] > #kachel-objekte #objekte").count() == 1
    assert max(luecken.values()) <= LUECKE_HOECHSTENS, luecken
    assert not pruefen(seite)


@pytest.mark.parametrize("breite", list(BREITEN))
def test_hinweis_am_betrag_vor_dem_bestaetigen(
    seite: Page, adressen: dict[str, Any], breite: str
) -> None:
    """Review 3, Punkt 7: Weicht der eingetippte Betrag vom gelesenen PDF-Betrag ab, steht der
    Hinweis sofort am Feld, bevor man bestätigt; stimmt er wieder, verschwindet er."""
    seite.set_viewport_size(BREITEN[breite])
    seite.goto(adressen["beleg"], wait_until="networkidle")
    feld = seite.locator("input[name=betrag]")
    original = feld.input_value()
    assert seite.locator(".feld-abweichung").count() == 0

    feld.fill("1,00")
    hinweis = seite.locator(".feld-abweichung")
    hinweis.wait_for()
    assert "weicht vom gelesenen PDF-Betrag" in hinweis.inner_text()
    assert seite.url.endswith(adressen["beleg"])  # nichts abgeschickt
    ZIEL.mkdir(parents=True, exist_ok=True)
    seite.screenshot(path=ZIEL / f"beleg-abweichung-{breite}.png", full_page=True)

    feld.fill(original)
    assert seite.locator(".feld-abweichung").count() == 0
