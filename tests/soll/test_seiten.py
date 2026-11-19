"""Seite „Offene Posten“ je Vertrag und als Gesamtliste (Schritt 5, Aufgabe 7)."""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.test import Client
from django.urls import reverse

from apps.soll import services as soll
from apps.soll.models import Sollposten, Sollregel
from apps.stammdaten.models import Vertrag
from tests.soll.conftest import VertragBauer, geltend

D = datetime.date
E = Decimal


def test_offene_posten_je_vertrag(neuer_vertrag: VertragBauer, angemeldet: Client) -> None:
    """Schritt 5, Aufgabe 7: Seite „Offene Posten“ je Vertrag, am Handy bedienbar (Viewport,
    Karten statt breiter Tabelle), mit Summe und Link aus der Vertragsseite."""
    vertrag = neuer_vertrag(D(2026, 10, 1))
    oktober, november = geltend(vertrag)
    soll.zahlungsstand_setzen(oktober, E("85.00"))

    url = reverse("soll:vertrag", args=[vertrag.pk])
    html = angemeldet.get(url).content.decode()
    assert 'name="viewport"' in html
    assert 'class="tab tab-karten sollposten"' in html and 'data-titel="fällig"' in html
    assert "85,00\u00a0€</strong> in 1 Posten" in html
    assert f'id="posten-{november.pk}"' in html
    assert "04.11.2026" in html
    assert "3. Werktag des Monats" in html
    assert f'href="{reverse("soll:liste")}" aria-current="page"' in html

    vertragsseite = angemeldet.get(reverse("stammdaten:vertrag", args=[vertrag.pk]))
    text = vertragsseite.content.decode()
    assert f'href="{url}"' in text and "85,00\u00a0€</strong> in 1 Posten" in text


def test_gesamtliste_mit_filter(neuer_vertrag: VertragBauer, angemeldet: Client) -> None:
    """Schritt 5, Aufgabe 7: Gesamtliste über alle Verträge mit Filter."""
    a = neuer_vertrag(D(2026, 11, 1))
    b = neuer_vertrag(D(2026, 10, 1), "40.00")
    (bezahlt, _) = geltend(b)
    soll.zahlungsstand_setzen(bezahlt, E("40.00"))
    liste = reverse("soll:liste")

    html = angemeldet.get(liste).content.decode()
    assert a.nummer in html and b.nummer in html
    assert "2 Posten" in html and "125,00\u00a0€" in html

    html = angemeldet.get(liste, {"suche": a.nummer}).content.decode()
    assert "1 Posten" in html and b.nummer not in html
    html = angemeldet.get(liste, {"status": "bezahlt"}).content.decode()
    assert "1 Posten" in html and f'id="posten-{bezahlt.pk}"' in html
    html = angemeldet.get(liste, {"status": "ueberfaellig"}).content.decode()
    assert "2 Posten" in html  # beide fällig vor dem 20.11.2026
    html = angemeldet.get(liste, {"faellig_bis": "2026-11-03"}).content.decode()
    assert "0 Posten" in html
    html = angemeldet.get(liste, {"art": "pauschale"}).content.decode()
    assert "0 Posten" in html


def test_seiten_nur_mit_anmeldung(neuer_vertrag: VertragBauer, client: Client) -> None:
    vertrag = neuer_vertrag(D(2026, 11, 1))
    for url in (reverse("soll:liste"), reverse("soll:vertrag", args=[vertrag.pk])):
        assert client.get(url).status_code == 302


def test_stornieren_fall_und_regel(neuer_vertrag: VertragBauer, angemeldet: Client) -> None:
    vertrag = neuer_vertrag(D(2026, 11, 1))
    (posten,) = geltend(vertrag)
    url = reverse("soll:vertrag", args=[vertrag.pk])

    antwort = angemeldet.post(reverse("soll:stornieren", args=[posten.pk]), {"grund": "Test"})
    assert antwort["Location"] == url
    posten.refresh_from_db()
    assert (posten.status, posten.storno_grund) == ("storniert", "Test")

    # „Soll neu stellen“ erzeugt den Posten aus dem Vertrag wieder.
    angemeldet.post(url, {"aktion": "neu_stellen"})
    (neu,) = geltend(vertrag)
    assert neu.pk != posten.pk

    antwort = angemeldet.post(
        url,
        {"aktion": "regel", "regel-faelligkeit": "kalendertag", "regel-faelligkeit_tag": "1"},
    )
    assert antwort.status_code == 302
    assert Sollregel.objects.get(vertrag=vertrag).faelligkeit == "kalendertag"
    assert [p.faellig_am for p in geltend(vertrag)] == [D(2026, 11, 1)]

    antwort = angemeldet.post(
        url, {"aktion": "regel", "regel-faelligkeit": "werktag", "regel-faelligkeit_tag": "15"}
    )
    assert antwort.status_code == 200
    assert "außerhalb" in antwort.content.decode()


def test_posten_von_hand_ueber_die_seite(neuer_vertrag: VertragBauer, angemeldet: Client) -> None:
    vertrag: Vertrag = neuer_vertrag(D(2026, 11, 1))
    url = reverse("soll:vertrag", args=[vertrag.pk])
    antwort = angemeldet.post(
        url,
        {
            "aktion": "posten",
            "posten-art": "sonstiges",
            "posten-bezeichnung": "Ersatz Handsender",
            "posten-von": "2026-11-12",
            "posten-netto": "25.00",
            "posten-steuersatz": "0",
        },
    )
    assert antwort.status_code == 302
    posten = Sollposten.objects.get(vertrag=vertrag, art="sonstiges")
    assert (posten.betrag, posten.quelle) == (E("25.00"), "hand")
    assert "Ersatz Handsender" in angemeldet.get(url).content.decode()
