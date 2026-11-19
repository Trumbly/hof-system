"""Kennzahlen aus Verwaltio (V12) und die Kachel „Wohnraum“."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, override_settings
from django.urls import reverse

from apps.anbindungen import services
from apps.anbindungen.models import Kennzahl
from apps.anbindungen.verwaltio import (
    FakeVerwaltio,
    VerwaltioApiClient,
    VerwaltioKennzahlen,
    VerwaltioNichtEingerichtet,
)
from apps.kern import zeit
from apps.kern.models import Lauf, Objekt
from apps.stammdaten import services as stammdaten
from apps.stammdaten.models import Einheit, Partei

D = datetime.date
#: Die echten Clients (Pfade aus den Klassen, damit sie Umbenennungen folgen).
ECHT_VERWALTIO = f"{VerwaltioApiClient.__module__}.{VerwaltioApiClient.__name__}"
TAG = D(2026, 11, 17)


@pytest.fixture
def wohnungen(objekt: Objekt) -> list[Einheit]:
    return [
        stammdaten.einheit_anlegen(objekt, Einheit.Typ.WOHNUNG, bezeichnung=f"Wohnung {n}")
        for n in range(1, 4)
    ]


def _zahlen(nw_id: str, posten: str, mahn: int, nka: str, fristen: int) -> VerwaltioKennzahlen:
    return VerwaltioKennzahlen(
        nw_id=nw_id,
        offene_posten=Decimal(posten),
        mahnfaelle=mahn,
        nka_jahr=2025,
        nka_stand=nka,
        fristen_offen=fristen,
        naechste_frist=D(2026, 12, 1) + datetime.timedelta(days=fristen) if fristen else None,
    )


@pytest.fixture
def fake(wohnungen: list[Einheit]) -> FakeVerwaltio:
    a, b, c = (w.nw_id for w in wohnungen)
    return FakeVerwaltio(
        daten={
            a: _zahlen(a, "0.00", 0, "abgeschlossen", 0),
            b: _zahlen(b, "640.00", 1, "in_arbeit", 2),
            c: _zahlen(c, "125.50", 2, "versendet", 1),
        }
    )


def test_kennzahlen_je_wohneinheit_mit_nw_id(wohnungen: list[Einheit], fake: FakeVerwaltio) -> None:
    """V12: je Wohneinheit offene Posten (Summe), Mahnfälle (Anzahl), Stand der NKA und
    Fristen, gespeichert als Kennzahl mit NW-ID und Datum."""
    ergebnis = services.kennzahlen_abrufen(fake, TAG)

    assert (ergebnis.neu, ergebnis.aktualisiert, ergebnis.fehler) == (3, 0, [])
    zeilen = list(Kennzahl.objects.order_by("nw_id"))
    assert [z.nw_id for z in zeilen] == [w.nw_id for w in wohnungen]
    b = zeilen[1]
    assert (b.datum, b.einheit, b.offene_posten, b.mahnfaelle) == (
        TAG,
        wohnungen[1],
        Decimal("640.00"),
        1,
    )
    assert (b.nka_jahr, b.nka_stand, b.fristen_offen, b.naechste_frist) == (
        2025,
        "in_arbeit",
        2,
        D(2026, 12, 3),
    )


def test_idempotent_je_tag(wohnungen: list[Einheit], fake: FakeVerwaltio) -> None:
    services.kennzahlen_abrufen(fake, TAG)
    zweiter = services.kennzahlen_abrufen(fake, TAG)
    assert (zweiter.neu, zweiter.aktualisiert) == (0, 3)
    assert Kennzahl.objects.count() == 3
    services.kennzahlen_abrufen(fake, TAG + datetime.timedelta(days=1))
    assert Kennzahl.objects.count() == 6


def test_einzelne_einheit_scheitert(wohnungen: list[Einheit]) -> None:
    fake = FakeVerwaltio(fehler={wohnungen[0].nw_id})
    ergebnis = services.kennzahlen_abrufen(fake, TAG)
    assert ergebnis.fehler == [wohnungen[0].nw_id]
    assert ergebnis.neu == 2


def test_lauf_verwaltio_kennzahlen(wohnungen: list[Einheit]) -> None:
    """Der Lauf nutzt den Client aus den Einstellungen (in Tests der Fake) und schreibt
    den Laufeintrag."""
    call_command("verwaltio_kennzahlen", "--stichtag", "2026-11-17")
    lauf = Lauf.objects.get(name="verwaltio_kennzahlen")
    assert (lauf.status, lauf.gebucht, lauf.fehler) == ("erfolgreich", 3, 0)
    assert "3 Kennzahlen neu" in lauf.meldung
    # Gleicher Tag noch einmal: aktualisiert, nicht doppelt.
    call_command("verwaltio_kennzahlen", "--stichtag", "2026-11-17")
    assert Kennzahl.objects.count() == 3
    assert "3 aktualisiert" in Lauf.objects.filter(name="verwaltio_kennzahlen").first().meldung  # type: ignore[union-attr]


@override_settings(
    HOF_VERWALTIO_CLIENT=ECHT_VERWALTIO,
    VERWALTIO_URL="",
    VERWALTIO_TOKEN="",
)
def test_lauf_ohne_einrichtung_endet_ohne_fehler(wohnungen: list[Einheit]) -> None:
    call_command("verwaltio_kennzahlen", "--stichtag", "2026-11-17")
    lauf = Lauf.objects.get(name="verwaltio_kennzahlen")
    assert lauf.status == "erfolgreich"
    assert "nicht eingerichtet" in lauf.meldung
    assert not Kennzahl.objects.exists()


@override_settings(
    HOF_VERWALTIO_CLIENT=ECHT_VERWALTIO,
    VERWALTIO_URL="https://verwaltio.invalid/api",
    VERWALTIO_TOKEN="nur-test",
)
def test_echter_client_ist_noch_offen(wohnungen: list[Einheit]) -> None:
    """Die echte Implementierung wartet auf die API-Dokumentation: klar als offen markiert."""
    client = VerwaltioApiClient()
    with pytest.raises(NotImplementedError, match="API-Dokumentation"):
        client.kennzahlen("NW-W-0001", TAG)
    with pytest.raises(NotImplementedError, match="API-Dokumentation"):
        client.wohneinheiten()
    with pytest.raises(CommandError, match="NotImplementedError"):
        call_command("verwaltio_kennzahlen", "--stichtag", "2026-11-17")
    assert Lauf.objects.get(name="verwaltio_kennzahlen").status == "abgebrochen"


@override_settings(VERWALTIO_URL="", VERWALTIO_TOKEN="")
def test_echter_client_ohne_einrichtung() -> None:
    with pytest.raises(VerwaltioNichtEingerichtet):
        VerwaltioApiClient()


def test_fake_in_produktion_gesperrt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DJANGO_ENV", "prod")
    with pytest.raises(VerwaltioNichtEingerichtet, match="Produktion"):
        FakeVerwaltio()


def test_fake_ohne_angaben_ist_fest(wohnungen: list[Einheit]) -> None:
    fake = FakeVerwaltio()
    assert fake.wohneinheiten() == [w.nw_id for w in wohnungen]
    eins = fake.kennzahlen(wohnungen[0].nw_id, TAG)
    assert eins == FakeVerwaltio().kennzahlen(wohnungen[0].nw_id, TAG)
    assert eins.offene_posten >= 0 and eins.nka_jahr == 2025


def test_wohnraum_ueberblick(wohnungen: list[Einheit], fake: FakeVerwaltio) -> None:
    leer = services.wohnraum_ueberblick(TAG)
    assert (leer.stand, leer.wohneinheiten, leer.veraltet) == (None, 3, True)

    services.kennzahlen_abrufen(fake, TAG)
    ueberblick = services.wohnraum_ueberblick(TAG)
    assert ueberblick.stand == TAG
    assert (ueberblick.mit_kennzahl, ueberblick.ohne_kennzahl) == (3, 0)
    assert ueberblick.offene_posten == Decimal("765.50")
    assert (ueberblick.mahnfaelle, ueberblick.einheiten_mit_mahnung) == (3, 2)
    assert ueberblick.nka_jahr == 2025
    assert ueberblick.nka == (("in Arbeit", 1), ("versendet", 1), ("abgeschlossen", 1))
    assert (ueberblick.fristen_offen, ueberblick.naechste_frist) == (3, D(2026, 12, 2))
    assert not ueberblick.veraltet
    assert services.wohnraum_ueberblick(TAG + datetime.timedelta(days=3)).veraltet


def test_cockpit_kachel_wohnraum_ohne_namen(
    wohnungen: list[Einheit], fake: FakeVerwaltio, angemeldet: Client
) -> None:
    """Abnahme Schritt 10: Das Cockpit (bis Schritt 9b die Startseite) zeigt für Wohnraum
    offene Posten, Mahnfälle, Stand der NKA und Fristen, ohne Namen."""
    partei = stammdaten.partei_anlegen(Partei.Art.PERSON, vorname="Wanda", nachname="Wohnmieter")
    services.kennzahlen_abrufen(fake, zeit.heute())

    html = angemeldet.get(reverse("kern:startseite")).content.decode()

    kachel = html.split('id="kachel-wohnraum"', 1)[1].split("</li>", 1)[0]
    assert "Offene Posten: <strong>765,50\u00a0€</strong>" in kachel
    assert "Mahnfälle: <strong>3</strong>" in kachel
    assert "NKA 2025: 1 in Arbeit, 1 versendet, 1 abgeschlossen" in kachel
    assert "Fristen: <strong>3</strong> offen, nächste am 02.12.2026" in kachel
    assert "3 von 3 Wohnungen" in kachel
    assert reverse("anbindungen:kennzahlen") in kachel
    for name in (partei.vorname, partei.nachname, "Wohnung 1", wohnungen[0].nw_id):
        assert name not in kachel

    seite = angemeldet.get(reverse("anbindungen:kennzahlen")).content.decode()
    assert wohnungen[1].nw_id in seite and "640,00\u00a0€" in seite
    assert "Wohnung 1" not in seite


def test_kachel_ohne_kennzahlen(angemeldet: Client) -> None:
    html = angemeldet.get(reverse("kern:startseite")).content.decode()
    assert "Noch keine Kennzahlen aus Verwaltio" in html
