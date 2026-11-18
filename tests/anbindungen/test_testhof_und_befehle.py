"""Testhof-Modul der Anbindungen, Befehl ``website_token`` und Zeitpläne der Läufe."""

from __future__ import annotations

import io

from django.core.management import call_command
from django.test import Client

from apps.anbindungen import services
from apps.anbindungen.models import Abweichung, Buchungsanfrage, Kennzahl, Websitezugang
from apps.anbindungen.verwaltio import FakeVerwaltio
from apps.anbindungen.website import FakeWebsite
from apps.stammdaten.models import Einheit
from testhof import erfunden
from testhof.aufbau import STICHTAG, Testhof
from tests.ablaeufe.hilfen import posten
from tests.test_betrieb import SYSTEMD, _systemd


def test_testhof_anfragen_kennzahlen_und_abweichung(testhof: Testhof) -> None:
    """Testhof: Buchungsanfragen, Kennzahlen und eine absichtliche ID-Abweichung im
    Website-Fake, deterministisch (die Gleichheit prüft tests/testhof/test_generator.py)."""
    daten = testhof.anbindungen
    assert [(a.art, a.status) for a in daten.anfragen] == [
        ("buchung", "offen"),
        ("buchung", "angenommen"),
        ("kuendigung", "offen"),
    ]
    for anfrage in daten.anfragen:
        assert erfunden.ist_invalid_mail(anfrage.kontakt_email)
        assert erfunden.ist_fiktive_telefonnummer(anfrage.kontakt_telefon)
    assert daten.kuendigung.vertrag is not None
    assert daten.kuendigung.vertragsnummer == daten.kuendigung.vertrag.nummer
    assert len(posten(services.ART_BUCHUNGSANFRAGE, daten.buchung_offen, status="offen")) == 1

    # Die offene Buchung lässt die Garage im Katalog, die angenommene nimmt ihre Einheit heraus.
    im_katalog = {e.nw_id for e in services.katalog(STICHTAG)}
    assert daten.buchung_offen.einheit.nw_id in im_katalog
    assert daten.buchung_angenommen.einheit.nw_id not in im_katalog
    assert len(im_katalog) == 10

    wohnungen = Einheit.objects.filter(typ=Einheit.Typ.WOHNUNG).count()
    assert Kennzahl.objects.filter(datum=STICHTAG).count() == wohnungen == 6
    assert services.wohnraum_ueberblick(STICHTAG).mit_kennzahl == 6

    ((hof_id, website_id),) = daten.website_umbenannt.items()
    assert website_id == hof_id.lower()
    abweichung = Abweichung.objects.get(behoben_am__isnull=True)
    assert abweichung == daten.abweichung
    assert (abweichung.quelle, abweichung.art, abweichung.nw_id, abweichung.gemeldet) == (
        "website",
        "schreibweise",
        hof_id,
        website_id,
    )
    assert len(posten(services.ART_ID_ABWEICHUNG, abweichung, status="offen")) == 1


def test_testhof_abweichung_schliesst_sich_wenn_die_website_sie_behebt(testhof: Testhof) -> None:
    ergebnis = services.id_abgleich(
        website=FakeWebsite(), verwaltio=FakeVerwaltio(), datum=STICHTAG
    )
    assert (ergebnis.neu, ergebnis.behoben) == (0, 1)
    assert not Abweichung.objects.filter(behoben_am__isnull=True).exists()


def test_befehl_website_token(freie_garage: Einheit, client: Client) -> None:
    ausgabe = io.StringIO()
    call_command("website_token", "--bezeichnung", "Website-Server", stdout=ausgabe)
    token = ausgabe.getvalue().strip().splitlines()[-1]
    assert token.startswith(services.TOKEN_PRAEFIX)
    assert Websitezugang.objects.get(aktiv=True).bezeichnung == "Website-Server"
    url = "/api/website/katalog"
    assert client.get(url, HTTP_AUTHORIZATION=f"Bearer {token}").status_code == 200

    # --behalten: Wechsel ohne Ausfall.
    call_command("website_token", "--behalten", stdout=io.StringIO())
    assert Websitezugang.objects.filter(aktiv=True).count() == 2
    assert client.get(url, HTTP_AUTHORIZATION=f"Bearer {token}").status_code == 200

    call_command("website_token", "--sperren", stdout=io.StringIO())
    assert not Websitezugang.objects.filter(aktiv=True).exists()
    assert client.get(url, HTTP_AUTHORIZATION=f"Bearer {token}").status_code == 401


def test_zeitplaene_der_laeufe() -> None:
    """Zeitplan (seit Schritt 9): verwaltio_kennzahlen und danach id_abgleich laufen täglich
    im Tageslauf, nach den Fristen; eigene Timer entfallen."""
    from apps.cockpit import tageslauf

    assert _systemd(SYSTEMD / "laeufe" / "tageslauf.conf")["Timer"]["OnCalendar"]
    rang = {s.name: s.reihenfolge for s in tageslauf.schritte()}
    assert rang["fristen"] < rang["verwaltio_kennzahlen"] < rang["id_abgleich"]
    for lauf in ("verwaltio_kennzahlen", "id_abgleich"):
        assert not (SYSTEMD / "laeufe" / f"{lauf}.conf").exists()


def test_buchungsanfrage_str_ohne_namen(testhof: Testhof) -> None:
    anfrage: Buchungsanfrage = testhof.anbindungen.buchung_offen
    assert str(anfrage) == f"Buchungsanfrage #{anfrage.pk}"
