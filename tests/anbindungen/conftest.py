"""Fixtures der Anbindungen: vermarktete Einheiten, Token der Website, Rückgabe ohne Schreiben."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.test import Client

from apps.anbindungen import services
from apps.kern.models import Objekt
from apps.stammdaten import schreiben
from apps.stammdaten import services as stammdaten
from apps.stammdaten.models import Einheit, EinheitStatus, Uebergabe, Vertrag
from tests.factories import ObjektFabrik
from tests.stammdaten.conftest import mieter_anlegen

D = datetime.date
BASIS = D(2026, 1, 1)
VERTRAGSBEGINN = D(2026, 3, 1)


@pytest.fixture
def objekt(db: None) -> Objekt:
    return ObjektFabrik(kuerzel="HOF")  # type: ignore[no-any-return]


def vermarktete_einheit(
    objekt: Objekt,
    typ: str = Einheit.Typ.GARAGE,
    *,
    status: str | None = EinheitStatus.Status.FREI,
    zielmiete: str | None = "89.00",
    ab: datetime.date = BASIS,
    vermarktung: str = Einheit.Vermarktung.WEBSITE,
) -> Einheit:
    return stammdaten.einheit_anlegen(
        objekt,
        typ,
        bezeichnung=f"{typ} Hofseite",
        flaeche_qm=Decimal("18.50"),
        vermarktung=vermarktung,
        status=status,
        status_ab=ab if status else None,
        zielmiete=Decimal(zielmiete) if zielmiete else None,
        zielmiete_ab=ab if zielmiete else None,
        zielmiete_begruendung="Marktvergleich",
    )


@pytest.fixture
def freie_garage(objekt: Objekt) -> Einheit:
    return vermarktete_einheit(objekt)


def aktiver_vertrag(einheit: Einheit, beginn: datetime.date = VERTRAGSBEGINN) -> Vertrag:
    return stammdaten.vertrag_anlegen(
        einheiten=[einheit],
        mieter=[mieter_anlegen("Testmieter")],
        beginn=beginn,
        miete=Decimal("85.00"),
        kaution=Decimal("0.00"),
        status=Vertrag.Status.AKTIV,
    )


def rueckgabe(vertrag: Vertrag, datum: datetime.date) -> Uebergabe:
    """Rückgabe über die Services aus Schritt 4 (ohne PDF-Protokoll)."""
    with schreiben.ohne_schreiben("Test ohne PDF"):
        uebergabe = stammdaten.uebergabe_beginnen(vertrag, Uebergabe.Art.RUECKGABE, datum)
        stammdaten.uebergabe_speichern(uebergabe, zustand="besenrein")
        return stammdaten.uebergabe_abschliessen(uebergabe)


@pytest.fixture
def token(db: None) -> str:
    return services.website_token_erzeugen("Website-Test")


@pytest.fixture
def website(client: Client, token: str) -> Client:
    """Ein Client, wie die Website ihn nutzt: ohne Sitzung, mit Token."""
    client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    return client
