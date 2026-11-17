"""Hilfen für die durchgehenden Tests der Standardabläufe."""

from __future__ import annotations

from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.test import Client
from django.urls import reverse

from apps.kern.models import Arbeitsposten


def posten(art: str, bezug: models.Model, **filter_: Any) -> list[Arbeitsposten]:
    return list(
        Arbeitsposten.objects.filter(
            art=art,
            bezug_typ=ContentType.objects.get_for_model(bezug),
            bezug_id=bezug.pk,
            **filter_,
        )
    )


def schritt(client: Client, uebergabe_pk: int, nummer: int, daten: dict[str, Any]) -> None:
    """Sendet einen Schritt der Übergabe am Handy und erwartet die Weiterleitung."""
    url = reverse("stammdaten:uebergabe", args=[uebergabe_pk, nummer])
    antwort = client.post(url, daten)
    assert antwort.status_code == 302, antwort.content.decode()[:500]
