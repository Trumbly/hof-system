"""Suche in der Kopfzeile mit der Tastatur (Review 2, B1): Strg+K, Pfeiltasten, Enter.

Gegen den Testhof (Seed 42): NW-G-0018 ist die Einheit von V-0005 (tests/e2e/adressen.py).
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import HANDY
from tests.e2e.server import Server

pytestmark = pytest.mark.e2e

TREFFER = ".suche-treffer a.suche-eintrag"


def _vertrag(adressen: dict[str, Any]) -> re.Pattern[str]:
    return re.compile(re.escape(adressen["vertrag_smoke"]) + "$")


def test_strg_k_und_enter_oeffnet_den_ersten_treffer(
    seite: Page, server: Server, adressen: dict[str, Any]
) -> None:
    """Review 2, B1: Strg+K setzt den Cursor in die Suche, Enter öffnet den ersten Treffer."""
    seite.goto("/fristen/")
    seite.keyboard.press("Control+k")
    feld = seite.locator(".leiste .suche-eingabe")
    expect(feld).to_be_focused()
    seite.keyboard.type("V-0005")
    expect(seite.locator(f".leiste {TREFFER}").first).to_be_visible()
    expect(seite.locator(f".leiste {TREFFER}").first).to_have_attribute("aria-selected", "true")
    seite.keyboard.press("Enter")
    seite.wait_for_url(_vertrag(adressen))


def test_pfeiltasten_waehlen_treffer_gruppiert(
    seite: Page, server: Server, adressen: dict[str, Any]
) -> None:
    """Review 2, B1: Treffer stehen gruppiert (Einheiten, Verträge …); Pfeil nach unten wählt
    den nächsten, Enter öffnet ihn."""
    seite.goto("/")
    seite.keyboard.press("Control+k")
    seite.keyboard.type("NW-G-0018")
    liste = seite.locator(".leiste .suche-treffer")
    expect(liste.locator(".suche-gruppe-titel").first).to_have_text("Einheiten")
    expect(liste).to_contain_text("Verträge")
    eintraege = liste.locator("a.suche-eintrag")
    expect(eintraege.nth(0)).to_contain_text("NW-G-0018")
    expect(eintraege.nth(1)).to_contain_text("V-0005")
    seite.keyboard.press("ArrowDown")
    expect(eintraege.nth(1)).to_have_attribute("aria-selected", "true")
    expect(eintraege.nth(0)).to_have_attribute("aria-selected", "false")
    seite.keyboard.press("ArrowUp")
    expect(eintraege.nth(0)).to_have_attribute("aria-selected", "true")
    seite.keyboard.press("ArrowDown")
    seite.keyboard.press("Enter")
    seite.wait_for_url(_vertrag(adressen))


def test_enter_gleich_nach_dem_tippen_wartet_auf_die_treffer(
    seite: Page, server: Server, adressen: dict[str, Any]
) -> None:
    """Review 2, B1: Wer tippt und sofort Enter drückt, landet trotzdem beim ersten Treffer."""
    seite.goto("/")
    seite.keyboard.press("Control+k")
    seite.keyboard.type("V-0005")
    seite.keyboard.press("Enter")
    seite.wait_for_url(_vertrag(adressen))


def test_escape_schliesst_und_kein_treffer(seite: Page, server: Server) -> None:
    """Review 2, B1: Ohne Treffer sagt die Liste das; Escape schließt sie."""
    seite.goto("/")
    seite.keyboard.press("Control+k")
    seite.keyboard.type("xqzxqz")
    liste = seite.locator(".leiste .suche-treffer")
    expect(liste).to_contain_text("Nichts gefunden")
    seite.keyboard.press("Escape")
    expect(liste).to_be_hidden()


def test_suche_am_handy_in_der_kopfzeile(
    seite: Page, server: Server, adressen: dict[str, Any]
) -> None:
    """Review 2, B1 und C1: Am Handy steht die Suche in der Kopfzeile; Antippen eines
    Treffers öffnet ihn, ohne waagrechte Scrollleiste."""
    seite.set_viewport_size(HANDY)
    seite.goto("/")
    feld = seite.locator(".kopf-handy .suche-eingabe")
    expect(feld).to_be_visible()
    feld.fill("Bohnbach")
    treffer = seite.locator(f".kopf-handy {TREFFER}")
    expect(treffer.first).to_be_visible()
    breiten = seite.evaluate(
        "() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]"
    )
    assert breiten[0] <= breiten[1]
    seite.locator(f".kopf-handy {TREFFER}", has_text="V-0005").click()
    seite.wait_for_url(_vertrag(adressen))
