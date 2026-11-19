"""Beleg erfassen: der offene Beleg aus dem Rechnungspostfach (Arbeitsposten
„Belege zuordnen“).

Weg seit R2-8 (Review 2, B9): Arbeitslisten → „Zuordnen“ → Seite des Belegs (PDF links,
Formular rechts). Datum, Betrag, Rechnungsnummer und Lieferant stehen aus der Textschicht
des PDFs im Formular, Objekt und Kostenart aus dem letzten Beleg des Lieferanten; die passende
Abbuchung ist vorgewählt → „Bestätigen“. Die Werte, die ein Mensch vom PDF abliest, liefert
``tests/e2e/adressen.py`` (eigene Muster, nicht die Erkennung der Anwendung).

Dazu ``hochladen``: ein neuer Beleg eines neuen Lieferanten über „Beleg hochladen“ in der
Ablage (Datei wählen genügt, die Seite des Belegs öffnet sich vorausgefüllt).
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from testhof.pdfs import rechnung_pdf
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
    "rueckmeldung": "#toasts",
    # Hochladen: „Datei wählen“ (nicht „Foto aufnehmen“) in der Karte „Beleg hochladen“
    "datei": "form[action$='/belege/neu/'] input[type=file][name=datei]:not([capture])",
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
    assert "bestätigt" in m.text(s["rueckmeldung"]), "Der Beleg ist nicht bestätigt."


def hochladen(m: Messung, adressen: dict[str, Any], ablage: Path) -> None:
    """Neuer Beleg über „Beleg hochladen“ (Ablage → Datei wählen → prüfen → Bestätigen)."""
    s = SELEKTOREN
    b = NEUER_BELEG
    pdf = ablage / "beleg-neu.pdf"
    pdf.write_bytes(
        rechnung_pdf(
            lieferant=b["lieferant"],
            nummer=b["rechnungsnummer"],
            datum=datetime.date.fromisoformat(b["datum"]),
            betrag=Decimal(b["betrag"].replace(",", ".")),
            leistung="Material (erfunden, Testhof)",
        )
    )
    m.beginnen(adressen["seiten"]["dokumente"])
    with m.seite.expect_navigation():
        m.datei(s["datei"], str(pdf))  # sendet sofort (Baustein dateiknoepfe, sofort)
    m.seite.wait_for_load_state("networkidle")
    m.tippe(s["lieferant_neu"], b["lieferant"])
    m.tippe(s["datum"], b["datum"])
    m.tippe(s["betrag"], b["betrag"])
    m.waehle(s["objekt"], b["objekt"])
    m.waehle(s["kategorie"], b["kategorie"])
    m.tippe(s["leistungsjahr"], b["leistungsjahr"], vorbelegt_reicht=True)
    m.tippe(s["rechnungsnummer"], b["rechnungsnummer"])
    m.klick(s["bestaetigen"])
    m.anhalten()
    assert "bestätigt" in m.text(s["rueckmeldung"]), m.seite.url
