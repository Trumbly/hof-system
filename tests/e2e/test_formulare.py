"""Formulare im Fehlerzustand ohne überlappende Elemente (Review 3, Punkt 2), 1440 und 390 px.

Der Smoke-Test lädt Seiten nur; Fehlermeldungen und die Radio-Gruppe „Wunschtermin vor dem
Fristende“ erscheinen aber erst nach dem Absenden. Dieser Test öffnet die Dialoge und
Formulare, sendet sie leer bzw. unvollständig ab (ohne die Prüfung des Browsers, damit der
Server antwortet) und prüft dann:

- Fehlermeldungen sind sichtbar,
- Label, Eingabe, Fehlertext und nächstes Feld überschneiden sich nicht
  (``tests/e2e/ueberlappung.py``),
- ein Screenshot unter ``tests/e2e/artefakte/formulare/<breite>/``.

Gespeichert wird nichts: Jedes Absenden scheitert an der Prüfung auf dem Server.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from typing import Any

import pytest
from playwright.sync_api import Locator, Page

from tests.e2e.server import ARTEFAKTE
from tests.e2e.test_smoke import BREITEN
from tests.e2e.ueberlappung import ueberlappende_elemente

pytestmark = pytest.mark.e2e

ZIEL = ARTEFAKTE / "formulare"
DIALOG = "dialog[open]"
#: Wunschtermin vor dem Fristende des Smoke-Vertrags (V-0005): Zugang und gewünschtes Ende.
ZUGANG = "2026-11-01"
WUNSCH_VOR_FRISTENDE = "2026-11-15"


@pytest.fixture(scope="module", autouse=True)
def _artefakte_leeren() -> Iterator[None]:
    shutil.rmtree(ZIEL, ignore_errors=True)
    ZIEL.mkdir(parents=True)
    yield


def _ohne_browserpruefung(formular: Locator) -> None:
    """Das Formular geht ohne die Prüfung des Browsers (``required``) an den Server."""
    formular.evaluate("f => { f.noValidate = true; }")


def _leeren(formular: Locator) -> None:
    """Leert Text-, Zahl- und Datumsfelder, Auswahllisten und Radios eines Formulars."""
    formular.evaluate(
        """f => {
        for (const e of f.querySelectorAll('input, select, textarea')) {
            if (['hidden', 'submit', 'button', 'file'].includes(e.type)) continue;
            if (e.type === 'radio' || e.type === 'checkbox') { e.checked = false; continue; }
            if (e.tagName === 'SELECT') { e.selectedIndex = -1; continue; }
            e.value = '';
        }
    }"""
    )


def _pruefen(
    seite: Page, name: str, breite: str, bereich: str = "main", behaelter: str = "form"
) -> None:
    fehler = seite.locator(f"{bereich} .feld-fehler")
    fehler.first.wait_for(state="visible")
    seite.screenshot(path=ZIEL / breite / f"{name}.png", full_page=True)
    befunde = ueberlappende_elemente(seite, behaelter)
    assert not befunde, f"{name} bei {breite} px:\n- " + "\n- ".join(befunde)


def _kuendigungsdialog(seite: Page, adressen: dict[str, Any]) -> Locator:
    seite.goto(adressen["vertrag_smoke"], wait_until="networkidle")
    seite.click("main a:has-text('Kündigung erfassen')")
    formular = seite.locator(f"{DIALOG} form")
    formular.wait_for(state="visible")
    return formular


@pytest.fixture(params=list(BREITEN))
def breite(request: pytest.FixtureRequest, seite: Page) -> str:
    wert = str(request.param)
    seite.set_viewport_size(BREITEN[wert])
    (ZIEL / wert).mkdir(parents=True, exist_ok=True)
    return wert


def test_kuendigungsdialog_leer(seite: Page, adressen: dict[str, Any], breite: str) -> None:
    """Review 3, Punkt 2: Kündigungsdialog leer abgesendet, Fehler am Feld, nichts
    überlappt."""
    formular = _kuendigungsdialog(seite, adressen)
    _leeren(formular)
    _ohne_browserpruefung(formular)
    formular.locator("button[type=submit]").click()
    _pruefen(seite, "kuendigung-leer", breite, DIALOG)


def test_kuendigungsdialog_wunschtermin(seite: Page, adressen: dict[str, Any], breite: str) -> None:
    """Review 3, Punkt 2: Wunschtermin vor dem Fristende im Vertragsdialog; die Radio-Gruppe
    mit Fehlermeldung überlappt weder sich selbst noch das nächste Feld."""
    formular = _kuendigungsdialog(seite, adressen)
    formular.locator("input[name=kuendigung-zugang_am]").fill(ZUGANG)
    formular.locator("input[name=kuendigung-zum_wunsch]").fill(WUNSCH_VOR_FRISTENDE)
    formular.locator("button[type=submit]").click()
    gruppe = seite.locator(f"{DIALOG} fieldset:has(input[name=kuendigung-wunsch_wahl])")
    gruppe.wait_for(state="visible")
    assert gruppe.locator("legend").inner_text().startswith("Wunschtermin vor dem Fristende")
    _pruefen(seite, "kuendigung-wunschtermin", breite, DIALOG)


def test_kuendigungsanfrage(seite: Page, adressen: dict[str, Any], breite: str) -> None:
    """Review 3, Punkt 2: Kündigungsanfrage (anbindungen/anfrage.html) mit der Radio-Gruppe
    „Wunschtermin vor dem Fristende“: vorausgefüllt und nach dem Absenden ohne Wahl ohne
    Überlappung."""
    seite.goto(adressen["anfrage_kuendigung"], wait_until="networkidle")
    formular = seite.locator("#kuendigung-anlegen form")
    befunde = ueberlappende_elemente(seite)
    assert not befunde, "Kündigungsanfrage:\n- " + "\n- ".join(befunde)
    if not formular.locator("input[name=kuendigung-wunsch_wahl]").count():
        pytest.skip("Die Anfrage des Testhofs hat keinen Wunschtermin vor dem Fristende.")
    formular.evaluate(
        "f => f.querySelectorAll('input[type=radio]').forEach(r => { r.checked = false; })"
    )
    _ohne_browserpruefung(formular)
    formular.locator("button[type=submit]").click()
    seite.wait_for_load_state("networkidle")
    _pruefen(seite, "kuendigungsanfrage-ohne-wahl", breite, "body")


def test_serie_leer(seite: Page, adressen: dict[str, Any], breite: str) -> None:
    """Review 3, Punkt 2: Serienmitteilung leer abgesendet: „Wer keine E-Mail-Adresse hat“
    und die Empfänger als Gruppen, Fehler unter der Gruppe, nichts überlappt."""
    seite.goto(adressen["seiten_r2_8"]["serie_neu"], wait_until="networkidle")
    formular = seite.locator("form#serie-formular")
    _leeren(formular)
    _ohne_browserpruefung(formular)
    formular.locator("button[name=schritt][value=vorschau]").click()
    seite.wait_for_load_state("networkidle")
    for name in ("ohne_mail", "auswahl"):
        gruppe = seite.locator(f"form#serie-formular fieldset:has(input[name={name}])")
        assert gruppe.locator(".feld-fehler").is_visible(), f"kein Fehler an {name}"
    _pruefen(seite, "serie-leer", breite)


def test_beleg_leer(seite: Page, adressen: dict[str, Any], breite: str) -> None:
    """Review 3, Punkt 2: Beleg erfassen leer abgesendet, Fehler am Feld, nichts
    überlappt."""
    seite.goto(adressen["beleg"], wait_until="networkidle")
    formular = seite.locator("form#beleg-formular")
    _leeren(formular)
    _ohne_browserpruefung(formular)
    formular.locator("button[type=submit]").click()
    seite.wait_for_load_state("networkidle")
    _pruefen(seite, "beleg-leer", breite)


def test_stilseite_optionen(seite: Page, adressen: dict[str, Any], breite: str) -> None:
    """Review 3, Punkt 2: Der Baustein auf /stil/ (normal und mit Fehler) ohne Überlappung."""
    seite.goto(adressen["seiten_r2_8"]["stil"] + "#optionen", wait_until="networkidle")
    abschnitt = seite.locator("#optionen")
    assert abschnitt.locator("fieldset legend").count() >= 4
    abschnitt.screenshot(path=ZIEL / breite / "stil-optionen.png")
    _pruefen(seite, "stil", breite, "#optionen", behaelter="#optionen .karte")
