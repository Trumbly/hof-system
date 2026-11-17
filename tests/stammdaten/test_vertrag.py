"""Vertrag mit Parteien, Konditionen und Kaution (H1.3); Mietänderung ab Datum."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from apps.kern.models import Objekt
from apps.stammdaten import services
from apps.stammdaten.models import Einheit, Kondition, Partei, Vertrag, Vertragspartei
from tests.stammdaten.conftest import BASIS, garage_anlegen, mieter_anlegen, vertrag_anlegen

pytestmark = pytest.mark.django_db
D = datetime.date


def test_vertrag_anlegen_mit_standardwerten(garage: Einheit, mieter: Partei) -> None:
    """Schritt 4, Aufgabe 1: Vertrag mit Vertragsart, Einheiten, Beginn, Kündigungsfrist,
    Zahlweise, Mandant und Status Entwurf; Kondition und Kaution hängen daran."""
    zahler = mieter_anlegen("Zahlerin")
    vertrag = services.vertrag_anlegen(
        einheiten=[garage],
        mieter=[mieter],
        zahler=[zahler],
        beginn=BASIS,
        miete=Decimal("85.00"),
        kaution=Decimal("170.00"),
    )

    assert vertrag.nummer == "V-0001"
    assert vertrag.vertragsart == Vertrag.Art.G
    assert vertrag.mandant == garage.objekt.mandant
    assert vertrag.status == Vertrag.Status.ENTWURF
    assert vertrag.kuendigungsregel == "monat_580a"
    assert list(vertrag.einheiten.all()) == [garage]
    assert vertrag.parteien_mit_rolle(Vertragspartei.Rolle.MIETER) == [mieter]
    assert vertrag.parteien_mit_rolle(Vertragspartei.Rolle.ZAHLER) == [zahler]
    kondition = vertrag.kondition_am(BASIS)
    assert kondition is not None
    assert (kondition.miete, kondition.zahlweise) == (Decimal("85.00"), "monatlich")
    assert vertrag.kaution.betrag == Decimal("170.00")
    # Ein Entwurf ändert den Status der Einheit nicht; erst die Übergabe vermietet.
    assert garage.status_am(BASIS).status == "frei"  # type: ignore[union-attr]


def test_halle_bekommt_quartalsfrist(objekt: Objekt, mieter: Partei) -> None:
    halle = services.einheit_anlegen(objekt, Einheit.Typ.HALLE)
    vertrag = vertrag_anlegen(halle, mieter)
    assert (vertrag.vertragsart, vertrag.kuendigungsregel) == ("H", "quartal_580a")


def test_bestandsvertrag_vermietet_die_einheit(garage: Einheit, mieter: Partei) -> None:
    vertrag_anlegen(garage, mieter, beginn=D(2026, 3, 1), status="aktiv")
    assert garage.status_am(D(2026, 2, 28)).status == "frei"  # type: ignore[union-attr]
    assert garage.status_am(D(2026, 3, 1)).status == "vermietet"  # type: ignore[union-attr]


def test_einheit_nicht_doppelt_vermieten(garage: Einheit, mieter: Partei) -> None:
    erster = vertrag_anlegen(garage, mieter, beginn=D(2026, 3, 1), status="aktiv")
    with pytest.raises(services.StammdatenFehler, match=erster.nummer):
        vertrag_anlegen(garage, mieter_anlegen("Zweite"), beginn=D(2026, 6, 1))

    services.vertrag_aendern(erster, ende=D(2026, 5, 31))
    zweiter = vertrag_anlegen(garage, mieter_anlegen("Dritte"), beginn=D(2026, 6, 1))
    assert zweiter.status == Vertrag.Status.ENTWURF


def test_mietaenderung_gilt_ab_datum_alter_stand_bleibt_sichtbar(
    aktiver_vertrag: Vertrag, angemeldet: Client
) -> None:
    """Abnahme Schritt 4: Eine Mietänderung gilt ab Datum, der alte Stand bleibt sichtbar
    (im Datenbestand und auf der Vertragsseite)."""
    neu = services.kondition_ab(
        aktiver_vertrag, D(2026, 9, 1), miete=Decimal("92.50"), grund="Mieterhöhung"
    )

    alt = aktiver_vertrag.kondition_am(D(2026, 8, 31))
    assert alt is not None
    assert (alt.miete, alt.gueltig_ab, alt.gueltig_bis) == (
        Decimal("85.00"),
        D(2026, 2, 1),
        D(2026, 8, 31),
    )
    assert aktiver_vertrag.kondition_am(D(2026, 9, 1)) == neu
    assert (neu.miete, neu.gueltig_bis, neu.zahlweise) == (Decimal("92.50"), None, "monatlich")
    assert aktiver_vertrag.konditionen.count() == 2

    html = angemeldet.get(reverse("stammdaten:vertrag", args=[aktiver_vertrag.pk])).content.decode()
    assert "85,00 €" in html and "92,50 €" in html
    assert "31.08.2026" in html and "01.09.2026" in html


def test_mietaenderung_ueber_die_seite(aktiver_vertrag: Vertrag, angemeldet: Client) -> None:
    """Schritt 4, Aufgabe 1: Die Seite übernimmt eine neue Miete ab Datum über den Service."""
    url = reverse("stammdaten:vertrag", args=[aktiver_vertrag.pk])
    antwort = angemeldet.post(
        url,
        {
            "aktion": "kondition",
            "kondition-gueltig_ab": "2026-10-01",
            "kondition-miete": "99.00",
            "kondition-zahlweise": "jaehrlich",
            "kondition-grund": "Neuer Vertragsstand",
        },
    )
    assert antwort.status_code == 302
    assert antwort["Location"] == f"{url}#kondition"
    neu = aktiver_vertrag.kondition_am(D(2026, 10, 1))
    assert neu is not None
    assert (neu.miete, neu.zahlweise) == (Decimal("99.00"), "jaehrlich")
    assert aktiver_vertrag.kondition_am(D(2026, 9, 30)).miete == Decimal("85.00")  # type: ignore[union-attr]


def test_staffel_als_mehrere_zeilen(aktiver_vertrag: Vertrag) -> None:
    """Schritt 4, Aufgabe 1: Staffeln sind mehrere Konditionen mit Gültig-ab; eine nachträglich
    eingefügte Änderung endet vor der nächsten Stufe."""
    services.kondition_ab(aktiver_vertrag, D(2027, 2, 1), miete=Decimal("95.00"), grund="Staffel 2")
    services.kondition_ab(aktiver_vertrag, D(2026, 8, 1), pauschale=Decimal("5.00"))

    zeilen = [
        (k.gueltig_ab, k.gueltig_bis, k.miete, k.pauschale)
        for k in aktiver_vertrag.konditionen.order_by("gueltig_ab")
    ]
    assert zeilen == [
        (D(2026, 2, 1), D(2026, 7, 31), Decimal("85.00"), Decimal("0.00")),
        (D(2026, 8, 1), D(2027, 1, 31), Decimal("85.00"), Decimal("5.00")),
        (D(2027, 2, 1), None, Decimal("95.00"), Decimal("0.00")),
    ]


def test_kondition_vor_beginn_abgelehnt(aktiver_vertrag: Vertrag) -> None:
    with pytest.raises(services.StammdatenFehler):
        services.kondition_ab(aktiver_vertrag, D(2025, 1, 1), miete=Decimal("1.00"))
    with pytest.raises(services.StammdatenFehler):
        services.kondition_ab(aktiver_vertrag, D(2026, 2, 1), miete=Decimal("1.00"))
    with pytest.raises(services.StammdatenFehler):
        services.kondition_ab(aktiver_vertrag, D(2026, 5, 1), zahlweise="woechentlich")


def test_unbekannte_kuendigungsregel_abgelehnt(garage: Einheit, mieter: Partei) -> None:
    with pytest.raises(services.StammdatenFehler):
        services.vertrag_anlegen(
            einheiten=[garage], mieter=[mieter], beginn=BASIS, miete=None, kuendigungsregel="x"
        )


def test_letzter_mieter_bleibt(entwurf: Vertrag) -> None:
    vp = entwurf.vertragsparteien.get()
    with pytest.raises(services.StammdatenFehler):
        services.vertragspartei_entfernen(vp)


def test_vertrag_ueber_zwei_einheiten(objekt: Objekt, mieter: Partei) -> None:
    a, b = garage_anlegen(objekt), garage_anlegen(objekt)
    vertrag = services.vertrag_anlegen(
        einheiten=[a, b], mieter=[mieter], beginn=BASIS, miete=Decimal("150.00"), status="aktiv"
    )
    assert set(vertrag.einheiten.all()) == {a, b}
    assert Kondition.objects.filter(vertrag=vertrag).count() == 1
