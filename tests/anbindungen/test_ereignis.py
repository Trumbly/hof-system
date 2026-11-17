"""Ereignisse der Website (W10): Buchung und Kündigung werden Anfrage und Arbeitsposten."""

from __future__ import annotations

import datetime
import json
import logging
from typing import Any

import pytest
from django.conf import settings
from django.test import Client

from apps.anbindungen import services
from apps.anbindungen.models import Buchungsanfrage
from apps.kern import personenfelder
from apps.kern.models import Arbeitsposten, Objekt
from apps.stammdaten import services as stammdaten
from apps.stammdaten.models import Einheit
from tests.ablaeufe.hilfen import posten
from tests.anbindungen.conftest import aktiver_vertrag, vermarktete_einheit

URL = "/api/website/ereignis"
NAME = "Hanna Erfunden"
MAIL = "hanna.erfunden@testhof.invalid"


def _meldung(einheit: Einheit, **anders: Any) -> dict[str, Any]:
    daten: dict[str, Any] = {
        "ereignis_id": "web-2026-000123",
        "art": "buchung",
        "nw_id": einheit.nw_id,
        "zeitpunkt": "2026-11-17T10:15:00+01:00",
        "wunsch_ab": "2026-12-01",
        "kontakt": {"name": NAME, "email": MAIL, "telefon": "0151 23456789"},
        "nachricht": "Ich brauche die Garage ab Dezember.",
    }
    daten.update(anders)
    return daten


def _senden(website: Client, daten: Any) -> Any:
    return website.post(URL, data=json.dumps(daten), content_type="application/json")


def test_buchung_erscheint_als_anfrage_an_der_einheit(
    freie_garage: Einheit, website: Client
) -> None:
    """Abnahme Schritt 10: Eine Buchung auf der Website erscheint als Anfrage an der Einheit,
    mit Arbeitsposten „Buchungsanfrage prüfen“; den Vertrag legst du in v1 von Hand an."""
    antwort = _senden(website, _meldung(freie_garage))

    assert antwort.status_code == 201, antwort.content
    daten = antwort.json()
    anfrage = Buchungsanfrage.objects.get(pk=daten["anfrage"])
    assert daten == {
        "ereignis_id": "web-2026-000123",
        "anfrage": anfrage.pk,
        "neu": True,
        "status": "offen",
    }
    assert list(freie_garage.buchungsanfragen.all()) == [anfrage]
    assert (anfrage.art, anfrage.status) == ("buchung", "offen")
    assert anfrage.wunsch_ab == datetime.date(2026, 12, 1)
    assert anfrage.gemeldet_am == datetime.datetime.fromisoformat("2026-11-17T10:15:00+01:00")
    assert (anfrage.kontakt_name, anfrage.kontakt_email) == (NAME, MAIL)
    assert anfrage.vertrag is None
    (pruefen,) = posten(services.ART_BUCHUNGSANFRAGE, anfrage)
    assert pruefen.status == "offen"
    assert pruefen.aktionen == [services.AKTION_ENTSCHEIDEN, "zurueckstellen"]
    assert pruefen.faellig_am == datetime.date(2026, 11, 19)
    # Der Hinweis nennt die NW-ID, keine Namen.
    assert freie_garage.nw_id in pruefen.hinweis
    assert NAME not in pruefen.hinweis and MAIL not in pruefen.hinweis
    # Ohne Vertrag: Die Einheit bleibt buchbar, bis du entscheidest.
    assert freie_garage.nw_id in {e.nw_id for e in services.katalog()}


def test_dieselbe_meldung_zweimal_ergibt_eine_anfrage(
    freie_garage: Einheit, website: Client
) -> None:
    """W10 ist idempotent über die Ereignis-ID der Website."""
    assert _senden(website, _meldung(freie_garage)).status_code == 201
    zweite = _senden(website, _meldung(freie_garage))
    assert zweite.status_code == 200
    assert zweite.json()["neu"] is False
    assert Buchungsanfrage.objects.count() == 1
    assert Arbeitsposten.objects.filter(art=services.ART_BUCHUNGSANFRAGE).count() == 1

    # Dieselbe ID mit anderem Inhalt: Konflikt, nichts geändert.
    anders = _senden(website, _meldung(freie_garage, wunsch_ab="2027-01-01"))
    assert anders.status_code == 409
    assert "2027" not in anders.content.decode()
    assert Buchungsanfrage.objects.get().wunsch_ab == datetime.date(2026, 12, 1)

    # Eine neue ID ist eine neue Anfrage.
    assert _senden(website, _meldung(freie_garage, ereignis_id="web-2")).status_code == 201
    assert Buchungsanfrage.objects.count() == 2


def test_kuendigung_mit_vertragsnummer(objekt: Objekt, website: Client) -> None:
    einheit = vermarktete_einheit(objekt)
    vertrag = aktiver_vertrag(einheit)
    antwort = _senden(
        website,
        _meldung(einheit, art="kuendigung", vertragsnummer=vertrag.nummer, wunsch_ab=None),
    )
    assert antwort.status_code == 201
    anfrage = Buchungsanfrage.objects.get()
    assert (anfrage.art, anfrage.vertrag, anfrage.vertragsnummer) == (
        "kuendigung",
        vertrag,
        vertrag.nummer,
    )
    (pruefen,) = posten(services.ART_BUCHUNGSANFRAGE, anfrage)
    assert pruefen.hinweis == f"Kündigung für Einheit {einheit.nw_id}"
    assert services.entscheidungen(anfrage) == ("erledigt", "abgelehnt")


