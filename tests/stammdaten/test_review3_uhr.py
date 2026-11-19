"""Review 3, Punkt 1: Vorbelegungen rechnen mit der einen Uhr (``kern.zeit``)."""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.stammdaten.models import Uebergabe, Vertrag
from testhof.aufbau import STICHTAG

pytestmark = pytest.mark.django_db
HTMX = {"HTTP_HX_REQUEST": "true"}


def test_kuendigung_zugang_vorbelegt_mit_stichtag(
    aktiver_vertrag: Vertrag, angemeldet: Client
) -> None:
    """Review 3, Punkt 1: Der Zugang der Kündigung ist mit dem Stichtag vorbelegt, nicht mit
    dem echten Datum."""
    url = reverse("stammdaten:vertrag", args=[aktiver_vertrag.pk])
    html = angemeldet.get(f"{url}?dialog=kuendigung", **HTMX).content.decode()
    feld = html.split('name="kuendigung-zugang_am"', 1)[1].split(">", 1)[0]
    assert f'value="{STICHTAG.isoformat()}"' in feld


def test_uebergabe_datum_ist_stichtag(entwurf: Vertrag, angemeldet: Client) -> None:
    """Review 3, Punkt 1: Eine neu begonnene Übergabe trägt den Stichtag als Datum."""
    antwort = angemeldet.post(
        reverse("stammdaten:uebergabe_starten", args=[entwurf.pk, "uebergabe"])
    )
    assert antwort.status_code == 302
    uebergabe = Uebergabe.objects.get(vertrag=entwurf)
    assert uebergabe.datum == STICHTAG
