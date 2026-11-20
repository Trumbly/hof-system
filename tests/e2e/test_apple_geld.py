"""Apple-Design, Bereich Geld (PR D): Bank, Kassenbuch, Offene Posten, Abgleich, Mahnwesen.

Je Seite die Prüfung des Smoke-Tests (Serverfehler, Überlauf, englische Texte, Überlappung in
Formularen; am Handy Ziele mindestens 44 px) und zusätzlich die 44-px-Ziele der Bausteine aus
``test_design.py``:

- alle Seiten des Bereichs hell bei 1440, 390 und 320 px,
- die wichtigsten Seiten auch dunkel bei 1440 und 390 px,
- alle Seiten hell bei 360 und 390 px mit Verdana (``-breit``), als Ersatz für die breiteren
  Schriften der Pipeline unter Linux (wie ``test_seite_schmal``).

Die Adressen kommen aus ``tests/e2e/adressen_geld.py`` (im Server-Prozess, über die URL-Namen).
Screenshots unter ``tests/e2e/artefakte/geld/<breite>/``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from playwright.sync_api import Browser, Response

from tests.e2e import server as e2e_server
from tests.e2e.server import ARTEFAKTE, Server
from tests.e2e.test_design import _kleine_ziele
from tests.e2e.test_smoke import ZIEL_PX, pruefen

pytestmark = pytest.mark.e2e

ORDNER = ARTEFAKTE / "geld"
BREITEN = {
    "1440": {"width": 1440, "height": 900},
    "390": {"width": 390, "height": 844},
    "320": {"width": 320, "height": 700},
    "360-breit": {"width": 360, "height": 700},
    "390-breit": {"width": 390, "height": 844},
}
#: Breite Schrift für die Breiten mit „-breit“
VERDANA = "* { font-family: Verdana, sans-serif !important; }"

#: Alle Seiten des Bereichs (Schlüssel aus adressen_geld.sammeln).
SEITEN = (
    "bank_konten",
    "bank_umsaetze",
    "bank_import",
    "bank_freigabe",
    "kassenbuch",
    "kassenbuch_umsatz",
    "kassenbuch_zuordnen",
    "kassenbuch_aufteilen",
    "kassenbuch_regel_aus_umsatz",
    "kassenbuch_regeln",
    "kassenbuch_regel_neu",
    "kassenbuch_regel",
    "kassenbuch_regel_anwenden",
    "kassenbuch_abschluesse",
    "kassenbuch_bericht",
    "offene_posten",
    "soll_vertrag",
    "rueckstaende",
    "mietkonto",
    "handzahlung",
    "klaerfall",
    "klaerfall_zuordnen",
    "klaerfall_aufteilen",
    "klaerfall_regel",
    "zahlerregeln",
    "zahlerregel",
    "mahnwesen",
    "mahnfall",
    "mahnstufen",
    "mahnstufe",
    "sperren",
    "kuendigen",
)
#: Die wichtigsten Seiten, zusätzlich dunkel.
WICHTIG = (
    "bank_konten",
    "bank_umsaetze",
    "kassenbuch",
    "kassenbuch_umsatz",
    "kassenbuch_aufteilen",
    "kassenbuch_regeln",
    "kassenbuch_abschluesse",
    "offene_posten",
    "soll_vertrag",
    "rueckstaende",
    "mietkonto",
    "klaerfall",
    "mahnwesen",
    "mahnfall",
)

FAELLE = [(s, b, "light") for s in SEITEN for b in BREITEN] + [
    (s, b, "dark") for s in WICHTIG for b in ("1440", "390")
]


@pytest.fixture(scope="module")
def geld(server: Server) -> dict[str, str]:
    """Adressen der Geld-Seiten, gelesen im Server-Prozess wie ``adressen.py``."""
    ausgabe = e2e_server._manage(
        server.umgebung, "shell", "-c", "from tests.e2e.adressen_geld import ausgeben; ausgeben()"
    )
    for zeile in ausgabe.splitlines():
        if zeile.startswith("GELD "):
            return json.loads(zeile.removeprefix("GELD "))  # type: ignore[no-any-return]
    raise RuntimeError(f"Keine Adressen gelesen:\n{ausgabe}")


@pytest.mark.parametrize(
    ("schluessel", "breite", "schema"), FAELLE, ids=[f"{s}-{b}-{c}" for s, b, c in FAELLE]
)
def test_geld_seite(
    browser: Browser,
    anmeldung: Any,
    server: Server,
    geld: dict[str, str],
    schluessel: str,
    breite: str,
    schema: str,
) -> None:
    """Apple-Design, Bereich Geld: Seite ohne Serverfehler, ohne Überlauf und Überlappung,
    Klickflächen der Bausteine mindestens 44 px (am Handy alle Knöpfe und Haken)."""
    kontext = browser.new_context(
        base_url=server.basis_url,
        storage_state=str(anmeldung),
        viewport=BREITEN[breite],  # type: ignore[arg-type]
        color_scheme=schema,  # type: ignore[arg-type]
        locale="en-US",
        timezone_id="Europe/Berlin",
    )
    seite = kontext.new_page()
    seite.set_default_timeout(15_000)
    serverfehler: list[str] = []

    def _antwort(antwort: Response) -> None:
        if antwort.status >= 500 and antwort.url.startswith(server.basis_url):
            serverfehler.append(f"{antwort.status} bei {antwort.url}")

    seite.on("response", _antwort)
    try:
        antwort = seite.goto(geld[schluessel], wait_until="networkidle")
        assert antwort is not None and antwort.status == 200
        if breite.endswith("-breit"):
            seite.add_style_tag(content=VERDANA)
        endung = "hell" if schema == "light" else "dunkel"
        ziel = ORDNER / breite / f"{schluessel}-{endung}.png"
        ziel.parent.mkdir(parents=True, exist_ok=True)
        seite.screenshot(path=ziel, full_page=True)

        befunde = serverfehler + pruefen(seite, handy=breite != "1440")
        if klein := _kleine_ziele(seite):
            befunde.append(f"Klickflächen unter {ZIEL_PX} px: {', '.join(klein[:6])}")
        assert not befunde, f"{schluessel} bei {breite} px ({schema}):\n- " + "\n- ".join(befunde)
    finally:
        kontext.close()
