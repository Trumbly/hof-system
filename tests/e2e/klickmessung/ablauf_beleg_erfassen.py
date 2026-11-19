"""Beleg erfassen: der offene Beleg aus dem Rechnungspostfach (Arbeitsposten
„Belege zuordnen“).

Weg heute: Arbeitslisten → „Zuordnen“ → Seite des Belegs. Lieferant, Objekt und Kostenart
sind aus früheren Belegen vorbelegt; Belegdatum, Betrag und Rechnungsnummer liest man vom PDF
ab und tippt sie → „Bestätigen“. Die Werte vom PDF liefert ``tests/e2e/adressen.py``
(Textschicht des erfundenen PDFs).

Dazu ``hochladen``: ein neuer Beleg eines neuen Lieferanten über „Beleg hochladen“.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from testhof.pdfs import einfaches_pdf
from tests.e2e.klickmessung.messung import Messung

NAME = "beleg_erfassen"
TITEL = "Beleg erfassen (aus dem Rechnungspostfach)"

#: Alle Selektoren des Ablaufs. ``{posten}``: Arbeitsposten „Belege zuordnen“.
SELEKTOREN = {
    "zuordnen": "#posten-{posten} button:has-text('Zuordnen')",
    "lieferant": "select[name=lieferant]",
    "lieferant_neu": "input[name=lieferant_neu]",
    "datum": "input[name=datum]",
    "betrag": "input[name=betrag]",
    "objekt": "select[name=objekt]",
    "kategorie": "select[name=kategorie]",
    "leistungsjahr": "input[name=leistungsjahr]",
    "rechnungsnummer": "input[name=rechnungsnummer]",
    "bestaetigen": "main button:has-text('Bestätigen')",
    # Hochladen
    "hochladen": "main a:has-text('Beleg hochladen')",
    "datei": "input[type=file][name=datei]",
    "ablegen": "main button:has-text('Ablegen')",
}

#: Erfundener Beleg für das Hochladen (neuer Lieferant).
NEUER_BELEG = {
    "lieferant": "Baustoffe Beispiel GmbH",
    "datum": "2026-10-28",
    "betrag": "86,40",
    "rechnungsnummer": "BB-2026-117",
    "objekt": r"^HOF\b",
    "kategorie": r"^Material\b",
    "leistungsjahr": "2026",
}


def durchspielen(m: Messung, adressen: dict[str, Any]) -> None:
    s = {k: v.format(posten=adressen["posten"]["beleg"]) for k, v in SELEKTOREN.items()}
    angaben = adressen["beleg_angaben"]
    m.beginnen(adressen["arbeitslisten"])
    m.klick(s["zuordnen"])
    # Lieferant und Objekt: Vorschlag übernehmen. Die Kostenart muss zur Leistung auf dem
    # PDF passen; steht etwas anderes vorbelegt, muss man umwählen (Review 2, A6).
    m.waehle(s["kategorie"], rf"^{angaben['kostenart']}$")
    m.tippe(s["datum"], angaben["datum"])
    m.tippe(s["betrag"], angaben["betrag"])
    m.tippe(s["rechnungsnummer"], angaben["rechnungsnummer"])
    m.klick(s["bestaetigen"])
    m.anhalten()
    assert "bestätigt" in m.text(), "Der Beleg ist nicht bestätigt."


def hochladen(m: Messung, adressen: dict[str, Any], ablage: Path) -> None:
    """Neuer Beleg über „Beleg hochladen“ (Ablage → Beleg hochladen → Felder → Ablegen)."""
    s = SELEKTOREN
    b = NEUER_BELEG
    pdf = ablage / "beleg-neu.pdf"
    pdf.write_bytes(
        einfaches_pdf(
            [
                f"{b['lieferant']} (erfunden, Testhof)",
                f"Rechnungsnummer {b['rechnungsnummer']}",
                "Datum 28.10.2026",
                f"Betrag {b['betrag']} EUR",
            ]
        )
    )
    m.beginnen(adressen["seiten"]["dokumente"])
    m.klick(s["hochladen"])
    m.datei(s["datei"], str(pdf))
    m.tippe(s["lieferant_neu"], b["lieferant"])
    m.tippe(s["datum"], b["datum"])
    m.tippe(s["betrag"], b["betrag"])
    m.waehle(s["objekt"], b["objekt"])
    m.waehle(s["kategorie"], b["kategorie"])
    m.tippe(s["leistungsjahr"], b["leistungsjahr"], vorbelegt_reicht=True)
    m.tippe(s["rechnungsnummer"], b["rechnungsnummer"])
    m.klick(s["ablegen"])
    m.anhalten()
    assert "/dokumente/belege/" in m.seite.url or "Beleg" in m.text(), m.seite.url
