"""Seiten der Anbindungen: Buchungsanfragen entscheiden, Kennzahlen, Abweichungen, Katalog."""

from __future__ import annotations

import datetime

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.anbindungen import services
from apps.anbindungen.models import Buchungsanfrage, Einheitbild
from apps.kern.models import Arbeitsposten, Objekt
from apps.stammdaten import services as stammdaten
from apps.stammdaten.models import Einheit
from tests.ablaeufe.hilfen import posten
from tests.anbindungen.conftest import aktiver_vertrag, vermarktete_einheit
from tests.stammdaten.conftest import PNG, mieter_anlegen

NAME = "Paul Probeanfrage"


def _anfrage(einheit: Einheit, art: str = "buchung", nummer: int = 1) -> Buchungsanfrage:
    anfrage, _ = services.ereignis_annehmen(
        services.WebsiteEreignis(
            ereignis_id=f"w-{nummer}",
            art=art,
            nw_id=einheit.nw_id,
            gemeldet_am=timezone.now(),
            kontakt_name=NAME,
            kontakt_email="paul@testhof.invalid",
        )
    )
    return anfrage


@pytest.mark.parametrize(
    "seite",
    [
        "anbindungen:uebersicht",
        "anbindungen:anfragen",
        "anbindungen:kennzahlen",
        "anbindungen:abweichungen",
        "anbindungen:vermarktung",
    ],
)
def test_seiten_mit_anmeldung_und_am_handy(seite: str, angemeldet: Client, client: Client) -> None:
    html = angemeldet.get(reverse(seite)).content.decode()
    assert 'name="viewport"' in html
    assert f'href="{reverse("anbindungen:uebersicht")}"' in html  # Menüpunkt „Anbindungen“
    assert ">Anbindungen</a>" in html
    assert 'aria-current="page"' in html
    angemeldet.logout()
    assert Client().get(reverse(seite)).status_code == 302


def test_anfrage_annehmen_und_vertrag_anlegen(freie_garage: Einheit, angemeldet: Client) -> None:
    anfrage = _anfrage(freie_garage)
    url = reverse("anbindungen:anfrage", args=[anfrage.pk])
    seite = angemeldet.get(url).content.decode()
    assert NAME in seite and "paul@testhof.invalid" in seite

    assert angemeldet.post(url, {"status": "angenommen"}).status_code == 302
    anfrage.refresh_from_db()
    assert anfrage.status == "angenommen"
    (pruefen,) = posten(services.ART_BUCHUNGSANFRAGE, anfrage)
    assert pruefen.status == "offen"  # bleibt offen bis zum Vertrag

    # Ohne Vertrag geht „Vertrag angelegt“ nicht.
    angemeldet.post(url, {"status": "vertrag_angelegt"})
    anfrage.refresh_from_db()
    assert anfrage.status == "angenommen"

    vertrag = stammdaten.vertrag_anlegen(
        einheiten=[freie_garage],
        mieter=[mieter_anlegen()],
        beginn=datetime.date(2026, 12, 1),
        miete=None,
    )
    antwort = angemeldet.post(url, {"status": "vertrag_angelegt", "vertrag": vertrag.pk})
    assert antwort.status_code == 302
    anfrage.refresh_from_db()
    assert (anfrage.status, anfrage.vertrag) == ("vertrag_angelegt", vertrag)
    assert anfrage.entschieden_am is not None
    pruefen.refresh_from_db()
    assert pruefen.status == "erledigt"
    # Entschieden: keine weiteren Entscheidungen.
    assert services.entscheidungen(anfrage) == ()
    assert "Entscheiden" not in angemeldet.get(url).content.decode()


def test_vertrag_muss_die_einheit_umfassen(objekt: Objekt) -> None:
    garage = vermarktete_einheit(objekt)
    andere = vermarktete_einheit(objekt, Einheit.Typ.STELLPLATZ)
    fremd = aktiver_vertrag(andere)
    anfrage = _anfrage(garage)
    with pytest.raises(services.AnbindungFehler, match="umfasst"):
        services.buchungsanfrage_entscheiden(anfrage, "vertrag_angelegt", vertrag=fremd)
    with pytest.raises(services.AnbindungFehler, match="nicht möglich"):
        services.buchungsanfrage_entscheiden(anfrage, "erledigt")


def test_ablehnen_ueber_den_arbeitsposten(freie_garage: Einheit, angemeldet: Client) -> None:
    anfrage = _anfrage(freie_garage)
    (pruefen,) = posten(services.ART_BUCHUNGSANFRAGE, anfrage)
    liste = angemeldet.get(reverse("kern:arbeitslisten")).content.decode()
    ziel = reverse("anbindungen:anfrage", args=[anfrage.pk])
    assert ziel in liste  # Aktion „Anfrage entscheiden“ und Verweis führen zur Anfrage
    assert NAME not in liste  # Die Arbeitsliste nennt nur die NW-ID

    angemeldet.post(ziel, {"status": "abgelehnt"})
    anfrage.refresh_from_db()
    pruefen.refresh_from_db()
    assert (anfrage.status, pruefen.status) == ("abgelehnt", "erledigt")


def test_kuendigung_erfassen(objekt: Objekt, angemeldet: Client) -> None:
    einheit = vermarktete_einheit(objekt)
    aktiver_vertrag(einheit)
    anfrage = _anfrage(einheit, "kuendigung")
    url = reverse("anbindungen:anfrage", args=[anfrage.pk])
    assert "Kündigung erfasst" in angemeldet.get(url).content.decode()
    angemeldet.post(url, {"status": "erledigt"})
    anfrage.refresh_from_db()
    assert anfrage.status == "erledigt"
    assert not Arbeitsposten.objects.filter(
        art=services.ART_BUCHUNGSANFRAGE, status="offen"
    ).exists()


def test_liste_filtert_offen(freie_garage: Einheit, angemeldet: Client) -> None:
    offen = _anfrage(freie_garage, nummer=1)
    erledigt = _anfrage(freie_garage, nummer=2)
    services.buchungsanfrage_entscheiden(erledigt, "abgelehnt")
    url = reverse("anbindungen:anfragen")
    seite = angemeldet.get(url).content.decode()
    assert f"anfrage-{offen.pk}" in seite and f"anfrage-{erledigt.pk}" not in seite
    alle = angemeldet.get(url, {"status": "alle"}).content.decode()
    assert f"anfrage-{erledigt.pk}" in alle
    # Die Liste zeigt keine Namen.
    assert NAME not in alle


def test_bilder_hochladen_und_entfernen(freie_garage: Einheit, angemeldet: Client) -> None:
    url = reverse("anbindungen:bilder", args=[freie_garage.pk])
    antwort = angemeldet.post(
        url,
        {
            "aktion": "bild",
            "bild": SimpleUploadedFile("tor.png", PNG, content_type="image/png"),
            "beschreibung": "Tor",
        },
    )
    assert antwort.status_code == 302
    bild = Einheitbild.objects.get(einheit=freie_garage)
    assert angemeldet.get(reverse("anbindungen:bild", args=[bild.pk])).status_code == 200
    seite = angemeldet.get(reverse("anbindungen:vermarktung")).content.decode()
    assert "1 Bild" in seite

    falsch = angemeldet.post(url, {"bild": SimpleUploadedFile("liste.txt", b"text")})
    assert falsch.status_code == 200
    assert Einheitbild.objects.count() == 1

    angemeldet.post(reverse("anbindungen:bild_entfernen", args=[bild.pk]))
    bild.refresh_from_db()
    assert not bild.aktiv
    assert services.katalog()[0].bilder == ()
