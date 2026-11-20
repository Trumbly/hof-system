"""Offene Posten in der neuen Gestaltung (PR R2-9): Filterleiste, Beträge über ``|euro``,
Stornieren, Fälligkeit und Posten von Hand als Dialoge mit Meldung und Link."""

from __future__ import annotations

import json

import pytest
from django.test import Client
from django.urls import reverse
from django.utils.html import strip_tags

from tests.soll.conftest import VertragBauer, geltend

pytestmark = pytest.mark.django_db
HTMX = {"HTTP_HX_REQUEST": "true"}


def test_liste_mit_filterleiste_und_euro(neuer_vertrag: VertragBauer, angemeldet: Client) -> None:
    """R2-9: Filter als eine Leiste über der Tabelle (tab-karten), Beträge im deutschen
    Format mit geschütztem Leerzeichen."""
    neuer_vertrag()
    html = angemeldet.get(reverse("soll:liste")).content.decode()
    assert '<form method="get" class="filterleiste"' in html
    assert 'class="tab tab-karten sollposten"' in html
    assert "85,00 €" in html and "85.00 €" not in html


def test_stornieren_im_dialog(neuer_vertrag: VertragBauer, angemeldet: Client) -> None:
    """Review 2, B-Leitlinien: „Stornieren …“ öffnet einen Dialog; ohne Grund steht der Satz
    am Feld, danach ist die Seite ersetzt und die Meldung nennt den Posten."""
    vertrag = neuer_vertrag()
    posten = geltend(vertrag)[-1]
    url = reverse("soll:vertrag", args=[vertrag.pk])
    seite = angemeldet.get(url).content.decode()
    assert f'hx-get="{url}?dialog=storno&amp;posten={posten.pk}"' in seite

    dialog = angemeldet.get(url, {"dialog": "storno", "posten": posten.pk}, **HTMX)
    assert reverse("soll:stornieren", args=[posten.pk]) in dialog.content.decode()
    fehler = angemeldet.post(reverse("soll:stornieren", args=[posten.pk]), {"grund": ""}, **HTMX)
    assert "Ein Storno braucht einen Grund." in fehler.content.decode()

    antwort = angemeldet.post(
        reverse("soll:stornieren", args=[posten.pk]), {"grund": "doppelt"}, **HTMX
    )
    html = antwort.content.decode()
    assert f"Posten #{posten.pk} (Miete " in html and "storniert" in html
    assert 'id="soll-vertrag" class="stapel" hx-swap-oob="true"' in html
    assert "dialogSchliessen" in json.loads(antwort["HX-Trigger"])


def test_posten_von_hand_im_dialog(neuer_vertrag: VertragBauer, angemeldet: Client) -> None:
    """R2-9: „Posten von Hand“ als Dialog, vorausgefüllt mit dem heutigen Datum."""
    vertrag = neuer_vertrag()
    url = reverse("soll:vertrag", args=[vertrag.pk])
    dialog = angemeldet.get(url, {"dialog": "posten"}, **HTMX).content.decode()
    assert 'name="posten-von" value="2026-11-20"' in dialog
    antwort = angemeldet.post(
        url,
        {
            "aktion": "posten",
            "posten-art": "sonstiges",
            "posten-von": "2026-11-12",
            "posten-netto": "25.00",
            "posten-steuersatz": "0",
        },
        **HTMX,
    )
    assert f"25,00 €) für {vertrag.nummer} angelegt" in strip_tags(antwort.content.decode())
