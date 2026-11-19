"""Ablesung am Handy (O2) und die übrigen Seiten der Zähler."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.kern.models import Objekt
from apps.stammdaten.models import Einheit
from apps.zaehler import services
from apps.zaehler.models import Zaehler
from tests.zaehler.conftest import foto

pytestmark = pytest.mark.django_db
D = datetime.date


@pytest.fixture
def hauptzaehler(objekt: Objekt) -> Zaehler:
    return services.zaehler_anlegen("TZ-W-1", "wasser", objekt=objekt, bezeichnung="Keller")


def _ablesung(objekt: Objekt) -> str:
    return reverse("zaehler:ablesung", args=[objekt.pk])


def test_ablesung_am_handy_speichert_stand_datum_und_foto(
    angemeldet: Client, objekt: Objekt, stromzaehler: Zaehler, hauptzaehler: Zaehler
) -> None:
    """Abnahme Schritt 8 (O2): Die Ablesung am Handy listet die Zähler eines Objekts und
    speichert je Zähler Stand, Datum und Foto (direkt aus der Kamera)."""
    seite = angemeldet.get(_ablesung(objekt)).content.decode()
    assert 'name="viewport"' in seite
    assert "Strom TZ-S-1" in seite and "Wasser TZ-W-1" in seite
    assert seite.index("TZ-W-1") < seite.index("TZ-S-1")  # erst die Hauptzähler
    for z in (stromzaehler, hauptzaehler):
        # R2-9: eigener Kameraknopf (Baustein dateiknoepfe), Stand mit Zahlentastatur
        assert f'type="file" name="foto_{z.pk}" accept="image/*" capture="environment"' in seite
        assert f'name="stand_{z.pk}"' in seite and 'inputmode="decimal"' in seite

    antwort = angemeldet.post(
        _ablesung(objekt),
        {
            "datum": "2025-12-31",
            f"stand_{stromzaehler.pk}": "812,4",
            f"foto_{stromzaehler.pk}": foto("zaehler.png"),
            f"stand_{hauptzaehler.pk}": "",
        },
    )
    assert antwort.status_code == 302

    stand = stromzaehler.staende.get(datum=D(2025, 12, 31))
    assert (stand.stand, stand.art, bool(stand.foto)) == (Decimal("812.4"), "abgelesen", True)
    assert not stand.foto.name.endswith("zaehler.png")  # neutraler Dateiname
    assert not hauptzaehler.staende.exists()
    # Das Foto gibt es nur mit Anmeldung.
    url = reverse("zaehler:foto", args=[stand.pk])
    assert angemeldet.get(url).status_code == 200
    angemeldet.logout()
    abgemeldet = Client().get(url)
    assert abgemeldet.status_code == 302
    assert reverse("account_login") in abgemeldet["Location"]


def test_kleinerer_stand_am_handy_wird_abgelehnt(
    angemeldet: Client, objekt: Objekt, stromzaehler: Zaehler, hauptzaehler: Zaehler
) -> None:
    """Abnahme Schritt 8: Ein kleinerer Stand als der letzte wird am Handy abgelehnt; die
    übrigen Stände des Formulars werden gespeichert, der abgelehnte bleibt stehen."""
    services.stand_erfassen(stromzaehler, D(2024, 12, 31), "900")
    antwort = angemeldet.post(
        _ablesung(objekt),
        {
            "datum": "2025-12-31",
            f"stand_{stromzaehler.pk}": "850",
            f"stand_{hauptzaehler.pk}": "42,25",
        },
    )
    seite = antwort.content.decode()
    assert antwort.status_code == 200
    assert "850 ist kleiner als der letzte Stand 900 vom 31.12.2024" in seite
    assert f'name="stand_{stromzaehler.pk}" value="850"' in seite
    assert not stromzaehler.staende.filter(datum=D(2025, 12, 31)).exists()
    assert hauptzaehler.staende.get().stand == Decimal("42.25")


def test_nach_wechsel_wird_der_kleinere_stand_angenommen(
    angemeldet: Client, objekt: Objekt, stromzaehler: Zaehler
) -> None:
    """Schritt 8, Aufgabe 3: Ein kleinerer Stand wird abgelehnt, außer nach einem Wechsel; dann
    steht in der Liste der neue Zähler."""
    services.stand_erfassen(stromzaehler, D(2024, 12, 31), "900")
    neuer = services.zaehler_wechseln(
        stromzaehler, D(2025, 6, 15), "950", neue_nummer="TZ-S-2", einbau_stand="0"
    ).neuer

    seite = angemeldet.get(_ablesung(objekt)).content.decode()
    assert "TZ-S-2" in seite and "TZ-S-1" not in seite
    antwort = angemeldet.post(_ablesung(objekt), {"datum": "2025-12-31", f"stand_{neuer.pk}": "12"})
    assert antwort.status_code == 302
    assert neuer.staende.get(datum=D(2025, 12, 31)).stand == Decimal("12")


def test_schaetzwert_am_handy_mit_kennzeichen(
    angemeldet: Client, objekt: Objekt, stromzaehler: Zaehler
) -> None:
    """Schritt 8, Aufgabe 3: Schätzwerte tragen ein Kennzeichen, auch in den Listen."""
    angemeldet.post(
        _ablesung(objekt),
        {
            "datum": "2025-12-31",
            f"stand_{stromzaehler.pk}": "1000",
            f"geschaetzt_{stromzaehler.pk}": "on",
        },
    )
    stand = stromzaehler.staende.get(datum=D(2025, 12, 31))
    assert stand.ist_geschaetzt
    for url in (
        _ablesung(objekt),
        reverse("zaehler:zaehler", args=[stromzaehler.pk]),
        reverse("zaehler:uebersicht"),
    ):
        seite = angemeldet.get(url).content.decode()
        assert '<span class="badge badge-warnung">geschätzt</span>' in seite


def test_ablesung_lehnt_zukunft_und_andere_dateien_ab(
    angemeldet: Client, objekt: Objekt, stromzaehler: Zaehler
) -> None:
    morgen = datetime.date.today() + datetime.timedelta(days=1)
    antwort = angemeldet.post(
        _ablesung(objekt), {"datum": morgen.isoformat(), f"stand_{stromzaehler.pk}": "5"}
    )
    assert "nicht in der Zukunft" in antwort.content.decode()
    pdf = SimpleUploadedFile("x.pdf", b"%PDF-1.4", content_type="application/pdf")
    antwort = angemeldet.post(
        _ablesung(objekt),
        {"datum": "2025-12-31", f"stand_{stromzaehler.pk}": "5", f"foto_{stromzaehler.pk}": pdf},
    )
    assert "Nur Fotos" in antwort.content.decode()
    assert stromzaehler.staende.count() == 1  # nur der Einbaustand


def test_zaehler_anlegen_und_wechseln_ueber_die_seiten(angemeldet: Client, garage: Einheit) -> None:
    """Abnahme Schritt 8: Zähler lassen sich anlegen und wechseln (über die Seiten)."""
    antwort = angemeldet.post(
        reverse("zaehler:neu"),
        {
            "nummer": "TZ-S-7",
            "art": "strom",
            "einheit": garage.pk,
            "einbaudatum": "2021-03-01",
            "eichfrist_bis": "2028-12-31",
            "einbau_stand": "0",
        },
    )
    zaehler = Zaehler.objects.get(nummer="TZ-S-7")
    assert antwort["Location"] == reverse("zaehler:zaehler", args=[zaehler.pk])
    einheit_seite = angemeldet.get(reverse("stammdaten:einheit", args=[garage.pk]))
    assert "TZ-S-7" in einheit_seite.content.decode()

    url = reverse("zaehler:zaehler", args=[zaehler.pk])
    antwort = angemeldet.post(
        url, {"aktion": "stand", "stand-datum": "2024-12-31", "stand-stand": "700"}
    )
    assert antwort.status_code == 302
    antwort = angemeldet.post(
        url,
        {
            "aktion": "wechsel",
            "wechsel-datum": "2025-06-15",
            "wechsel-ausbau_stand": "812,5",
            "wechsel-neue_nummer": "TZ-S-8",
            "wechsel-einbau_stand": "0",
            "wechsel-ausbau_foto": foto("a.png"),
        },
    )
    neuer = Zaehler.objects.get(nummer="TZ-S-8")
    assert antwort["Location"] == reverse("zaehler:zaehler", args=[neuer.pk])
    zaehler.refresh_from_db()
    assert zaehler.nachfolger == neuer and zaehler.ausgebaut_am == D(2025, 6, 15)
    seite = angemeldet.get(url).content.decode()
    assert "Ausgebaut am 15.06.2025" in seite and "TZ-S-8" in seite

    # Ein abgelehnter Wechsel erscheint als Meldung, nichts ändert sich.
    antwort = angemeldet.post(
        reverse("zaehler:zaehler", args=[neuer.pk]),
        {
            "aktion": "wechsel",
            "wechsel-datum": "2020-01-01",
            "wechsel-ausbau_stand": "1",
            "wechsel-neue_nummer": "TZ-S-9",
            "wechsel-einbau_stand": "0",
        },
        follow=True,
    )
    assert "der Ausbau muss der letzte Stand sein" in antwort.content.decode()
    assert not Zaehler.objects.filter(nummer="TZ-S-9").exists()


def test_seiten_brauchen_anmeldung(client: Client, stromzaehler: Zaehler, objekt: Objekt) -> None:
    for url in (
        reverse("zaehler:uebersicht"),
        reverse("zaehler:besuch"),
        reverse("zaehler:zaehler", args=[stromzaehler.pk]),
        _ablesung(objekt),
    ):
        assert client.get(url).status_code == 302
