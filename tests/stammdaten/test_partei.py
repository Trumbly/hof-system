"""Partei mit Kontaktwegen (H1.2); Personenfelder sind registriert."""

from __future__ import annotations

import logging

import pytest

from apps.kern import personenfelder
from apps.kern.personenfelder import ERSATZ, PersonenfelderFilter
from apps.stammdaten import services
from apps.stammdaten.models import (
    Kaution,
    Kontaktweg,
    Partei,
    Schluessel,
    Uebergabe,
    UebergabeFoto,
    Vertrag,
)

pytestmark = pytest.mark.django_db


def test_partei_und_kontaktwege_sind_personenfelder() -> None:
    """Schritt 4, Aufgabe 1 (CLAUDE.md, Regel 4.6): Name, Anschrift und Kontaktwege einer
    Partei sind als Personenfelder registriert, ebenso Freitexte über Personen."""
    assert {
        "anrede",
        "vorname",
        "nachname",
        "firma",
        "adresszusatz",
        "strasse",
        "plz",
        "ort",
        "notiz",
    } <= personenfelder.personenfelder(Partei)
    assert personenfelder.personenfelder(Kontaktweg) == {"wert"}
    assert "sonderabreden" in personenfelder.personenfelder(Vertrag)
    assert "abrechnung_notiz" in personenfelder.personenfelder(Kaution)
    assert "inhaber_text" in personenfelder.personenfelder(Schluessel)
    assert {"zustand", "zurueckgelassen"} <= personenfelder.personenfelder(Uebergabe)
    assert "beschreibung" in personenfelder.personenfelder(UebergabeFoto)


def test_str_und_logs_ohne_namen(caplog: pytest.LogCaptureFixture) -> None:
    """Fehlermeldungen und Logs nennen IDs, keine Namen."""
    partei = services.partei_anlegen(vorname="Hanna", nachname="Vogelsang", ort="Testdorf")
    assert str(partei) == f"Partei #{partei.pk}"

    filter_ = PersonenfelderFilter()
    record = logging.LogRecord(
        "t", logging.INFO, "", 0, "Partei %s in %s", (partei, partei.ort), None
    )
    filter_.filter(record)
    assert "Testdorf" not in record.getMessage()
    assert ERSATZ in record.getMessage()


def test_partei_braucht_namen() -> None:
    with pytest.raises(services.StammdatenFehler):
        services.partei_anlegen(Partei.Art.PERSON)
    with pytest.raises(services.StammdatenFehler):
        services.partei_anlegen(Partei.Art.FIRMA, nachname="Ohne Firma")
    firma = services.partei_anlegen(Partei.Art.FIRMA, firma="Beispiel GmbH")
    assert firma.anzeigename == "Beispiel GmbH"


def test_kontaktwege(mieter: Partei) -> None:
    services.kontaktweg_hinzufuegen(
        mieter, Kontaktweg.Art.EMAIL, "neu@testhof.invalid", bevorzugt=True
    )
    bevorzugt = mieter.kontaktwege.filter(bevorzugt=True)
    assert [k.wert for k in bevorzugt] == ["neu@testhof.invalid"]
    with pytest.raises(services.StammdatenFehler, match=f"Partei #{mieter.pk}"):
        services.kontaktweg_hinzufuegen(mieter, Kontaktweg.Art.EMAIL, "keine-adresse")
    with pytest.raises(services.StammdatenFehler):
        services.kontaktweg_hinzufuegen(mieter, Kontaktweg.Art.TELEFON, "  ")
    services.kontaktweg_entfernen(bevorzugt.get())
    assert mieter.kontaktwege.count() == 1


def test_unbekanntes_feld_wird_abgelehnt(mieter: Partei) -> None:
    with pytest.raises(services.StammdatenFehler):
        services.partei_aendern(mieter, iban="DE00")
