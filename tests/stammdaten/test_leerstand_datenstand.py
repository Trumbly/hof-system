"""Leerstand (H2.11) und Seite „Datenstand“."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from apps.kern.models import Objekt
from apps.stammdaten import datenstand, leerstand, services
from apps.stammdaten.models import Einheit, EinheitStatus, Partei, Vertrag
from tests.stammdaten.conftest import garage_anlegen, vertrag_anlegen

pytestmark = pytest.mark.django_db
D = datetime.date


def test_leerstand_tage_frei_und_entgangener_ertrag(objekt: Objekt, mieter: Partei) -> None:
    """Schritt 4, Aufgabe 6: Leerstand je Einheit mit Tagen frei und entgangenem Ertrag nach
    Zielmiete, tageweise je Monat."""
    garage = garage_anlegen(objekt, zielmiete="93.00", frei_ab=D(2026, 4, 1))
    # April (30 Tage) frei, ab Mai vermietet: 30 Tage, 93,00 €
    vertrag_anlegen(garage, mieter, beginn=D(2026, 5, 1), status="aktiv")
    # Zweite Garage: ab 16.06. frei, Zielmiete ab 01.07. höher
    zweite = garage_anlegen(objekt, zielmiete="60.00", frei_ab=D(2026, 6, 16))
    services.zielmiete_setzen(zweite, Decimal("62.00"), D(2026, 7, 1))
    # Wohnraum zählt nie.
    services.einheit_anlegen(objekt, Einheit.Typ.WOHNUNG)

    zeilen = {z.einheit.nw_id: z for z in leerstand.berechnen(D(2026, 4, 1), D(2026, 7, 31))}

    assert set(zeilen) == {garage.nw_id, zweite.nw_id}
    assert (zeilen[garage.nw_id].tage_frei, zeilen[garage.nw_id].entgangen) == (
        30,
        Decimal("93.00"),
    )
    assert zeilen[garage.nw_id].frei_seit is None  # am Ende des Zeitraums vermietet
    # 15 Tage Juni à 2,00 € + 31 Tage Juli à 2,00 € = 92,00 €
    assert (zeilen[zweite.nw_id].tage_frei, zeilen[zweite.nw_id].entgangen) == (
        46,
        Decimal("92.00"),
    )
    assert zeilen[zweite.nw_id].frei_seit == D(2026, 6, 16)


def test_vertrag_deckt_frei_gemeldete_tage(objekt: Objekt, mieter: Partei) -> None:
    """Eine Rückgabe vor Vertragsende kostet keinen Ertrag: Der Vertrag deckt die Tage."""
    garage = garage_anlegen(objekt, zielmiete="31.00", frei_ab=D(2026, 1, 1))
    vertrag = vertrag_anlegen(garage, mieter, beginn=D(2026, 1, 1), status="aktiv")
    services.vertrag_aendern(vertrag, ende=D(2026, 3, 31))
    services.einheit_status_setzen(garage, EinheitStatus.Status.FREI, D(2026, 3, 20), "Rückgabe")

    (zeile,) = leerstand.berechnen(D(2026, 3, 1), D(2026, 3, 31) + datetime.timedelta(days=30))
    assert zeile.tage_frei == 30  # nur April
    assert zeile.entgangen == Decimal("31.00")


def test_tage_ohne_zielmiete(objekt: Objekt) -> None:
    lager = services.einheit_anlegen(
        objekt, Einheit.Typ.LAGER, status=EinheitStatus.Status.FREI, status_ab=D(2026, 1, 1)
    )
    (zeile,) = leerstand.berechnen(D(2026, 1, 1), D(2026, 1, 10))
    assert zeile.einheit == lager
    assert (zeile.tage_frei, zeile.tage_ohne_zielmiete, zeile.entgangen) == (
        10,
        10,
        Decimal("0.00"),
    )


def test_leerstand_seite_zeigt_entgangenen_ertrag(objekt: Objekt, angemeldet: Client) -> None:
    """Abnahme Schritt 4: Die Leerstand-Ansicht zeigt den entgangenen Ertrag je Einheit."""
    garage = garage_anlegen(objekt, zielmiete="62.00", frei_ab=D(2026, 1, 1))

    html = angemeldet.get(
        reverse("stammdaten:leerstand"), {"von": "2026-01-01", "bis": "2026-01-31"}
    ).content.decode()

    assert f'id="leerstand-{garage.nw_id}"' in html
    zeile = html.split(f'id="leerstand-{garage.nw_id}"', 1)[1].split("</tr>", 1)[0]
    assert ">31<" in zeile  # Tage frei
    assert "62,00 €" in zeile  # entgangen im Januar = Zielmiete
    assert 'id="summe">62,00 €' in html


# --- Datenstand ----------------------------------------------------------------------


def _luecken(bereich: str) -> dict[str, tuple[str, ...]]:
    for stand in datenstand.ermitteln(D(2026, 11, 1)):
        if stand.schluessel == bereich:
            return {luecke.kennung: tuple(f for f, _ in luecke.felder) for luecke in stand.luecken}
    raise AssertionError(bereich)


def test_datenstand_zaehlt_luecken_je_bereich(objekt: Objekt) -> None:
    """Schritt 4, Aufgabe 7: Zählung der Lücken je Bereich nach den Regeln des Bauplans."""
    voll = garage_anlegen(objekt)
    leer = services.einheit_anlegen(objekt, Einheit.Typ.GARAGE)
    services.einheit_anlegen(objekt, Einheit.Typ.WOHNUNG)  # nur NW-ID und Objekt: vollständig
    partei = services.partei_anlegen(vorname="Ohne", nachname="Kontakt")
    vertrag = services.vertrag_anlegen(
        einheiten=[voll],
        mieter=[partei],
        beginn=D(2026, 1, 1),
        miete=Decimal("80.00"),
        zahlweise="",
        kuendigungsregel="",
        status="aktiv",
    )
    services.schluessel_anlegen(voll, anzahl=2)  # wer sie hat: unbekannt

    assert _luecken("einheit") == {leer.nw_id: ("flaeche", "status", "zielmiete")}
    assert _luecken("partei") == {f"Partei #{partei.pk}": ("anschrift", "kontaktweg")}
    assert _luecken("vertrag") == {
        vertrag.nummer: ("zahlweise", "kuendigungsfrist", "kaution", "scan")
    }
    assert _luecken("schluessel") == {voll.nw_id: ("inhaber",), leer.nw_id: ("anzahl",)}
    services.kaution_aendern(vertrag.kaution, betrag=Decimal("160.00"))
    assert _luecken("kaution") == {f"Kaution {vertrag.nummer}": ("eingang", "konto")}
    assert datenstand.zaehler(D(2026, 11, 1)) == {
        "einheit": 3,
        "partei": 2,
        "vertrag": 3,
        "kaution": 2,
        "schluessel": 2,
    }


def test_datenstand_fuehrt_mit_einem_klick_zur_eingabe(objekt: Objekt, angemeldet: Client) -> None:
    """Abnahme Schritt 4: Der Datenstand zeigt fehlende Pflichtfelder und führt mit einem
    Klick zur Eingabe; nach dem Speichern ist die Lücke weg."""
    einheit = services.einheit_anlegen(
        objekt,
        Einheit.Typ.GARAGE,
        status=EinheitStatus.Status.FREI,
        status_ab=D(2026, 1, 1),
        zielmiete=Decimal("80"),
        zielmiete_ab=D(2026, 1, 1),
    )
    seite = angemeldet.get(reverse("stammdaten:datenstand")).content.decode()
    link = f"{reverse('stammdaten:einheit', args=[einheit.pk])}#stammfelder"
    assert f'href="{link}"' in seite
    assert "fehlt Fläche" in seite

    eingabe = angemeldet.get(link.split("#")[0]).content.decode()
    assert 'id="stammfelder"' in eingabe
    assert 'name="stammfelder-flaeche_qm"' in eingabe

    antwort = angemeldet.post(
        link.split("#")[0],
        {
            "aktion": "stammfelder",
            "stammfelder-typ": "garage",
            "stammfelder-objekt": objekt.pk,
            "stammfelder-bezeichnung": "",
            "stammfelder-flaeche_qm": "19.50",
            "stammfelder-vermarktung": "website",
            "stammfelder-website_slug": "",
            "stammfelder-notiz": "",
        },
    )
    assert antwort.status_code == 302
    einheit.refresh_from_db()
    assert einheit.flaeche_qm == Decimal("19.50")
    seite = angemeldet.get(reverse("stammdaten:datenstand")).content.decode()
    assert f'href="{link}"' not in seite


@pytest.mark.parametrize(
    ("bereich", "anker"),
    [("partei", "stammfelder"), ("vertrag", "kondition"), ("kaution", "kaution")],
)
def test_jede_luecke_hat_einen_link_zur_eingabe(
    bereich: str, anker: str, objekt: Objekt, angemeldet: Client
) -> None:
    einheit = garage_anlegen(objekt)
    partei = services.partei_anlegen(vorname="A", nachname="B")
    vertrag = services.vertrag_anlegen(
        einheiten=[einheit], mieter=[partei], beginn=D(2026, 1, 1), miete=None, status="aktiv"
    )
    services.kaution_aendern(vertrag.kaution, betrag=Decimal("100.00"))
    (luecke,) = next(b for b in datenstand.ermitteln() if b.schluessel == bereich).luecken
    assert luecke.url.endswith(f"#{anker}")
    seite = angemeldet.get(luecke.url.split("#")[0])
    assert seite.status_code == 200
    assert f'id="{anker}"' in seite.content.decode()
    assert vertrag.status == Vertrag.Status.AKTIV
