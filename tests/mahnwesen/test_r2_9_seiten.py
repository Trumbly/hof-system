"""Mahnwesen in der neuen Gestaltung (PR R2-9): Sperren und „Von Hand belegen“ als Dialoge,
Meldungen mit Link, Unternavigation als Reiter."""

from __future__ import annotations

import datetime
import json

import pytest
from django.test import Client
from django.urls import reverse
from django.utils.html import strip_tags

from apps.kern.models import Benutzer
from apps.mahnwesen.models import Mahnsperre
from tests.mahnwesen.conftest import FAELLIG_OKT, Mahnhof, Vorlagen, fall_zu, lauf, letzter

pytestmark = pytest.mark.django_db
HTMX = {"HTTP_HX_REQUEST": "true"}
SPERREN = reverse("mahnwesen:sperren")


def test_sperre_setzen_und_aufheben_im_dialog(mahnhof: Mahnhof, angemeldet: Client) -> None:
    """R2-9: „Sperre setzen“ und „Aufheben“ öffnen Dialoge; Fehler stehen am Feld, nach dem
    Speichern wird die Liste ersetzt und die Meldung nennt Vertrag, Grund und Datum."""
    vertrag = mahnhof.vertrag()
    seite = angemeldet.get(SPERREN).content.decode()
    assert f'hx-get="{SPERREN}?dialog=sperre"' in seite
    assert '<nav class="reiter unternavigation" aria-label="Mahnwesen">' in seite

    dialog = angemeldet.get(SPERREN, {"dialog": "sperre"}, **HTMX).content.decode()
    assert "Sperre setzen" in dialog and 'name="vertrag"' in dialog

    fehler = angemeldet.post(
        SPERREN,
        {"vertrag": vertrag.pk, "grund": "ratenplan", "von": "2026-10-10", "bis": "2026-10-01"},
        **HTMX,
    ).content.decode()
    assert "„bis“ liegt vor „gilt ab“." in fehler
    assert not Mahnsperre.objects.exists()

    antwort = angemeldet.post(
        SPERREN, {"vertrag": vertrag.pk, "grund": "ratenplan", "von": "2026-10-01"}, **HTMX
    )
    html = antwort.content.decode()
    sperre = Mahnsperre.objects.get()
    assert f"Mahnsperre für {vertrag.nummer} gesetzt (Ratenplan, ab 01.10.2026)" in strip_tags(html)
    assert f"#sperre-{sperre.pk}" in html and 'id="sperren-liste"' in html
    assert "dialogSchliessen" in json.loads(antwort["HX-Trigger"])

    aufheben = angemeldet.get(
        SPERREN, {"dialog": "aufheben", "sperre": sperre.pk}, **HTMX
    ).content.decode()
    assert reverse("mahnwesen:sperre_aufheben", args=[sperre.pk]) in aufheben
    antwort = angemeldet.post(
        reverse("mahnwesen:sperre_aufheben", args=[sperre.pk]), {"am": "2026-10-20"}, **HTMX
    )
    assert f"Mahnsperre für {vertrag.nummer} zum 20.10.2026 aufgehoben" in strip_tags(
        antwort.content.decode()
    )


def test_von_hand_belegen_im_dialog(
    mahnhof: Mahnhof, benutzer: Benutzer, stufen: None, angemeldet: Client
) -> None:
    """R2-9: „Von Hand belegen“ (Probebetrieb) ist ein Dialog; danach sind Kopf und
    Stufenverlauf ersetzt und die Meldung nennt Stufe, Vertrag und Datum."""
    Vorlagen(benutzer).freigeben("briefkopf")
    vertrag = mahnhof.vertrag()
    lauf(FAELLIG_OKT + 5 * datetime.timedelta(days=1))
    schritt = letzter(vertrag)
    url = reverse("mahnwesen:fall", args=[fall_zu(vertrag).pk])

    seite = angemeldet.get(url).content.decode()
    assert f"{url}?dialog=belegen&amp;schritt={schritt.pk}" in seite
    dialog = angemeldet.get(url, {"dialog": "belegen", "schritt": schritt.pk}, **HTMX)
    assert f'name="schritt" value="{schritt.pk}"' in dialog.content.decode()

    antwort = angemeldet.post(url, {"schritt": schritt.pk, "am": "2026-10-12"}, **HTMX)
    html = antwort.content.decode()
    assert f"für {vertrag.nummer} am 12.10.2026 belegt" in strip_tags(html)
    assert 'id="stufenverlauf" hx-swap-oob="true"' in html
    schritt.refresh_from_db()
    assert schritt.belegt_am == datetime.date(2026, 10, 12)
