"""Fixtures des Kassenbuchs: ein kleiner Hof über die Services, Umsätze über die Bank."""

from __future__ import annotations

import datetime
import random
from dataclasses import dataclass
from decimal import Decimal

import pytest

from apps.bank import services as bank_services
from apps.bank.models import Bankkonto, Bankumsatz
from apps.kassenbuch import services
from apps.kassenbuch.models import Kategorie
from apps.kern import services as kern_services
from apps.kern.models import Mandant, Objekt
from testhof import erfunden

_zufall = random.Random("tests-kassenbuch")  # noqa: S311  (erfundene Test-IBANs)


def neue_iban() -> str:
    return erfunden.erfundene_iban(_zufall)


@dataclass
class KleinerHof:
    gbr: Mandant
    kg: Mandant
    hof: Objekt
    halle: Objekt
    konto: Bankkonto
    miete: Kategorie
    strom: Kategorie
    entnahme_gbr: Kategorie
    entnahme_kg: Kategorie
    wohnraum: Kategorie


@pytest.fixture
def klein(db: None) -> KleinerHof:
    gbr = kern_services.mandant_anlegen(Mandant.Art.GBR, "GBR", "GbR Beispiel")
    kg = kern_services.mandant_anlegen(Mandant.Art.KG_LINIE, "KG", "KG-Linie Beispiel")
    hof = kern_services.objekt_anlegen(gbr, "HOF", "Hofanlage")
    halle = kern_services.objekt_anlegen(kg, "HALLE", "Halle")
    konto = bank_services.bankkonto_anlegen(
        gbr,
        bank="Vivid",
        bezeichnung="Vivid Test",
        iban=neue_iban(),
        zugang=Bankkonto.Zugang.ENABLE_BANKING,
    )
    return KleinerHof(
        gbr=gbr,
        kg=kg,
        hof=hof,
        halle=halle,
        konto=konto,
        miete=services.kategorie_anlegen("Mieteinnahmen Garagen", Kategorie.Art.EINNAHME),
        strom=services.kategorie_anlegen("Strom", Kategorie.Art.AUSGABE, umlagefaehiger_anteil=50),
        entnahme_gbr=services.kategorie_anlegen(
            "Entnahmen GBR", Kategorie.Art.ENTNAHME, mandant=gbr
        ),
        entnahme_kg=services.kategorie_anlegen("Entnahmen KG", Kategorie.Art.ENTNAHME, mandant=kg),
        wohnraum=services.kategorie_anlegen(
            "Mieteinnahmen Wohnraum", Kategorie.Art.EINNAHME, wohnraum=True
        ),
    )


def umsatz(
    konto: Bankkonto,
    datum: datetime.date,
    betrag: str,
    *,
    eingang: bool = True,
    iban: str = "DE40012345670000000001",
    name: str = "Erika Beispiel",
    zweck: str = "Miete",
) -> Bankumsatz:
    """Ein Umsatz über ``bank.services`` (erfundene Werte)."""
    ergebnis = bank_services.umsaetze_uebernehmen(
        konto,
        [
            bank_services.UmsatzDaten(
                buchungsdatum=datum,
                betrag=Decimal(betrag),
                richtung="eingang" if eingang else "ausgang",
                gegenpartei=name,
                gegen_iban=iban,
                verwendungszweck=zweck,
            )
        ],
    )
    assert len(ergebnis.umsaetze) == 1, "Umsatz doppelt: Zweck oder Datum ändern"
    return ergebnis.umsaetze[0]


def ganz(klein: KleinerHof, eintrag: Bankumsatz, kategorie: Kategorie | None = None) -> None:
    """Bucht einen Umsatz ganz auf HOF."""
    services.umsatz_zuordnen(eintrag, kategorie=kategorie or klein.miete, objekt=klein.hof)
