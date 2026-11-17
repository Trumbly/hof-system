"""Fristende einer Kündigung nach den Regeln aus konfiguration.toml (H2.8)."""

from __future__ import annotations

import datetime

import pytest

from apps.stammdaten import fristrechnung
from apps.stammdaten.konfiguration import kuendigungsregel

D = datetime.date


@pytest.mark.parametrize(
    ("jahr", "ostern"), [(2024, D(2024, 3, 31)), (2026, D(2026, 4, 5)), (2027, D(2027, 3, 28))]
)
def test_ostersonntag(jahr: int, ostern: datetime.date) -> None:
    assert fristrechnung.ostersonntag(jahr) == ostern


def test_werktage_ohne_samstag_und_feiertage() -> None:
    assert fristrechnung.ist_werktag(D(2026, 11, 2))  # Montag
    assert not fristrechnung.ist_werktag(D(2026, 11, 7))  # Samstag
    assert not fristrechnung.ist_werktag(D(2026, 10, 3))  # Tag der Deutschen Einheit
    assert not fristrechnung.ist_werktag(D(2026, 4, 6))  # Ostermontag
    # Dritter Werktag im Januar 2027: 01.01. Feiertag, 02./03. Wochenende
    assert fristrechnung.nter_werktag(D(2027, 1, 1), 3) == D(2027, 1, 6)


@pytest.mark.parametrize(
    ("zugang", "ende"),
    [
        (D(2026, 11, 2), D(2027, 1, 31)),  # bis 3. Werktag (04.11.): übernächster Monat
        (D(2026, 11, 4), D(2027, 1, 31)),
        (D(2026, 11, 5), D(2027, 2, 28)),  # zu spät: ein Monat mehr
        (D(2027, 1, 6), D(2027, 3, 31)),  # Feiertag und Wochenende schieben die Karenz
        (D(2026, 7, 2), D(2026, 9, 30)),
    ],
)
def test_garage_monat_580a(zugang: datetime.date, ende: datetime.date) -> None:
    """Schritt 4, Aufgabe 5: Fristende nach Vertragsart; Garage: spätestens am dritten
    Werktag eines Monats zum Ablauf des übernächsten Monats."""
    assert fristrechnung.fristende(zugang, kuendigungsregel("monat_580a")) == ende


@pytest.mark.parametrize(
    ("zugang", "ende"),
    [
        (D(2026, 10, 5), D(2027, 3, 31)),  # 3. Werktag im Q4 ist der 05.10.
        (D(2026, 10, 6), D(2027, 6, 30)),
        (D(2026, 12, 15), D(2027, 6, 30)),
    ],
)
def test_halle_quartal_580a(zugang: datetime.date, ende: datetime.date) -> None:
    """Schritt 4, Aufgabe 5: Halle (Geschäftsraum): dritter Werktag des Quartals zum Ablauf
    des nächsten Quartals."""
    assert fristrechnung.fristende(zugang, kuendigungsregel("quartal_580a")) == ende


@pytest.mark.parametrize(
    ("zugang", "ende"),
    [
        (D(2026, 3, 1), D(2026, 4, 30)),
        (D(2026, 3, 15), D(2026, 4, 30)),
        (D(2026, 3, 31), D(2026, 4, 30)),
        (D(2026, 12, 20), D(2027, 1, 31)),
    ],
)
def test_ein_monat_zum_monatsende(zugang: datetime.date, ende: datetime.date) -> None:
    assert fristrechnung.fristende(zugang, kuendigungsregel("ein_monat_monatsende")) == ende


def test_monate_addieren_kuerzt_auf_monatsende() -> None:
    assert fristrechnung.monate_addieren(D(2026, 1, 31), 1) == D(2026, 2, 28)
    assert fristrechnung.monate_addieren(D(2026, 9, 30), 6) == D(2027, 3, 30)
    assert fristrechnung.periodenende(D(2026, 8, 3), "quartal") == D(2026, 9, 30)
