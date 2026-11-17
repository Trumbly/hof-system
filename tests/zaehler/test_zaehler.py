"""Zähler anlegen und wechseln (H7.1), Stände erfassen (H7.2), Eichfrist."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from apps.fristen import services as fristen
from apps.fristen.models import Frist
from apps.kern import services as kern
from apps.kern.models import Objekt
from apps.stammdaten import services as stammdaten
from apps.stammdaten.models import Einheit
from apps.zaehler import konfiguration, services
from apps.zaehler.models import Zaehler, Zaehlerstand
from tests.zaehler.conftest import foto

pytestmark = pytest.mark.django_db
D = datetime.date


def test_zaehler_anlegen_an_einheit_oder_objekt(garage: Einheit, objekt: Objekt) -> None:
    """Abnahme Schritt 8: Zähler lassen sich anlegen, an einer Einheit oder als Hauptzähler
    an einem Objekt (genau eins), mit Einbaustand und Eichfrist im Fristenregister."""
    zaehler = services.zaehler_anlegen(
        "TZ-S-1",
        "strom",
        einheit=garage,
        einbaudatum=D(2021, 3, 1),
        eichfrist_bis=D(2028, 12, 31),
        einbau_stand="12,5",
    )
    haupt = services.zaehler_anlegen("TZ-W-1", "wasser", objekt=objekt, bezeichnung="Keller")

    (einbau,) = zaehler.staende.all()
    assert (einbau.art, einbau.datum, einbau.stand) == ("einbau", D(2021, 3, 1), Decimal("12.5"))
    assert zaehler.ort == garage.nw_id and haupt.ort == "Objekt HOF"
    assert zaehler.standort_objekt == haupt.standort_objekt == objekt
    (frist,) = fristen.fristen_zu(zaehler, nur_offen=True)
    assert (frist.art, frist.quelle, frist.datum) == (
        Frist.Art.EICHFRIST,
        Frist.Quelle.ZAEHLER,
        D(2028, 12, 31),
    )
    assert frist.vorlauf_tage == konfiguration.laden().eichfrist_vorlauf_tage
    assert not fristen.fristen_zu(haupt).exists()  # ohne Eichfrist keine Frist

    with pytest.raises(services.ZaehlerFehler, match="genau einer"):
        services.zaehler_anlegen("TZ-X", "strom", einheit=garage, objekt=objekt)
    with pytest.raises(services.ZaehlerFehler, match="genau einer"):
        services.zaehler_anlegen("TZ-X", "strom")
    with pytest.raises(services.ZaehlerFehler, match="gibt es schon"):
        services.zaehler_anlegen("TZ-S-1", "strom", objekt=objekt)
    with pytest.raises(services.ZaehlerFehler, match="Unbekannte Art"):
        services.zaehler_anlegen("TZ-X", "oel", objekt=objekt)
    # Die Datenbank sichert „genau eins von Einheit und Objekt“ auch an den Services vorbei.
    with pytest.raises(IntegrityError), transaction.atomic():
        Zaehler.objects.filter(pk=haupt.pk).update(einheit=garage)


def test_kein_zaehler_an_wohnraum(objekt: Objekt) -> None:
    """Wohnraum führt Verwaltio: kein Zähler im Hof-System (H7, E2)."""
    wohnung = stammdaten.einheit_anlegen(objekt, Einheit.Typ.WOHNUNG)
    with pytest.raises(services.ZaehlerFehler, match="Wohnraum"):
        services.zaehler_anlegen("TZ-W-9", "wasser", einheit=wohnung)


def test_eichfrist_aendern_verschiebt_die_frist(stromzaehler: Zaehler) -> None:
    services.zaehler_aendern(stromzaehler, eichfrist_bis=D(2029, 12, 31), bezeichnung="rechts")
    (frist,) = fristen.fristen_zu(stromzaehler, nur_offen=True)
    assert frist.datum == D(2029, 12, 31)
    assert frist.verschiebungen.count() == 1
    with pytest.raises(services.ZaehlerFehler, match="nicht ändern"):
        services.zaehler_aendern(stromzaehler, art="gas")
    with pytest.raises(services.ZaehlerFehler, match="Stände vor dem"):
        services.zaehler_aendern(stromzaehler, einbaudatum=D(2022, 1, 1))


def test_kleinerer_stand_wird_abgelehnt(stromzaehler: Zaehler) -> None:
    """Abnahme Schritt 8: Ein kleinerer Stand als der letzte wird abgelehnt."""
    services.stand_erfassen(stromzaehler, D(2024, 12, 31), "812,4")
    with pytest.raises(services.StandUnplausibel, match=r"kleiner als der letzte Stand 812\.4"):
        services.stand_erfassen(stromzaehler, D(2025, 12, 31), "800")
    # Ein späterer Stand begrenzt auch nach oben, wenn ein älterer nachgetragen wird.
    services.stand_erfassen(stromzaehler, D(2025, 12, 31), "1020.7")
    with pytest.raises(services.StandUnplausibel, match="größer als der spätere Stand"):
        services.stand_erfassen(stromzaehler, D(2025, 6, 30), "1100")
    services.stand_erfassen(stromzaehler, D(2025, 6, 30), "900")
    assert [s.stand_text for s in stromzaehler.staende.order_by("datum")] == [
        "0",
        "812.4",
        "900",
        "1020.7",
    ]
    with pytest.raises(services.ZaehlerFehler, match="erst seit"):
        services.stand_erfassen(stromzaehler, D(2020, 1, 1), "0")
    with pytest.raises(services.ZaehlerFehler, match="keine Zahl"):
        services.stand_erfassen(stromzaehler, D(2026, 1, 5), "viel")
    with pytest.raises(services.ZaehlerFehler, match="≥ 0"):
        services.stand_erfassen(stromzaehler, D(2026, 1, 5), "-1")


def test_zweiter_stand_am_selben_tag_korrigiert(stromzaehler: Zaehler) -> None:
    erster = services.stand_erfassen(stromzaehler, D(2025, 12, 31), "1000")
    zweiter = services.stand_erfassen(
        stromzaehler, D(2025, 12, 31), "1001,5", art="geschaetzt", vermerk="linear geschätzt"
    )
    assert zweiter.pk == erster.pk
    assert (zweiter.stand, zweiter.ist_geschaetzt) == (Decimal("1001.5"), True)
    # Den Einbaustand korrigiert nur ein neuer Zähler, keine Ablesung.
    with pytest.raises(services.ZaehlerFehler, match="Einbau"):
        services.stand_erfassen(stromzaehler, D(2021, 3, 1), "3")
    with pytest.raises(services.ZaehlerFehler, match="nur beim Einbau"):
        services.stand_erfassen(stromzaehler, D(2025, 1, 1), "500", art="ausbau")


def test_schaetzwerte_tragen_ein_kennzeichen(stromzaehler: Zaehler) -> None:
    """Schritt 8, Aufgabe 3 (H7.2): Geschätzte Werte tragen ein Kennzeichen."""
    stand = services.stand_erfassen(
        stromzaehler, D(2025, 12, 31), "1000", art="geschaetzt", vermerk="linear geschätzt"
    )
    assert stand.ist_geschaetzt
    assert stand.get_art_display() == "geschätzt"
    assert stand.vermerk == "linear geschätzt"


def test_zaehlerwechsel_mit_beiden_staenden_in_einem_vorgang(stromzaehler: Zaehler) -> None:
    """Abnahme Schritt 8 (H7.1): Zählerwechsel mit Ausbau- und Einbaustand und Datum in einem
    Vorgang; danach gilt „nicht kleiner als der letzte Stand“ nur für den neuen Zähler."""
    services.stand_erfassen(stromzaehler, D(2024, 12, 31), "5000")

    wechsel = services.zaehler_wechseln(
        stromzaehler,
        D(2025, 6, 15),
        "5412,3",
        neue_nummer="TZ-S-2",
        einbau_stand="0",
        eichfrist_bis=D(2033, 12, 31),
        ausbau_foto=foto("alt.png"),
        einbau_foto=foto("neu.png"),
    )

    alter, neuer = wechsel.alter, wechsel.neuer
    alter.refresh_from_db()
    assert alter.ausgebaut_am == D(2025, 6, 15) and alter.nachfolger == neuer
    assert (wechsel.ausbau.art, wechsel.ausbau.stand) == ("ausbau", Decimal("5412.3"))
    assert (wechsel.einbau.art, wechsel.einbau.stand) == ("einbau", Decimal("0"))
    assert wechsel.ausbau.foto and wechsel.einbau.foto
    assert (neuer.einheit, neuer.art, neuer.einbaudatum) == (
        alter.einheit,
        alter.art,
        D(2025, 6, 15),
    )
    assert neuer.bezeichnung == alter.bezeichnung
    # Eichfrist: die alte ist erledigt, die neue offen.
    assert not fristen.fristen_zu(alter, nur_offen=True).exists()
    assert fristen.fristen_zu(neuer, nur_offen=True).get().datum == D(2033, 12, 31)

    # Der kleinere Stand gehört an den neuen Zähler und wird dort angenommen.
    stand = services.stand_erfassen(neuer, D(2025, 12, 31), "312")
    assert stand.stand == Decimal("312")
    with pytest.raises(services.StandUnplausibel):
        services.stand_erfassen(neuer, D(2026, 1, 10), "311")
    # Am alten Zähler gibt es nach dem Ausbau keinen Stand mehr.
    with pytest.raises(services.ZaehlerFehler, match="Stände gehören an Zähler TZ-S-2"):
        services.stand_erfassen(alter, D(2025, 12, 31), "6000")
    with pytest.raises(services.ZaehlerFehler, match="schon ausgebaut"):
        services.zaehler_wechseln(
            alter, D(2026, 1, 1), "7000", neue_nummer="TZ-S-3", einbau_stand="0"
        )


def test_wechsel_ist_alles_oder_nichts(stromzaehler: Zaehler) -> None:
    """H7.1: Ein Wechsel mit unpassendem Ausbaustand oder doppelter Nummer ändert nichts."""
    services.stand_erfassen(stromzaehler, D(2024, 12, 31), "5000")
    with pytest.raises(services.StandUnplausibel):
        services.zaehler_wechseln(
            stromzaehler, D(2025, 6, 15), "4000", neue_nummer="TZ-S-2", einbau_stand="0"
        )
    with pytest.raises(services.ZaehlerFehler, match="gibt es schon"):
        services.zaehler_wechseln(
            stromzaehler, D(2025, 6, 15), "5500", neue_nummer="TZ-S-1", einbau_stand="0"
        )
    stromzaehler.refresh_from_db()
    assert stromzaehler.ist_eingebaut and stromzaehler.nachfolger is None
    assert Zaehler.objects.count() == 1
    assert not stromzaehler.staende.filter(art=Zaehlerstand.Art.AUSBAU).exists()
    assert fristen.fristen_zu(stromzaehler, nur_offen=True).exists()


def test_ablesung_laesst_sich_zuruecknehmen(stromzaehler: Zaehler) -> None:
    """CLAUDE.md, Regel 4.4: Jede Änderung steht im Protokoll und lässt sich zurücknehmen."""
    stand = services.stand_erfassen(stromzaehler, D(2025, 12, 31), "1000")
    services.stand_erfassen(stromzaehler, D(2025, 12, 31), "1100")
    ereignis = Zaehlerstand.pgh_event_model.objects.filter(  # type: ignore[attr-defined]
        pgh_obj_id=stand.pk, pgh_label="update"
    ).get()
    kern.zuruecknehmen(f"zaehler.ZaehlerstandEvent:{ereignis.pgh_id}")
    stand.refresh_from_db()
    assert stand.stand == Decimal("1000")