def test_falsche_vertragsnummer_bleibt_ohne_verweis(objekt: Objekt) -> None:
    einheit = vermarktete_einheit(objekt)
    aktiver_vertrag(einheit)
    anfrage, _ = services.ereignis_annehmen(
        services.WebsiteEreignis(
            ereignis_id="k-1",
            art="kuendigung",
            nw_id=einheit.nw_id,
            gemeldet_am=datetime.datetime(2026, 11, 17, 9, tzinfo=datetime.UTC),
            vertragsnummer="V-9999",
        )
    )
    assert anfrage.vertrag is None
    assert anfrage.vertragsnummer == "V-9999"


@pytest.mark.parametrize(
    ("aenderung", "status", "feld"),
    [
        ({"ereignis_id": ""}, 400, "ereignis_id"),
        ({"ereignis_id": "a b"}, 400, "ereignis_id"),
        ({"art": "storno"}, 400, "art"),
        ({"nw_id": None}, 400, "nw_id"),
        ({"zeitpunkt": "gestern"}, 400, "zeitpunkt"),
        ({"wunsch_ab": "01.12.2026"}, 400, "wunsch_ab"),
        ({"kontakt": {"name": NAME, "email": "kein-at-zeichen"}}, 400, "kontakt.email"),
        ({"kontakt": {"name": 42}}, 400, "kontakt.name"),
        ({"kontakt": {"name": "x" * 201}}, 400, "kontakt.name"),
        ({"nachricht": ["Liste"]}, 400, "nachricht"),
        ({"nw_id": "NW-G-9999"}, 422, "nw_id"),
    ],
)
def test_fehlermeldungen_ohne_werte(
    freie_garage: Einheit, website: Client, aenderung: dict[str, Any], status: int, feld: str
) -> None:
    """Fehlermeldungen nennen das Feld, nie Werte aus der Meldung."""
    daten = _meldung(freie_garage, **aenderung)
    antwort = _senden(website, daten)
    assert antwort.status_code == status
    text = antwort.json()["fehler"]
    assert feld in text
    for wert in (NAME, MAIL, "kein-at-zeichen", "NW-G-9999", "storno", "gestern", "01.12.2026"):
        assert wert not in text
    assert not Buchungsanfrage.objects.exists()


def test_wohnraum_und_aufgeloeste_einheit_abgewiesen(objekt: Objekt, website: Client) -> None:
    wohnung = stammdaten.einheit_anlegen(objekt, Einheit.Typ.WOHNUNG)
    antwort = _senden(website, _meldung(wohnung))
    assert antwort.status_code == 422
    assert "Verwaltio" in antwort.json()["fehler"]
    assert wohnung.nw_id not in antwort.json()["fehler"]

    alt = vermarktete_einheit(objekt)
    stammdaten.einheit_aufloesen(alt, datetime.date(2026, 6, 1))
    antwort = _senden(website, _meldung(alt, ereignis_id="w-alt"))
    assert antwort.status_code == 422
    assert not Buchungsanfrage.objects.exists()


def test_ohne_token_kein_ereignis(freie_garage: Einheit, client: Client) -> None:
    antwort = client.post(
        URL, data=json.dumps(_meldung(freie_garage)), content_type="application/json"
    )
    assert antwort.status_code == 401
    assert not Buchungsanfrage.objects.exists()


def test_kein_json(website: Client, db: None) -> None:
    antwort = website.post(URL, data="kein json", content_type="application/json")
    assert antwort.status_code == 400
    assert website.post(URL, data="[]", content_type="application/json").status_code == 400
    assert website.get(URL).status_code == 405


def test_zeitpunkt_ohne_zeitzone_gilt_in_berlin(freie_garage: Einheit) -> None:
    from apps.anbindungen import api

    ereignis = api.ereignis_lesen(_meldung(freie_garage, zeitpunkt="2026-11-17T10:15:00"))
    assert ereignis.gemeldet_am.utcoffset() == datetime.timedelta(hours=1)


def test_kontaktfelder_sind_personenfelder() -> None:
    """Die Kontaktdaten der anfragenden Person sind Personenfelder; Logs filtern sie."""
    assert personenfelder.personenfelder(Buchungsanfrage) == {
        "kontakt_name",
        "kontakt_email",
        "kontakt_telefon",
        "nachricht",
    }


def test_log_nennt_keine_kontaktdaten(
    freie_garage: Einheit, website: Client, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        _senden(website, _meldung(freie_garage))
    text = caplog.text
    assert "web-2026-000123" in text
    assert NAME not in text and MAIL not in text


def test_arten_melden_sich_ueber_die_benachrichtigung() -> None:
    arten = set(settings.HOF_BENACHRICHTIGUNG_ARTEN)
    assert {services.ART_BUCHUNGSANFRAGE, services.ART_ID_ABWEICHUNG} <= arten
