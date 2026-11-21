"""Smoke-Test der Oberfläche (Review 2, C5): die 15 wichtigsten Seiten bei 1440 und 390 px.

Je Seite und Breite:
- kein Status ab 500 (auch nicht bei Nachladungen der Seite selbst),
- keine waagrechte Scrollleiste (``scrollWidth > clientWidth``); die Meldung nennt die
  Elemente, die über den Rand ragen,
- keine englischen Standardtexte: im Text der Seite („Page not found“, „Choose file“,
  Monatsnamen wie „October“) und in eingebauten Feldern des Browsers, die ihren Text selbst
  zeichnen (Datei- und Monatsfelder, siehe ``EINGEBAUTE_FELDER``),
- bei 390 px: Knöpfe und Haken (samt Label) mindestens 44 px hoch (Review 2, C4),
- in Formularen überschneiden sich Label, Eingabe, Fehlertext und nächstes Feld nicht
  (Review 3, Punkt 2, ``tests/e2e/ueberlappung.py``; Fehlerzustände in ``test_formulare.py``),
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
from tests.e2e.ueberlappung import ueberlappende_elemente

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
    # R2-9: Seiten von System, Bank, Mahnwesen und Zählern in der neuen Gestaltung
    ("laeufe", "Läufe"),
    ("zaehler", "Zähler"),
    ("bank", "Bank: Konten"),
    ("bank_umsaetze", "Bank: Umsätze"),
    ("offene_posten", "Offene Posten"),
    ("mahnwesen", "Mahnwesen"),
    ("anbindungen", "Anbindungen"),
    ("abschluesse", "Monatsabschlüsse"),
)
BREITEN = {"1440": DESKTOP, "390": HANDY}

#: Hauptaktion am Vertrag, die den Assistenten startet (Übergabe bzw. Rückgabe am Handy), und
#: der Schritt, den der Test zeigt.
UEBERGABE_STARTEN = "main #vertrag-kopf button.knopf-primaer:has-text('gabe am Handy')"
UEBERGABE_SCHRITT = 3  # Fotos

ENGLISCH = re.compile(
    r"Choose files?|No files? chosen|Page not found|Not Found|Server Error|"
    r"\b(?:January|February|March|May|June|July|October|December)\b",
    re.IGNORECASE,
)
#: Eingebaute Felder, deren Text der Browser in seiner Sprache zeichnet (nicht im DOM):
#: Dateifeld „Choose file / No file chosen“, Monatsfeld „October 2026“.
#: Die eigenen Dateiknöpfe (``.datei-eingabe``, Review 2, C4) verstecken das Dateifeld in
#: ihrem Knopf; es zeichnet keinen Text.
EINGEBAUTE_FELDER = {
    "input[type=file]:not(.datei-eingabe)": "Dateifeld zeigt „Choose file / No file chosen“",
    "input[type=month]": "Monatsfeld zeigt englische Monatsnamen",
}
#: Mindesthöhe der Ziele am Handy (Review 2, C4): Knöpfe und Haken samt Label.
ZIEL_PX = 44

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
    # Apple-Design (PR H): Am laufenden Vertrag ist „Rückgabe am Handy“ die Hauptaktion im
    # Kopf, ohne Umweg über „mehr ▾“.
    seite.click(UEBERGABE_STARTEN)
    seite.wait_for_url(re.compile(r"/schritt/1/"))
    url = re.sub(r"/schritt/1/", f"/schritt/{UEBERGABE_SCHRITT}/", seite.url)
    kontext.close()
    return url.removeprefix(server.basis_url)


def _ueberstehende_elemente(seite: Page) -> list[str]:
    """Die (äußersten) Elemente und Texte, die rechts über die Breite des Fensters ragen.

    Was in einem Vorfahren mit eigenem Scrollen oder Abschneiden liegt (Reiterleiste,
    Tabellenkasten), verbreitert die Seite nicht und bleibt außen vor. Texte zählen mit: Ein
    langes Wort (Mail-Adresse, Pfad) ragt aus seinem Kasten, ohne dass der Kasten selbst
    übersteht (Review 3, Abschluss)."""
    return seite.evaluate(
        """() => {
        const breite = document.documentElement.clientWidth;
        const treffer = [];
        const eingefasst = (e) => {
            for (let v = e.parentElement; v && v !== document.body; v = v.parentElement) {
                if (/(auto|scroll|hidden|clip)/.test(getComputedStyle(v).overflowX))
                    return true;
            }
            return false;
        };
        const name = (e) => e.tagName.toLowerCase() + (e.id ? '#' + e.id : '')
            + (e.className && typeof e.className === 'string'
               ? '.' + e.className.trim().split(/\\s+/).join('.') : '');
        for (const e of document.body.querySelectorAll('*')) {
            const r = e.getBoundingClientRect();
            if (r.width === 0 || r.right <= breite) continue;
            if (e.parentElement && e.parentElement.getBoundingClientRect().right > breite
                && e.parentElement !== document.body) continue;
            if (eingefasst(e)) continue;
            treffer.push(`${name(e)} (bis ${Math.round(r.right)} px)`);
            if (treffer.length >= 5) return treffer;
        }
        const gang = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        for (let t = gang.nextNode(); t; t = gang.nextNode()) {
            const bereich = document.createRange();
            bereich.selectNodeContents(t);
            const r = bereich.getBoundingClientRect();
            const e = t.parentElement;
            if (!e || r.width === 0 || r.right <= breite) continue;
            // eingefasst(t): auch das Element des Texts selbst darf abschneiden (.nur-leser)
            if (e.getBoundingClientRect().right > breite || eingefasst(t)) continue;
            const text = t.textContent.trim().replace(/\\s+/g, ' ').slice(0, 40);
            treffer.push(`Text „${text}“ in ${name(e)} (bis ${Math.round(r.right)} px)`);
            if (treffer.length >= 5) break;
        }
        return treffer;
    }"""
    )


def _kleine_ziele(seite: Page) -> list[str]:
    """Sichtbare Knöpfe und Haken, deren Ziel niedriger als ``ZIEL_PX`` ist. Bei einem Haken
    zählt das größere von Feld und Label (das Label ist das Ziel)."""
    return seite.evaluate(
        """(ziel) => {
        const klein = [];
        const sichtbar = (e) => { const r = e.getBoundingClientRect();
            return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== 'hidden'; };
        const name = (e) => (e.tagName.toLowerCase() + (e.id ? '#' + e.id : '')
            + ' „' + (e.innerText || e.value || e.getAttribute('aria-label') || '').trim()
                .slice(0, 30) + '“');
        for (const e of document.querySelectorAll(
                'button, input[type=submit], input[type=button], a.knopf, summary.knopf')) {
            if (!sichtbar(e)) continue;
            const h = e.getBoundingClientRect().height;
            if (h < ziel - 0.5) klein.push(`${name(e)} ${Math.round(h)} px`);
        }
        for (const e of document.querySelectorAll('input[type=checkbox], input[type=radio]')) {
            if (!sichtbar(e)) continue;
            let h = e.getBoundingClientRect().height;
            const labels = [...(e.labels || [])];
            for (const l of labels) h = Math.max(h, l.getBoundingClientRect().height);
            if (h < ziel - 0.5) klein.push(`${name(e)} ${Math.round(h)} px`);
        }
        return klein;
    }""",
        ZIEL_PX,
    )


def pruefen(seite: Page, handy: bool = False) -> list[str]:
    """Die Befunde einer geladenen Seite (leer: in Ordnung). ``handy``: auch die Größe der
    Ziele prüfen."""
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
    if handy and (klein := _kleine_ziele(seite)):
        befunde.append(f"Ziele unter {ZIEL_PX} px: {', '.join(klein[:6])}")
    if ueberlappend := ueberlappende_elemente(seite):
        befunde.append(f"überlappende Elemente im Formular: {'; '.join(ueberlappend)}")
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
    befunde += serverfehler + pruefen(seite, handy=breite == "390")
    with (SMOKE / "ergebnis.jsonl").open("a", encoding="utf-8") as datei:
        zeile = {"seite": titel, "pfad": pfad, "breite": breite, "befunde": befunde}
        datei.write(json.dumps(zeile, ensure_ascii=False) + "\n")
    assert not befunde, f"{titel} ({pfad}) bei {breite} px:\n- " + "\n- ".join(befunde)
