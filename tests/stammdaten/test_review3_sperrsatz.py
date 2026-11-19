"""Review 3, Punkt 6 zusammen mit Punkt 4: Der Hinweis „Senden ist gesperrt“ steht auch bei
einem Brief an eine Partei mit „Anschrift folgt“ genau einmal da (``sperrsatz``)."""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils.html import strip_tags

from apps.dokumente import services as dokumente
from apps.dokumente.models import Vorlage
from apps.dokumente.vorlagenarten import Versandweg
from apps.kern.models import Objekt
from apps.stammdaten import schreiben, services
from tests.stammdaten.conftest import garage_anlegen, vertrag_anlegen

pytestmark = pytest.mark.django_db


def _text(client: Client, pk: int) -> str:
    html = client.get(reverse("dokumente:schreiben", args=[pk])).content.decode()
    return " ".join(strip_tags(html).split())


def test_brief_anschrift_folgt_sperrsatz_einmal(
    objekt: Objekt, vorlagen: dict[str, Vorlage], angemeldet: Client, settings: Any
) -> None:
    """Review 3, Punkt 6: Auf der Seite eines Briefs an eine Partei mit „Anschrift folgt“
    steht „Senden ist gesperrt“ genau einmal, auch wenn dazu der Briefkopf fehlt."""
    partei = services.partei_mit_kontakt_anlegen(
        vorname="Erika", nachname="Beispiel", email="erika@testhof.invalid", anschrift_folgt=True
    )
    vertrag = vertrag_anlegen(garage_anlegen(objekt), partei, beginn=datetime.date(2026, 2, 1))
    kontext = schreiben.kontext_vertrag(vertrag)
    brief = dokumente.schreiben_erzeugen(vorlagen["mitteilung"], vertrag, kontext, Versandweg.BRIEF)

    text = _text(angemeldet, brief.pk)
    assert text.count("Senden ist gesperrt") == 1
    assert "gesperrt: Senden" not in text
    assert "Für den Versand als Brief fehlt die Anschrift des Empfängers" in text

    # Zwei Gründe zugleich (Anschrift und Briefkopf): weiterhin ein Anfang.
    settings.DOKUMENTE = {
        **settings.DOKUMENTE,
        "ABSENDER": {**settings.DOKUMENTE["ABSENDER"], "anschrift": ""},
    }
    text = _text(angemeldet, brief.pk)
    assert text.count("Senden ist gesperrt") == 1
    assert "Im Briefkopf fehlt" in text and "Anschrift des Empfängers" in text
