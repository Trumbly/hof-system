"""Fixtures der Zähler-Tests: erfundene Objekte, Einheiten und Zähler über die Services."""

from __future__ import annotations

import datetime

import pytest

from apps.kern.models import Objekt
from apps.stammdaten.models import Einheit
from apps.zaehler import services
from apps.zaehler.models import Zaehler
from tests.factories import ObjektFabrik
from tests.stammdaten.conftest import foto, garage_anlegen

__all__ = ["foto"]

D = datetime.date


@pytest.fixture
def objekt(db: None) -> Objekt:
    return ObjektFabrik(kuerzel="HOF")  # type: ignore[no-any-return]


@pytest.fixture
def garage(objekt: Objekt) -> Einheit:
    return garage_anlegen(objekt)


@pytest.fixture
def stromzaehler(garage: Einheit) -> Zaehler:
    """Stromzähler an der Garage, eingebaut am 01.03.2021 mit Stand 0, Eichfrist bis 2028."""
    return services.zaehler_anlegen(
        "TZ-S-1",
        Zaehler.Art.STROM,
        einheit=garage,
        bezeichnung="links neben dem Tor",
        einbaudatum=D(2021, 3, 1),
        eichfrist_bis=D(2028, 12, 31),
        einbau_stand="0",
    )
