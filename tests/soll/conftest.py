"""Fixtures der Sollstellung: erfundene Verträge über die Services, „heute“ fest."""

from __future__ import annotations

import datetime
from collections.abc import Callable
from decimal import Decimal

import pytest

from apps.kern.models import Objekt
from apps.soll import services as soll
from apps.soll.models import Sollposten
from apps.stammdaten import services
from apps.stammdaten.models import Einheit, Partei, Vertrag
from tests.factories import ObjektFabrik
from tests.stammdaten.conftest import garage_anlegen, mieter_anlegen

D = datetime.date
#: Fester Tag „heute“ der Sollstellung in diesen Tests.
HEUTE = D(2026, 11, 20)
BEGINN = D(2026, 1, 1)


@pytest.fixture(autouse=True)
def _heute(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(soll, "heute", lambda: HEUTE)


@pytest.fixture
def objekt(db: None) -> Objekt:
    return ObjektFabrik(kuerzel="HOF")  # type: ignore[no-any-return]


@pytest.fixture
def mieter(db: None) -> Partei:
    return mieter_anlegen()


VertragBauer = Callable[..., Vertrag]


@pytest.fixture
def neuer_vertrag(objekt: Objekt, mieter: Partei) -> VertragBauer:
    """Legt einen aktiven Garagenvertrag an: ``neuer_vertrag(beginn, miete, **kw)``."""

    def bauen(
        beginn: datetime.date = BEGINN,
        miete: str = "85.00",
        *,
        einheit: Einheit | None = None,
        status: str = Vertrag.Status.AKTIV,
        **kw: object,
    ) -> Vertrag:
        einheit = einheit or garage_anlegen(objekt, frei_ab=D(2019, 1, 1))
        return services.vertrag_anlegen(
            einheiten=[einheit],
            mieter=[mieter],
            beginn=beginn,
            miete=Decimal(miete),
            kaution=Decimal("0.00"),
            status=status,
            **kw,  # type: ignore[arg-type]
        )

    return bauen


def geltend(vertrag: Vertrag, art: str = Sollposten.Art.MIETE) -> list[Sollposten]:
    return list(
        Sollposten.objects.filter(vertrag=vertrag, art=art)
        .exclude(status=Sollposten.Status.STORNIERT)
        .order_by("von")
    )
