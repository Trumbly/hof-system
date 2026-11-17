"""Erfundene IBANs, Telefonnummern und E-Mails (Schritt 0c, Aufgabe 5)."""

from __future__ import annotations

import random

import pytest

from testhof import erfunden


def test_iban_pruefziffer_bekanntes_beispiel() -> None:
    """Die Prüfziffer stimmt mit dem Lehrbuchbeispiel der Deutschen Bundesbank überein."""
    assert erfunden.iban_pruefziffer("DE", "370400440532013000") == "89"
    assert erfunden.ist_gueltige_iban("DE89 3704 0044 0532 0130 00")
    assert not erfunden.ist_gueltige_iban("DE88370400440532013000")
    assert not erfunden.ist_gueltige_iban("keine IBAN")


def test_erfundene_iban_ist_gueltig_und_mit_erfundener_blz() -> None:
    """Schritt 0c, Aufgabe 5: Alle IBANs sind gültig berechnete Test-IBANs mit erfundener
    Bankleitzahl."""
    zufall = random.Random(42)
    for _ in range(200):
        iban = erfunden.erfundene_iban(zufall)
        assert len(iban) == 22
        assert erfunden.ist_gueltige_iban(iban)
        assert erfunden.ist_erfundene_iban(iban)
        assert iban[4] == "0"  # echte Bankleitzahlen beginnen mit 1 bis 8


def test_echte_bankleitzahl_ist_keine_test_iban() -> None:
    assert not erfunden.ist_erfundene_iban("DE89370400440532013000")
    with pytest.raises(ValueError):
        erfunden.erfundene_iban(random.Random(1), blz="37040044")


def test_erfundene_iban_mit_fester_blz() -> None:
    iban = erfunden.erfundene_iban(random.Random(1), blz="01234567")
    assert iban[4:12] == "01234567"
    assert erfunden.ist_erfundene_iban(iban)


def test_gleicher_seed_gleiche_werte() -> None:
    """Schritt 0c, Aufgabe 1: Gleicher Seed ergibt dieselben Werte."""

    def werte(seed: int) -> list[str]:
        zufall = random.Random(seed)
        return [
            erfunden.erfundene_iban(zufall),
            erfunden.telefon(zufall),
            erfunden.telefon(zufall, mobil=True),
            erfunden.email(zufall, "Jürgen", "Weiß"),
        ]

    assert werte(42) == werte(42)
    assert werte(42) != werte(43)


def test_telefonnummern_aus_dem_fiktiven_bereich() -> None:
    """Schritt 0c, Aufgabe 5: Alle Telefonnummern aus dem fiktiven Bereich der
    Bundesnetzagentur."""
    zufall = random.Random(7)
    for _ in range(200):
        festnetz = erfunden.telefon(zufall)
        mobil = erfunden.telefon(zufall, mobil=True)
        assert erfunden.ist_fiktive_telefonnummer(festnetz), festnetz
        assert erfunden.ist_fiktive_telefonnummer(mobil), mobil
        assert not festnetz.startswith("01")
        assert mobil.startswith("01")


@pytest.mark.parametrize(
    ("nummer", "fiktiv"),
    [
        ("030 23125 000", True),
        ("+49 89 99998 999", True),
        ("0152 28817386", True),
        ("0176 040690 17", True),
        ("030 23126 000", False),
        ("030 23125 0000", False),
        ("0152 28817387", False),
    ],
)
def test_fiktive_telefonnummer_erkennen(nummer: str, fiktiv: bool) -> None:
    assert erfunden.ist_fiktive_telefonnummer(nummer) is fiktiv


def test_email_endet_auf_invalid() -> None:
    """Schritt 0c, Aufgabe 5: Alle E-Mail-Adressen enden auf .invalid."""
    zufall = random.Random(3)
    adresse = erfunden.email(zufall, "Jürgen", "Weiß-Öztürk", domain="hofpost")
    assert adresse == "juergen.weiss.oeztuerk@hofpost.invalid"
    for _ in range(50):
        assert erfunden.ist_invalid_mail(erfunden.email(zufall, "Erika", "Musterfrau"))
    assert not erfunden.ist_invalid_mail("erika@beispiel.de")
