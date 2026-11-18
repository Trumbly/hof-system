"""Startdaten der Stufenfolge nach der SOP (H4.1 bis H4.3), je Vertragsart gleich.

- Zahlungserinnerung fünf Tage nach Fälligkeit, per Mail.
- Mahnung nach 14 Tagen mit zehn Tagen Frist, per Mail; belegt erst mit Zustellnachweis.
- Abmahnung mit Kündigungsandrohung bei der zweiten offenen Monatsmiete, als Brief.
- Fristlose Kündigung, hilfsweise ordentlich, ab zwei Monatsmieten Rückstand, als Brief.

Abmahnung und Kündigung druckt der Benutzer, unterschreibt sie und wirft sie als
Einschreiben ein (Prozessliste, Entscheidung P2): Versandweg „selbst“. Die Datenmigration
``0002_startdaten`` und ``services.startdaten_anlegen`` legen sie an; geändert wird in der
Oberfläche (Seite „Stufen“). Nur Daten, keine Modelle: Die Migration nutzt diese Datei mit
dem historischen Modell.

Annahmen (nicht aus der SOP): Die Erinnerung setzt eine Woche Zahlungsziel; die Abmahnung
zwei Wochen; die Kündigung nennt zwei Wochen Räumungsfrist. Die Mahnung der Kündigung
nennt den Namen „Fristlose Kündigung“, damit nicht die Vorlage der ordentlichen Kündigung
(H4.6) greift.
"""

from __future__ import annotations

from typing import Any

#: Vertragsarten (``Vertrag.Art``) in der Reihenfolge der Stammdaten.
VERTRAGSARTEN = ("G", "S", "L", "H", "GA", "A", "X")

#: Je Stufe die Felder des Modells ``Mahnstufe`` (ohne Vertragsart).
STUFEN: tuple[dict[str, Any], ...] = (
    {
        "reihenfolge": 1,
        "schluessel": "erinnerung",
        "titel": "Zahlungserinnerung",
        "ausloeser": "tage_nach_faelligkeit",
        "ausloeser_wert": 5,
        "frist_tage": 7,
        "vorlagenart": "zahlungserinnerung",
        "vorlage_name": "",
        "versandweg": "mail",
        "einseitige_erklaerung": False,
    },
    {
        "reihenfolge": 2,
        "schluessel": "mahnung",
        "titel": "Mahnung mit Fristsetzung",
        "ausloeser": "tage_nach_faelligkeit",
        "ausloeser_wert": 14,
        "frist_tage": 10,
        "vorlagenart": "mahnung",
        "vorlage_name": "",
        "versandweg": "mail",
        "einseitige_erklaerung": True,
    },
    {
        "reihenfolge": 3,
        "schluessel": "abmahnung",
        "titel": "Abmahnung mit Kündigungsandrohung",
        "ausloeser": "offene_monatsmieten",
        "ausloeser_wert": 2,
        "frist_tage": 14,
        "vorlagenart": "abmahnung",
        "vorlage_name": "",
        "versandweg": "selbst",
        "einseitige_erklaerung": True,
    },
    {
        "reihenfolge": 4,
        "schluessel": "kuendigung",
        "titel": "Fristlose Kündigung, hilfsweise ordentlich",
        "ausloeser": "rueckstand_monatsmieten",
        "ausloeser_wert": 2,
        "frist_tage": 14,
        "vorlagenart": "kuendigung",
        "vorlage_name": "Fristlose Kündigung",
        "versandweg": "selbst",
        "einseitige_erklaerung": True,
    },
)


def fehlende(vorhandene_arten: set[str]) -> list[dict[str, Any]]:
    """Felder der Stufen für jede Vertragsart, die noch keine Stufe hat."""
    return [
        {"vertragsart": art, **stufe}
        for art in VERTRAGSARTEN
        if art not in vorhandene_arten
        for stufe in STUFEN
    ]
