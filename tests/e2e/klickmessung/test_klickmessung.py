"""Klickmessung der Abläufe aus Review 2, B (``make klickmessung``).

Spielt jeden Ablauf so kurz wie möglich mit der Oberfläche durch und zählt Klicks,
Seitenwechsel und Tippfelder (Regeln in ``messung.py``). Je Ablauf ein Modul
``ablauf_*.py`` mit seinen Selektoren an einer Stelle (``SELEKTOREN``).

Ergebnis: ``tests/e2e/artefakte/klickmessung.md`` und ``.json``; die Werte trägt man in
``docs/klickmessung.md`` ein (Spalte „vorher“ bzw. „nachher“).

Ein Ablauf, den die Oberfläche nicht anbietet, endet als „nicht möglich“ (Test grün).
Verweigert die Anwendung einen Schritt (Fehlermeldung, Abschluss gesperrt), steht der Ablauf
als „gescheitert“ mit der Meldung im Ergebnis (Test „xfail“). Findet ein Selektor nichts oder
stürzt der Server ab, schlägt der Test fehl: Dann passt das Skript nicht mehr zur Oberfläche,
oder die Anwendung hat einen Fehler.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Page

from tests.e2e.conftest import HANDY
from tests.e2e.klickmessung import (
    ablauf_beleg_erfassen,
    ablauf_buchungsanfrage_vertrag,
    ablauf_klaerfall_zuordnen,
    ablauf_uebergabe_ohne_kaution,
    ablauf_umsatz_einordnen,
    ablauf_website_kuendigung,
)
from tests.e2e.klickmessung.messung import Ergebnis, Gescheitert, Messung, NichtMoeglich
from tests.e2e.server import ARTEFAKTE, WURZEL, Server

pytestmark = [pytest.mark.e2e, pytest.mark.klickmessung]

Lauf = Callable[[Messung, dict[str, Any], Path], None]

#: (Schlüssel, Titel, Lauf, Handy-Breite). Reihenfolge = Reihenfolge der Tabelle.
ABLAEUFE: tuple[tuple[str, str, Lauf, bool], ...] = (
    (
        ablauf_umsatz_einordnen.NAME,
        ablauf_umsatz_einordnen.TITEL,
        lambda m, a, _: ablauf_umsatz_einordnen.durchspielen(m, a),
        False,
    ),
    (
        ablauf_klaerfall_zuordnen.NAME,
        ablauf_klaerfall_zuordnen.TITEL,
        lambda m, a, _: ablauf_klaerfall_zuordnen.durchspielen(m, a),
        False,
    ),
    (
        ablauf_buchungsanfrage_vertrag.NAME,
        ablauf_buchungsanfrage_vertrag.TITEL,
        lambda m, a, _: ablauf_buchungsanfrage_vertrag.durchspielen(m, a),
        False,
    ),
    (
        ablauf_beleg_erfassen.NAME,
        ablauf_beleg_erfassen.TITEL,
        lambda m, a, _: ablauf_beleg_erfassen.durchspielen(m, a),
        False,
    ),
    (
        "beleg_hochladen",
        "Beleg erfassen (hochladen, neuer Lieferant)",
        ablauf_beleg_erfassen.hochladen,
        False,
    ),
    (
        ablauf_website_kuendigung.NAME,
        ablauf_website_kuendigung.TITEL,
        lambda m, a, _: ablauf_website_kuendigung.durchspielen(m, a),
        False,
    ),
    (
        "website_kuendigung_umweg",
        "Website-Kündigung, Umweg von Hand (ohne Verknüpfung)",
        lambda m, a, _: ablauf_website_kuendigung.umweg(m, a),
        False,
    ),
    (
        ablauf_uebergabe_ohne_kaution.NAME,
        ablauf_uebergabe_ohne_kaution.TITEL,
        ablauf_uebergabe_ohne_kaution.durchspielen,
        True,
    ),
    (
        "uebergabe_ohne_kaution_umweg",
        "Übergabe ohne Kaution, Umweg über die Vertragsseite (Kaution 0 €)",
        ablauf_uebergabe_ohne_kaution.umweg,
        True,
    ),
)

_ERGEBNISSE: dict[str, Ergebnis] = {}


def _stand() -> str:
    try:
        return subprocess.run(
            ["git", "describe", "--always", "--dirty"],  # noqa: S607
            cwd=WURZEL,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unbekannt"


def tabelle(ergebnisse: list[Ergebnis], stand: str) -> str:
    """Markdown-Tabelle „Ablauf · Klicks · Seitenwechsel · Tippfelder · Stand (Commit)“."""
    zeilen = [
        "| Ablauf | Klicks | Seitenwechsel | Tippfelder | Stand (Commit) |",
        "|---|---:|---:|---:|---|",
    ]
    hinweise = []
    for e in ergebnisse:
        if e.stand == "gemessen":
            werte = f"{e.klicks} | {e.seitenwechsel} | {e.tippfelder}"
        else:
            werte = f"{e.stand} | – | –"
        zeilen.append(f"| {e.titel} | {werte} | `{stand}` |")
        if e.hinweis:
            hinweise.append(f"- **{e.titel}** ({e.stand}): {e.hinweis}")
    return "\n".join(zeilen + (["", *hinweise] if hinweise else [])) + "\n"


@pytest.fixture(scope="module", autouse=True)
def _ergebnis_schreiben() -> Iterator[None]:
    _ERGEBNISSE.clear()
    yield
    ergebnisse = [_ERGEBNISSE[s] for s, *_ in ABLAEUFE if s in _ERGEBNISSE]
    stand = _stand()
    (ARTEFAKTE / "klickmessung.json").write_text(
        json.dumps(
            {"stand": stand, "ablaeufe": [e.als_dict() for e in ergebnisse]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (ARTEFAKTE / "klickmessung.md").write_text(tabelle(ergebnisse, stand), encoding="utf-8")


@pytest.mark.parametrize(
    ("schluessel", "titel", "lauf", "handy"), ABLAEUFE, ids=[a[0] for a in ABLAEUFE]
)
def test_ablauf(
    seite: Page,
    server: Server,
    adressen: dict[str, Any],
    tmp_path: Path,
    schluessel: str,
    titel: str,
    lauf: Lauf,
    handy: bool,
) -> None:
    """Review 2, B (Tabelle der Abläufe): Klicks, Seitenwechsel und Tippfelder messen."""
    if handy:
        seite.set_viewport_size(HANDY)
    ergebnis = Ergebnis(ablauf=schluessel, titel=titel)
    _ERGEBNISSE[schluessel] = ergebnis
    messung = Messung(seite, ergebnis)
    try:
        lauf(messung, adressen, tmp_path)
    except NichtMoeglich as grund:
        ergebnis.stand = "nicht möglich"
        ergebnis.hinweis = str(grund)
    except Exception as fehler:
        ergebnis.stand = "gescheitert"
        absturz = "" if server.laeuft() else " Der Server ist dabei abgestürzt."
        ergebnis.hinweis = (
            f"bei {seite.url.removeprefix(server.basis_url)}: {fehler}{absturz} "
            f"Bis dahin {ergebnis.klicks} Klicks, {ergebnis.seitenwechsel} Seitenwechsel, "
            f"{ergebnis.tippfelder} Tippfelder."
        )
        if server.laeuft():
            seite.screenshot(path=ARTEFAKTE / f"klickmessung-{schluessel}.png", full_page=True)
        if isinstance(fehler, Gescheitert) and server.laeuft():
            # Die Anwendung verweigert den Ablauf: gemessen und notiert, kein Fehler des Skripts.
            pytest.xfail(f"{titel}: gescheitert, {ergebnis.hinweis}")
        raise
    finally:
        ergebnis.schritte.append(f"Ende: {seite.url}")
