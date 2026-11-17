"""Seiten der Stammdaten: erreichbar, in der Navigation, am Handy bedienbar."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from apps.kern.models import Arbeitsposten, Objekt
from apps.stammdaten import services
from apps.stammdaten.models import Einheit, Partei, Vertrag

pytestmark = pytest.mark.django_db
D = datetime.date

LISTEN = [
    "stammdaten:uebersicht",
    "stammdaten:einheiten",
    "stammdaten:parteien",
    "stammdaten:vertraege",
    "stammdaten:leerstand",
    "stammdaten:datenstand",
    "stammdaten:einheit_neu",
    "stammdaten:partei_neu",
    "stammdaten:vertrag_neu",
]


@pytest.mark.parametrize("seite", LISTEN)
def test_seiten_mit_anmeldung_und_am_handy(seite: str, angemeldet: Client, client: Client) -> None:
    html = angemeldet.get(reverse(seite)).content.decode()
    assert 'name="viewport"' in html
    assert f'href="{reverse("stammdaten:uebersicht")}" aria-current="page"' in html

    angemeldet.logout()
    assert client.get(reverse(seite)).status_code == 302


def test_detailseiten(aktiver_vertrag: Vertrag, angemeldet: Client, objekt: Objekt) -> None:
    einheit = aktiver_vertrag.einheiten.get()
    partei = aktiver_vertrag.vertragsparteien.get().partei
    wohnung = services.einheit_anlegen(objekt, Einheit.Typ.WOHNUNG)
    for url in (
        reverse("stammdaten:einheit", args=[einheit.pk]),
        reverse("stammdaten:partei", args=[partei.pk]),
        reverse("stammdaten:vertrag", args=[aktiver_vertrag.pk]),
    ):
        assert angemeldet.get(url).status_code == 200
    html = angemeldet.get(reverse("stammdaten:einheit", args=[wohnung.pk])).content.decode()
    assert "geführt in Verwaltio" in html
    assert 'name="zielmiete-betrag"' not in html


def test_neuer_vertrag_fordert_das_dokument_an(
    garage: Einheit, mieter: Partei, angemeldet: Client
) -> None:
    """Standardablauf 1, Schritt 4: Das System legt den Vertrag an und fordert Vertrag und
    Anlagen aus der Vorlage der Vertragsart an."""
    antwort = angemeldet.post(
        reverse("stammdaten:vertrag_neu"),
        {
            "einheit": garage.pk,
            "mieter": mieter.pk,
            "beginn": "2026-12-01",
            "miete": "88.00",
            "zahlweise": "monatlich",
            "kaution": "176.00",
        },
    )
    vertrag = Vertrag.objects.get()
    assert antwort["Location"] == reverse("stammdaten:vertrag", args=[vertrag.pk])
    assert vertrag.status == Vertrag.Status.ENTWURF
    assert vertrag.kaution.betrag == Decimal("176.00")
    assert (
        Arbeitsposten.objects.filter(
            art="Schreiben erstellen", hinweis__startswith="Vorlagenart vertrag_garage"
        ).count()
        == 1
    )
    assert vertrag.checklisten.filter(phase="vertrag").exists()


def test_wohnung_nicht_waehlbar_im_neuen_vertrag(objekt: Objekt, angemeldet: Client) -> None:
    wohnung = services.einheit_anlegen(objekt, Einheit.Typ.WOHNUNG)
    html = angemeldet.get(reverse("stammdaten:vertrag_neu")).content.decode()
    assert f'value="{wohnung.pk}"' not in html


def test_kuendigung_ueber_die_seite(aktiver_vertrag: Vertrag, angemeldet: Client) -> None:
    url = reverse("stammdaten:vertrag", args=[aktiver_vertrag.pk])
    antwort = angemeldet.post(
        url,
        {"aktion": "kuendigung", "kuendigung-zugang_am": "2026-11-03", "kuendigung-von": "mieter"},
    )
    assert antwort["Location"] == f"{url}#kuendigung"
    html = angemeldet.get(url).content.decode()
    assert "Fristende <strong>31.01.2027</strong>" in html


def test_fehler_aus_dem_service_als_meldung(aktiver_vertrag: Vertrag, angemeldet: Client) -> None:
    url = reverse("stammdaten:vertrag", args=[aktiver_vertrag.pk])
    antwort = angemeldet.post(
        url,
        {"aktion": "kondition", "kondition-gueltig_ab": "2020-01-01", "kondition-miete": "1"},
        follow=True,
    )
    assert "vor Vertragsbeginn" in antwort.content.decode()


def test_checkpunkt_abhaken(garage: Einheit, angemeldet: Client) -> None:
    liste = services.checkliste_anlegen("vorbereitung", einheit=garage)
    punkt = liste.punkte.first()
    assert punkt is not None
    weiter = reverse("stammdaten:einheit", args=[garage.pk])
    antwort = angemeldet.post(reverse("stammdaten:checkpunkt", args=[punkt.pk]), {"weiter": weiter})
    assert antwort["Location"] == weiter
    punkt.refresh_from_db()
    assert punkt.erledigt_am is not None
    # Kein Sprung auf fremde Seiten
    antwort = angemeldet.post(
        reverse("stammdaten:checkpunkt", args=[punkt.pk]), {"weiter": "https://example.invalid/"}
    )
    assert antwort["Location"] == reverse("stammdaten:uebersicht")
