"""Review 3, Nachtrag: Kleinigkeiten am Handy (390 px). Auf der Vertragsseite steht das Datum
der heute geltenden Kondition rechtsbündig wie in den übrigen Zeilen, nicht mittig zwischen
Beschriftung und Badge."""

from __future__ import annotations

from typing import Any

import pytest
from playwright.sync_api import Page

from tests.e2e.conftest import HANDY

pytestmark = pytest.mark.e2e

#: Rechte Kanten in px: der Zeile (Inhalt ohne Innenabstand), des Datums (Textknoten vor dem
#: Badge) und des Badges, dazu dessen linke Kante.
KANTEN_JS = """(zelle) => {
  const zeile = zelle.closest("tr");
  const stil = getComputedStyle(zeile);
  const knoten = Array.from(zelle.childNodes).find(
    k => k.nodeType === Node.TEXT_NODE && k.textContent.trim());
  const bereich = document.createRange();
  bereich.selectNodeContents(knoten);
  const datum = bereich.getBoundingClientRect();
  const badge = zelle.querySelector(".badge").getBoundingClientRect();
  return {
    zeile: zeile.getBoundingClientRect().right - parseFloat(stil.paddingRight),
    datum: datum.right,
    badge_links: badge.left,
    badge_rechts: badge.right,
    abstand: parseFloat(getComputedStyle(zelle).columnGap) || 0,
  };
}"""


def test_datum_mit_badge_rechtsbuendig_am_handy(seite: Page, adressen: dict[str, Any]) -> None:
    """Review 3, Nachtrag: Bei 390 px steht in der Konditionszeile mit dem Badge „heute“
    (bzw. „ab Beginn“) das Datum rechtsbündig: Datum und Badge schließen an der rechten Kante
    der Zeile ab, das Datum steht direkt vor dem Badge und nicht mittig."""
    seite.set_viewport_size(HANDY)
    seite.goto(adressen["vertrag_smoke"], wait_until="networkidle")
    zelle = seite.locator('tr.kondition.aktuell td[data-titel="gültig ab"]')
    assert zelle.locator(".badge").count() == 1
    k = zelle.evaluate(KANTEN_JS)
    assert abs(k["zeile"] - k["badge_rechts"]) <= 1, k
    # Rechte Kante des Datums: genau Badge und Abstand von der rechten Kante der Zeile entfernt.
    abstand_zur_zeile = k["zeile"] - k["datum"]
    erwartet = (k["badge_rechts"] - k["badge_links"]) + k["abstand"]
    assert abs(abstand_zur_zeile - erwartet) <= 2, k
