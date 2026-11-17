"""Einheit mit NW-ID, Status und Zielmiete als Zeitreihe; Wohnraum nur als Verweis (H1.1, H2.1)."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from apps.kern.models import Objekt
from apps.stammdaten import services
from apps.stammdaten.models import HINWEIS_VERWALTIO, Einheit, EinheitStatus, Partei
from tests.stammdaten.conftest import BASIS, vertrag_anlegen

pytestmark = pytest.mark.django_db
D = datetime.date


def test_nw_id_mit_typkuerzel_und_laufender_nummer(objekt: Objekt) -> None:
    """Schritt 4, Aufgabe 1: Das Hof-System vergibt die NW-ID im Format NW-<Typ>-<Nummer>,
    je Typ fortlaufend."""
    g1 = services.einheit_anlegen(objekt, Einheit.Typ.GARAGE)
    g2 = services.einheit_anlegen(objekt, Einheit.Typ.GARAGE)
    garten = services.einheit_anlegen(objekt, Einheit.Typ.GARTEN)
    wohnung = services.einheit_anlegen(objekt, Einheit.Typ.WOHNUNG)

    assert (g1.nw_id, g2.nw_id) == ("NW-G-0001", "NW-G-0002")
    assert garten.nw_id == "NW-GA-0001"
    assert wohnung.nw_id == "NW-W-0001"
    # Eine importierte ID mit höherer Nummer wird übersprungen, keine ID doppelt vergeben.
    services.einheit_anlegen(objekt, Einheit.Typ.GARAGE, nw_id="NW-G-0010")
    assert services.naechste_nw_id(Einheit.Typ.GARAGE) == "NW-G-0011"


def test_nw_id_bleibt_bei_aenderung(objekt: Objekt) -> None:
    einheit = services.einheit_anlegen(objekt, Einheit.Typ.GARAGE)
    services.einheit_aendern(einheit, typ=Einheit.Typ.LAGER, flaeche_qm=Decimal("30.00"))
    einheit.refresh_from_db()
    assert einheit.nw_id == "NW-G-0001"
    assert einheit.typ == Einheit.Typ.LAGER


def test_wohnraum_nur_mit_nw_id_und_hinweis(objekt: Objekt) -> None:
    """Schritt 4, Aufgabe 2: Einheiten vom Typ Wohnung existieren nur mit NW-ID und dem
    Hinweis „geführt in Verwaltio“, ohne Status, Zielmiete und Fläche."""
    wohnung = services.einheit_anlegen(objekt, Einheit.Typ.WOHNUNG, bezeichnung="Wohnung OG")

    assert wohnung.ist_wohnraum
    assert wohnung.hinweis == HINWEIS_VERWALTIO == "geführt in Verwaltio"
    with pytest.raises(services.WohnraumNichtHier):
        services.einheit_status_setzen(wohnung, EinheitStatus.Status.FREI, BASIS)
    with pytest.raises(services.WohnraumNichtHier):
        services.zielmiete_setzen(wohnung, Decimal("700"), BASIS)
    with pytest.raises(services.WohnraumNichtHier):
        services.einheit_aendern(wohnung, flaeche_qm=Decimal("70"))
    with pytest.raises(services.WohnraumNichtHier):
        services.einheit_anlegen(
            objekt, Einheit.Typ.WOHNUNG, zielmiete=Decimal("1"), zielmiete_ab=BASIS
        )
    with pytest.raises(services.WohnraumNichtHier):
        services.schluessel_anlegen(wohnung)


def test_vertrag_auf_wohnraum_wird_abgelehnt(objekt: Objekt, mieter: Partei) -> None:
    """Schritt 4, Aufgabe 2: Ein Vertrag auf eine Wohnung wird abgelehnt (Wohnraum führt
    Verwaltio)."""
    wohnung = services.einheit_anlegen(objekt, Einheit.Typ.WOHNUNG)

    with pytest.raises(services.WohnraumNichtHier, match="NW-W-0001"):
        vertrag_anlegen(wohnung, mieter)
    assert not wohnung.vertraege.exists()


def test_einheit_mit_vertrag_wird_nicht_zu_wohnraum(garage: Einheit, mieter: Partei) -> None:
    vertrag_anlegen(garage, mieter)
    with pytest.raises(services.WohnraumNichtHier):
        services.einheit_aendern(garage, typ=Einheit.Typ.WOHNUNG)


def test_status_ab_datum_alter_stand_bleibt(garage: Einheit) -> None:
    """Schritt 4, Aufgabe 1 (CLAUDE.md, Regel 4.3): Der Status hat Gültig-ab und Gültig-bis;
    ein neuer Status endet den alten am Vortag, der alte bleibt lesbar."""
    services.einheit_status_setzen(garage, EinheitStatus.Status.GESPERRT, D(2026, 5, 1), "Dach")

    staende = list(garage.statusstaende.order_by("gueltig_ab"))
    assert [(s.status, s.gueltig_ab, s.gueltig_bis) for s in staende] == [
        ("frei", BASIS, D(2026, 4, 30)),
        ("gesperrt", D(2026, 5, 1), None),
    ]
    assert garage.status_am(D(2026, 4, 30)).status == "frei"  # type: ignore[union-attr]
    assert garage.status_am(D(2026, 5, 1)).status == "gesperrt"  # type: ignore[union-attr]
    assert garage.status_am(D(2025, 12, 31)) is None
    with pytest.raises(services.StammdatenFehler):
        services.einheit_status_setzen(garage, EinheitStatus.Status.FREI, D(2026, 5, 1))


def test_zielmiete_mit_datum_und_begruendung(garage: Einheit) -> None:
    """Schritt 4, Aufgabe 1 (H2.1): Die Zielmiete ist ein Feld mit Datum und Begründung; eine
    neue gilt ab Datum, die alte bleibt sichtbar."""
    services.zielmiete_setzen(garage, Decimal("95.00"), D(2026, 7, 1), "Nachfrage gestiegen")

    alte, neue = garage.zielmieten.order_by("gueltig_ab")
    assert (alte.betrag, alte.gueltig_bis) == (Decimal("90.00"), D(2026, 6, 30))
    assert (neue.betrag, neue.gueltig_bis, neue.begruendung) == (
        Decimal("95.00"),
        None,
        "Nachfrage gestiegen",
    )
    assert garage.zielmiete_am(D(2026, 6, 30)) == alte


def test_einfuegen_vor_spaeterem_stand(garage: Einheit) -> None:
    """Ein Stand zwischen zwei vorhandenen gilt bis zum Tag vor dem späteren."""
    services.zielmiete_setzen(garage, Decimal("100.00"), D(2026, 9, 1))
    zwischen = services.zielmiete_setzen(garage, Decimal("95.00"), D(2026, 5, 1))

    assert zwischen.gueltig_bis == D(2026, 8, 31)
    assert [z.gueltig_bis for z in garage.zielmieten.order_by("gueltig_ab")] == [
        D(2026, 4, 30),
        D(2026, 8, 31),
        None,
    ]


def test_aufloesen_nur_ohne_gebundenen_vertrag(garage: Einheit, mieter: Partei) -> None:
    """H1.1: Eine Einheit wird aufgelöst, nicht gelöscht; ein laufender Vertrag verhindert das."""
    vertrag = vertrag_anlegen(garage, mieter, status="aktiv")
    with pytest.raises(services.StammdatenFehler, match=vertrag.nummer):
        services.einheit_aufloesen(garage, D(2026, 6, 1))

    lager = services.einheit_anlegen(garage.objekt, Einheit.Typ.LAGER)
    services.einheit_aufloesen(lager, D(2026, 6, 1), "Umbau")
    lager.refresh_from_db()
    assert lager.aufgeloest_am == D(2026, 6, 1)
    assert lager.status_am(D(2026, 6, 1)).status == "aufgeloest"  # type: ignore[union-attr]
