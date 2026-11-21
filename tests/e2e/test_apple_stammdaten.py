"""Apple-Design, Bereich Stammdaten (PR C).

Die Seiten der Stammdaten (Übersicht, Einheiten, Einheit, Einheit neu, Leerstand, Verträge,
Vertrag, Vertrag neu, Parteien, Partei, Partei neu, Übergaben, Buchungs- und
Kündigungsanfrage) und der Assistent Übergabe (sieben Schritte) bei 390 und 1440 px, hell und
dunkel. Je Seite die Prüfung des Smoke-Tests (Überlauf, Überlappung in Formularen, englische
Texte, Ziele am Handy) und Klickflächen mindestens 44 px (``test_design._kleine_ziele``).
Dazu bei 320 px nur der Überlauf.

Der Assistent hat am Handy einen Fuß, der unten klebt; über ihm läuft der Inhalt durch. Die
Prüfung läuft deshalb am Ende der Seite, wo der Fuß an seinem Platz unter dem Formular steht.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from playwright.sync_api import Browser, Page

from tests.e2e.conftest import DESKTOP, HANDY
from tests.e2e.klickmessung import ablauf_uebergabe_ohne_kaution as uebergabe_ablauf
from tests.e2e.klickmessung.messung import Ergebnis, Messung
from tests.e2e.server import ARTEFAKTE, Server
from tests.e2e.test_design import _kleine_ziele
from tests.e2e.test_smoke import ZIEL_PX, pruefen

pytestmark = pytest.mark.e2e

BREITEN = {"1440": DESKTOP, "390": HANDY}
SCHMAL = {"width": 320, "height": 700}
SCHEMAS = ("light", "dark")
ORDNER = ARTEFAKTE / "apple-stammdaten"
#: Freie Garage für den Vertrag im Entwurf (nicht die der Klickmessung, NW-G-0012/0013).
EINHEIT_ENTWURF = "NW-G-0019"
#: Schritte der Übergabe: Vorab, Zähler, Fotos, Schlüssel, Kaution, Unterschrift, Abschluss.
UEBERGABE_SCHRITTE = 7

SEITEN = (
    "uebersicht",
    "einheiten",
    "einheit",
    "einheit_neu",
    "leerstand",
    "vertraege",
    "vertrag",
    "vertrag_neu",
    "parteien",
    "partei",
    "partei_neu",
    "uebergaben",
    "anfragen",
    "anfrage_buchung",
    "anfrage_kuendigung",
)
ASSISTENT = tuple(f"uebergabe_{n}" for n in range(1, UEBERGABE_SCHRITTE + 1))


@pytest.fixture(scope="module")
def stammdaten_seiten(browser: Browser, anmeldung: Any, server: Server) -> dict[str, str]:
    """Die Adressen der Seiten; dazu ein Vertrag im Entwurf mit begonnener Übergabe."""
    seiten = dict(server.adressen["seiten_stammdaten"])
    kontext = browser.new_context(base_url=server.basis_url, storage_state=str(anmeldung))
    seite = kontext.new_page()
    seite.set_default_timeout(15_000)
    try:
        m = Messung(seite, Ergebnis(ablauf="apple-stammdaten", titel="Vorbereitung"))
        vertrag = uebergabe_ablauf.vorbereiten(m, EINHEIT_ENTWURF, server.adressen["vertrag_neu"])
        seiten["vertrag_entwurf"] = vertrag.removeprefix(server.basis_url)
        seite.goto(vertrag)
        seite.click(uebergabe_ablauf.SELEKTOREN["starten"])
        seite.wait_for_url(re.compile(r"/schritt/1/"))
        erster = seite.url.removeprefix(server.basis_url)
    finally:
        kontext.close()
    for n in range(1, UEBERGABE_SCHRITTE + 1):
        seiten[f"uebergabe_{n}"] = erster.replace("/schritt/1/", f"/schritt/{n}/")
    return seiten


def _kontext(browser: Browser, anmeldung: Any, server: Server, viewport: Any, schema: str) -> Any:
    return browser.new_context(
        base_url=server.basis_url,
        storage_state=str(anmeldung),
        viewport=viewport,
        color_scheme=schema,  # type: ignore[arg-type]
        locale="en-US",
        timezone_id="Europe/Berlin",
    )


def _laden(seite: Page, pfad: str, schluessel: str) -> None:
    antwort = seite.goto(pfad, wait_until="networkidle")
    assert antwort is not None and antwort.status == 200, f"{schluessel}: {pfad}"
    if schluessel.startswith("uebergabe_"):
        seite.evaluate("window.scrollTo(0, document.body.scrollHeight)")


@pytest.mark.parametrize("schema", SCHEMAS)
@pytest.mark.parametrize("breite", list(BREITEN))
def test_apple_stammdaten(
    browser: Browser,
    anmeldung: Any,
    server: Server,
    stammdaten_seiten: dict[str, str],
    breite: str,
    schema: str,
) -> None:
    """Apple-Design (PR C): Jede Seite der Stammdaten und jeder Schritt der Übergabe bei 390
    und 1440 px, hell und dunkel, ohne Befund des Smoke-Tests und mit Klickflächen von
    mindestens 44 px."""
    kontext = _kontext(browser, anmeldung, server, BREITEN[breite], schema)
    seite = kontext.new_page()
    seite.set_default_timeout(15_000)
    alle: list[str] = []
    try:
        for schluessel in (*SEITEN, "vertrag_entwurf", *ASSISTENT):
            if schluessel not in stammdaten_seiten:
                continue
            _laden(seite, stammdaten_seiten[schluessel], schluessel)
            ziel = ORDNER / breite / f"{schluessel}-{schema}.png"
            ziel.parent.mkdir(parents=True, exist_ok=True)
            seite.screenshot(path=ziel, full_page=True)
            befunde = pruefen(seite, handy=breite == "390")
            if klein := _kleine_ziele(seite):
                befunde.append(f"Klickflächen unter {ZIEL_PX} px: {', '.join(klein[:6])}")
            alle += [f"{schluessel}: {b}" for b in befunde]
    finally:
        kontext.close()
    assert not alle, f"Stammdaten bei {breite} px ({schema}):\n- " + "\n- ".join(alle)


#: Breite Schrift wie unter Linux (Pipeline): Verdana, wie ``test_seiten_r2_8.SCHMAL``.
VERDANA = "* { font-family: Verdana, sans-serif !important; }"


@pytest.mark.parametrize("schrift", ["system", "verdana"])
def test_apple_stammdaten_320(
    browser: Browser,
    anmeldung: Any,
    server: Server,
    stammdaten_seiten: dict[str, str],
    schrift: str,
) -> None:
    """Apple-Design (PR C): Bei 320 px ragt auf keiner Seite der Stammdaten etwas über den
    Rand, und in Formularen überlappt nichts; auch mit breiter Schrift (Verdana), die die
    Schriften unter Linux nachstellt (Knopf „Annehmen und Vertrag anlegen“, Kartenlink „im
    Kündigungsdialog des Vertrags öffnen“)."""
    kontext = _kontext(browser, anmeldung, server, SCHMAL, "light")
    seite = kontext.new_page()
    seite.set_default_timeout(15_000)
    alle: list[str] = []
    try:
        for schluessel in (*SEITEN, "vertrag_entwurf", *ASSISTENT):
            if schluessel not in stammdaten_seiten:
                continue
            _laden(seite, stammdaten_seiten[schluessel], schluessel)
            if schrift == "verdana":
                seite.add_style_tag(content=VERDANA)
            alle += [f"{schluessel}: {b}" for b in pruefen(seite)]
    finally:
        kontext.close()
    assert not alle, f"Stammdaten bei 320 px ({schrift}):\n- " + "\n- ".join(alle)


def test_vertrag_eine_hauptaktion(
    seite: Page, stammdaten_seiten: dict[str, str], server: Server
) -> None:
    """Apple-Design (PR C): Am Vertrag steht im Kopf genau eine Hauptaktion, alles Weitere
    unter „mehr ▾“. Im Entwurf ist es „Übergabe am Handy“, am laufenden Vertrag (PR H)
    „Rückgabe am Handy“; „Schreiben erstellen“ und „Kündigung erfassen“ stehen dort im
    Menü."""
    kopf = "main #vertrag-kopf .seitenkopf-aktionen"
    for schluessel, haupt in (
        ("vertrag_entwurf", "Übergabe am Handy"),
        ("vertrag", "Rückgabe am Handy"),
    ):
        seite.goto(stammdaten_seiten[schluessel], wait_until="networkidle")
        sichtbar = [
            t.strip()
            for t in seite.locator(f"{kopf} > * .knopf, {kopf} > .knopf").all_inner_texts()
        ]
        assert sichtbar == [haupt, "mehr ▾"], (schluessel, sichtbar)
        assert seite.locator(f"{kopf} .knopf-primaer").count() == 1
    menue = seite.locator(f"{kopf} .menue-liste")
    for eintrag in ("Miete ändern", "Kündigung erfassen", "Schreiben erstellen"):
        assert menue.get_by_text(eintrag).count() == 1, eintrag
    assert menue.get_by_text("Rückgabe am Handy").count() == 0
