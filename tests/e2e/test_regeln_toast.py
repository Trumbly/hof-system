"""Review 3, Punkt 3 im Browser: Regel merken, Toast-Link, Zahlen in Kopf und Spalte.

Gegen den Testhof (Seed 42) mit Tageslauf: „Umsätze einordnen“ hat Gruppen „… ohne Regel“.
Die Tests nehmen eine Gruppe ohne „Material Baumarkt“, denn deren Umsätze brauchen die
Klickmessung und ``tests/e2e/adressen.py``.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Locator, Page, expect

pytestmark = pytest.mark.e2e

EINORDNEN = "/arbeitslisten/?art=Umsätze einordnen"


def _zahlen_gleich(seite: Page) -> None:
    """Die Zahl im Listenkopf und die der gewählten Art links stimmen überein."""
    kopf = seite.locator("#posteingang-anzahl")
    links = seite.locator("#posteingang-arten a[aria-current=page] .zaehler")
    expect(kopf).to_have_text(links.inner_text())


def _gruppe_ohne_regel(seite: Page) -> Locator:
    return (
        seite.locator(".posten-gruppe")
        .filter(has_text="ohne Regel")
        .filter(has_not_text="Material Baumarkt")
        .first
    )


def test_toast_link_jetzt_anwenden_funktioniert(seite: Page) -> None:
    """Review 3, Punkt 3: Nach „Übernehmen“ mit „Als Regel merken“ steht der Toast mit
    „jetzt anwenden →“ auch nach acht Sekunden noch da; die Gruppe heißt „… passen auf Regel
    #…“; der Link führt zur Vorschau, und von dort bucht ein Klick die übrigen Umsätze."""
    seite.goto(EINORDNEN)
    gruppe = _gruppe_ohne_regel(seite)
    expect(gruppe).to_be_visible()
    zeile = gruppe.locator("[data-posten]").nth(1)
    zeile.scroll_into_view_if_needed()
    zeile.locator("input[name$='-regel']").check()
    vorher = seite.evaluate("() => window.scrollY")
    zeile.locator("[data-uebernehmen]").click()

    toast = seite.locator("#toasts .toast").filter(has_text="angelegt").last
    link = toast.locator(".toast-link")
    expect(link).to_have_text(re.compile("jetzt anwenden"))
    expect(
        seite.locator(".posten-gruppe summary").filter(has_text="passen auf Regel #").first
    ).to_be_visible()
    _zahlen_gleich(seite)
    # Die Liste lädt neu, die Seite springt dabei nicht (früher ans Ende, Scroll-Anker).
    assert abs(seite.evaluate("() => window.scrollY") - vorher) < 100

    seite.wait_for_timeout(9_000)
    expect(link).to_be_visible()  # Toast mit Link bleibt stehen

    link.click()
    seite.wait_for_url(re.compile(r"/kassenbuch/regeln/\d+/anwenden/$"))
    knopf = seite.get_by_role("button", name=re.compile(r"^\d+ Umsätze buchen$"))
    expect(knopf).to_be_visible()
    knopf.click()
    seite.wait_for_url(re.compile(r"/kassenbuch/regeln/$"))
    expect(seite.locator("#toasts")).to_contain_text("Umsätze gebucht.")


def _zurueckstellen_knopf(zeile: Locator) -> Locator:
    """„Zurückstellen“ der Zeile; steht er im Menü „mehr ▾“, wird es geöffnet."""
    knopf = zeile.locator("[data-aktion=zurueckstellen]")
    if not knopf.is_visible():
        zeile.locator("details.menue > summary").click()
    return knopf


def test_beide_zahlen_gleich_nach_einer_aktion(seite: Page) -> None:
    """Review 3, Punkt 3: Nach einer Aktion in der Zeile stimmen die Zahl im Listenkopf und
    die Zahl links überein (OOB zusammen), in „Alle offenen“ wie in „Zurückgestellt“."""
    seite.goto("/arbeitslisten/")
    _zahlen_gleich(seite)
    vorher = int(seite.locator("#posteingang-anzahl").inner_text())
    zeile = seite.locator("#posteingang [data-posten]").filter(
        has=seite.locator("[data-aktion=zurueckstellen]")
    )
    zeile = zeile.filter(has_not_text="Material Baumarkt").first
    posten_id = zeile.get_attribute("id")
    _zurueckstellen_knopf(zeile).click()

    expect(seite.locator(f"#{posten_id}")).to_have_count(0)
    expect(seite.locator("#posteingang-anzahl")).to_have_text(str(vorher - 1))
    _zahlen_gleich(seite)

    # Zurück: in „Zurückgestellt“ wieder öffnen; auch dort laufen die Zahlen gleich.
    seite.locator("#posteingang-arten a").filter(has_text="Zurückgestellt").click()
    expect(seite.locator("#posteingang-titel")).to_contain_text("Zurückgestellt")
    zurueck = int(seite.locator("#posteingang-anzahl").inner_text())
    zeile = seite.locator(f"#{posten_id}")
    knopf = zeile.locator("[data-aktion=wieder_oeffnen]")
    if not knopf.is_visible():
        zeile.locator("details.menue > summary").click()
    knopf.click()
    expect(seite.locator(f"#{posten_id}")).to_have_count(0)
    expect(seite.locator("#posteingang-anzahl")).to_have_text(str(zurueck - 1))
    _zahlen_gleich(seite)
