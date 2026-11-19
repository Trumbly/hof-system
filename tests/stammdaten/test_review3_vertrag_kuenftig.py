"""Review 3, Punkt 5: Vertrag mit Beginn in der Zukunft (V-0046 im Testhof)."""

from __future__ import annotations

import datetime

import pytest
from django.test import Client
from django.urls import reverse
from django.utils.html import strip_tags

from apps.stammdaten.models import Einheit, Partei, Vertrag
from tests.stammdaten.conftest import vertrag_anlegen

pytestmark = pytest.mark.django_db

#: Weit genug in der Zukunft, dass der Test auch mit dem Stichtag des Testhofs gilt.
KUENFTIG = datetime.date(2031, 1, 1)


def _seite(client: Client, vertrag: Vertrag) -> str:
    html = client.get(reverse("stammdaten:vertrag", args=[vertrag.pk])).content.decode()
    return " ".join(strip_tags(html).split())


def test_kuenftiger_beginn_ab_statt_seit(
    garage: Einheit, mieter: Partei, angemeldet: Client
) -> None:
    """Review 3, Punkt 5: Beginnt der Vertrag erst, sagt der Kopf „ab 01.01.2031“ und
    „Miete ab Beginn“; die Kondition trägt kein Badge „heute“, sondern „ab Beginn“."""
    vertrag = vertrag_anlegen(garage, mieter, beginn=KUENFTIG)
    text = _seite(angemeldet, vertrag)
    assert "ab 01.01.2031" in text
    assert "seit 01.01.2031" not in text
    assert "Miete ab Beginn" in text
    assert "Miete heute" not in text
    assert "01.01.2031 ab Beginn" in text
    assert "01.01.2031 heute" not in text


def test_laufender_vertrag_bleibt_seit_und_heute(
    garage: Einheit, mieter: Partei, angemeldet: Client
) -> None:
    """Review 3, Punkt 5: Ein laufender Vertrag behält „seit“, „Miete heute“ und das
    Badge „heute“ an der geltenden Kondition."""
    vertrag = vertrag_anlegen(garage, mieter, beginn=datetime.date(2026, 2, 1), status="aktiv")
    text = _seite(angemeldet, vertrag)
    assert "seit 01.02.2026" in text
    assert "Miete heute" in text
    assert "01.02.2026 heute" in text
