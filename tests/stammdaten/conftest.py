"""Fixtures der Stammdaten-Tests: erfundene Einheiten, Parteien und Verträge über die Services."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.kern.models import Objekt
from apps.stammdaten import services
from apps.stammdaten.models import Einheit, EinheitStatus, Kontaktweg, Partei, Vertrag
from tests.factories import ObjektFabrik

BASIS = datetime.date(2026, 1, 1)

#: Kleinstes gültiges PNG (1×1 Pixel), als Foto in Tests.
PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00"
    b"\x00\x00\x00IEND\xaeB`\x82"
)


def foto(name: str = "foto.png") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, PNG, content_type="image/png")


@pytest.fixture
def objekt(db: None) -> Objekt:
    return ObjektFabrik(kuerzel="HOF")  # type: ignore[no-any-return]


def garage_anlegen(
    objekt: Objekt, *, zielmiete: str = "90.00", frei_ab: datetime.date = BASIS
) -> Einheit:
    return services.einheit_anlegen(
        objekt,
        Einheit.Typ.GARAGE,
        bezeichnung="Garage Test",
        flaeche_qm=Decimal("18.00"),
        status=EinheitStatus.Status.FREI,
        status_ab=frei_ab,
        zielmiete=Decimal(zielmiete),
        zielmiete_ab=frei_ab,
        zielmiete_begruendung="Marktvergleich",
    )


@pytest.fixture
def garage(objekt: Objekt) -> Einheit:
    return garage_anlegen(objekt)


def mieter_anlegen(nachname: str = "Beispiel") -> Partei:
    partei = services.partei_anlegen(
        Partei.Art.PERSON,
        vorname="Erika",
        nachname=nachname,
        strasse="Testweg 1",
        plz="12345",
        ort="Beispielstadt",
    )
    services.kontaktweg_hinzufuegen(
        partei, Kontaktweg.Art.EMAIL, f"{nachname.lower()}@testhof.invalid", bevorzugt=True
    )
    return partei


@pytest.fixture
def mieter(db: None) -> Partei:
    return mieter_anlegen()


def vertrag_anlegen(
    einheit: Einheit,
    mieter: Partei,
    *,
    beginn: datetime.date = BASIS,
    status: str = Vertrag.Status.ENTWURF,
    kaution: str = "180.00",
) -> Vertrag:
    return services.vertrag_anlegen(
        einheiten=[einheit],
        mieter=[mieter],
        beginn=beginn,
        miete=Decimal("85.00"),
        kaution=Decimal(kaution),
        status=status,
    )


@pytest.fixture
def entwurf(garage: Einheit, mieter: Partei) -> Vertrag:
    return vertrag_anlegen(garage, mieter, beginn=datetime.date(2026, 12, 1))


@pytest.fixture
def aktiver_vertrag(garage: Einheit, mieter: Partei) -> Vertrag:
    vertrag = vertrag_anlegen(garage, mieter, beginn=datetime.date(2026, 2, 1), status="aktiv")
    services.kaution_eingang_erfassen(
        vertrag.kaution, datetime.date(2026, 1, 25), "Vivid Kautionen"
    )
    return vertrag
