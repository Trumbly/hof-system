"""Echtes PDF mit WeasyPrint. Läuft in der Pipeline; unter macOS ohne Pango übersprungen."""

from __future__ import annotations

import datetime
import os

import pytest

from apps.kassenbuch import pdf, services
from tests.kassenbuch.conftest import KleinerHof, ganz, umsatz

#: In der Pipeline gesetzt: Dort darf der Test nicht still übersprungen werden.
PFLICHT = os.environ.get("HOF_PDF_PFLICHT") == "1"

pytestmark = pytest.mark.skipif(
    not pdf.verfuegbar() and not PFLICHT,
    reason=(
        f"PDF-Test übersprungen: {pdf.grund()} Unter macOS: `brew install pango` und "
        "`export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`. "
        "In der Pipeline (Ubuntu) und im Produktions-Image läuft er."
    ),
)


def test_monatsbericht_als_echtes_pdf(klein: KleinerHof) -> None:
    """Aufgabe 6: Der Monatsbericht je Objekt entsteht als PDF (WeasyPrint)."""
    eintrag = umsatz(klein.konto, datetime.date(2026, 1, 5), "250.00")
    ganz(klein, eintrag)

    bericht = services.monatsbericht_erzeugen(klein.hof, datetime.date(2026, 1, 1))

    assert bericht.datei.name.endswith("monatsbericht-HOF-2026-01.pdf")
    inhalt = bericht.datei.read()
    assert inhalt.startswith(b"%PDF-")
    assert len(inhalt) > 1000
