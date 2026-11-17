"""Übergabe und Rückgabe (Schritt 4) erfassen Zählerstände über ``zaehler.services``;
Datenmigration aus dem früheren JSONField."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.kern.models import Objekt
from apps.stammdaten import services as stammdaten
from apps.stammdaten.models import Einheit, Partei, Uebergabe, Vertrag
from apps.zaehler import services
from apps.zaehler.models import Zaehler
from tests.stammdaten.conftest import mieter_anlegen, vertrag_anlegen
from tests.zaehler.conftest import foto

D = datetime.date


@pytest.fixture
def mieter(db: None) -> Partei:
    return mieter_anlegen()


@pytest.fixture
def aktiv(garage: Einheit, mieter: Partei) -> Vertrag:
    vertrag = vertrag_anlegen(garage, mieter, beginn=D(2024, 2, 1), status="aktiv")
    stammdaten.kaution_eingang_erfassen(vertrag.kaution, D(2024, 1, 25), "Vivid Kautionen")
    return vertrag


@pytest.mark.django_db
def test_rueckgabe_erfasst_staende_ueber_den_zaehlerdienst(
    aktiv: Vertrag, stromzaehler: Zaehler, garage: Einheit
) -> None:
    """Schritt 8, Aufgabe 5: Übergabe und Rückgabe erfassen Zählerstände über dieselbe Funktion
    wie die Ablesung, mit Kennzeichen „bei Übergabe“ und Bezug auf die Rückgabe."""
    services.stand_erfassen(stromzaehler, D(2024, 12, 31), "500")
    rueckgabe = stammdaten.uebergabe_beginnen(aktiv, Uebergabe.Art.RUECKGABE, D(2025, 9, 30))
    assert services.zaehler_der_uebergabe(rueckgabe) == [stromzaehler]

    with pytest.raises(stammdaten.StammdatenFehler, match="kleiner als der letzte"):
        stammdaten.uebergabe_speichern(
            rueckgabe, zaehlerstaende=[{"zaehler": stromzaehler.pk, "stand": "400"}]
        )
    stammdaten.uebergabe_speichern(
        rueckgabe,
        zaehlerstaende=[{"zaehler": stromzaehler, "stand": "612,8", "foto": foto()}],
    )
    # Erneutes Speichern korrigiert, legt keinen zweiten Stand an.
    stammdaten.uebergabe_speichern(rueckgabe, zaehlerstaende=[{"nummer": "TZ-S-1", "stand": "613"}])
    # Ein späteres Datum der Rückgabe nimmt die Stände mit.
    stammdaten.uebergabe_speichern(rueckgabe, datum=D(2025, 10, 1))
    stammdaten.uebergabe_abschliessen(rueckgabe)

    (stand,) = rueckgabe.zaehlerstaende.all()
    assert (stand.zaehler, stand.datum, stand.stand) == (stromzaehler, D(2025, 10, 1), Decimal(613))
    assert stand.bei_uebergabe and stand.foto
    assert stromzaehler.letzter_stand() == stand
    with pytest.raises(stammdaten.StammdatenFehler, match="abgeschlossen"):
        stammdaten.uebergabe_speichern(
            rueckgabe, zaehlerstaende=[{"zaehler": stromzaehler.pk, "stand": "700"}]
        )


@pytest.mark.django_db
def test_nur_zaehler_der_einheiten_des_vertrags(
    aktiv: Vertrag, stromzaehler: Zaehler, objekt: Objekt
) -> None:
    fremd = services.zaehler_anlegen("TZ-W-1", "wasser", objekt=objekt)
    rueckgabe = stammdaten.uebergabe_beginnen(aktiv, Uebergabe.Art.RUECKGABE, D(2025, 9, 30))
    with pytest.raises(stammdaten.StammdatenFehler, match="gehört nicht zu diesem Vorgang"):
        stammdaten.uebergabe_speichern(
            rueckgabe, zaehlerstaende=[{"zaehler": fremd.pk, "stand": "5"}]
        )
    assert not fremd.staende.exists()


# --- Datenmigration -------------------------------------------------------------------

VORHER = [("stammdaten", "0001_initial"), ("zaehler", "0001_initial")]


@pytest.mark.django_db(transaction=True)
def test_datenmigration_uebernimmt_json_eintraege_mit_bekanntem_zaehler() -> None:
    """Schritt 8, Aufgabe 5: Die Migration übernimmt vorhandene JSON-Einträge der Übergabe in
    Zählerstände, soweit der Zähler per Nummer gefunden wird; danach ist das Feld entfernt."""
    executor = MigrationExecutor(connection)
    ziel = executor.loader.graph.leaf_nodes()
    executor.migrate(VORHER)
    alt = executor.loader.project_state(VORHER).apps
    try:
        mandant = alt.get_model("kern", "Mandant").objects.create(
            art="gbr", kuerzel="GBR", bezeichnung="Test-GbR"
        )
        objekt = alt.get_model("kern", "Objekt").objects.create(
            mandant=mandant, kuerzel="HOF", bezeichnung="Hof"
        )
        einheit = alt.get_model("stammdaten", "Einheit").objects.create(
            nw_id="NW-G-0001", typ="garage", objekt=objekt
        )
        vertrag = alt.get_model("stammdaten", "Vertrag").objects.create(
            nummer="G-0001", vertragsart="G", mandant=mandant, beginn=D(2024, 1, 1)
        )
        zaehler = alt.get_model("zaehler", "Zaehler").objects.create(
            nummer="Z-7", art="strom", einheit=einheit
        )
        uebergabe = alt.get_model("stammdaten", "Uebergabe").objects.create(
            art="uebergabe",
            vertrag=vertrag,
            datum=D(2024, 1, 1),
            zaehlerstaende=[
                {"nummer": "Z-7", "stand": "123.4"},
                {"nummer": "Z-unbekannt", "stand": "5"},
                {"nummer": "Z-7", "stand": "kaputt"},
            ],
        )

        executor = MigrationExecutor(connection)
        executor.migrate(ziel)
    finally:
        MigrationExecutor(connection).migrate(
            MigrationExecutor(connection).loader.graph.leaf_nodes()
        )

    neu = Zaehler.objects.get(pk=zaehler.pk)
    (stand,) = neu.staende.all()
    assert (stand.datum, stand.stand, stand.art) == (D(2024, 1, 1), Decimal("123.4"), "abgelesen")
    assert stand.bei_uebergabe and stand.uebergabe_id == uebergabe.pk
    assert Uebergabe.objects.get(pk=uebergabe.pk).zaehlerstaende.get() == stand
    assert "zaehlerstaende" not in {f.column for f in Uebergabe._meta.concrete_fields}


@pytest.mark.django_db
def test_zaehler_und_soll_laufen_beim_abschluss_in_einer_transaktion(
    aktiv: Vertrag, stromzaehler: Zaehler, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Schritt 8 mit Schritt 5: Die Rückgabe ruft Zählerdienst und Sollstellung in derselben
    Transaktion auf. Scheitert die Sollstellung, bleibt alles wie vor dem Abschluss."""
    from apps.soll import services as soll

    rueckgabe = stammdaten.uebergabe_beginnen(aktiv, Uebergabe.Art.RUECKGABE, D(2025, 9, 30))
    stammdaten.uebergabe_speichern(
        rueckgabe, zaehlerstaende=[{"zaehler": stromzaehler.pk, "stand": "612"}]
    )
    echt = soll.vertrag_geaendert

    def scheitert(vertrag: Vertrag) -> None:
        raise RuntimeError("Sollstellung gescheitert")

    monkeypatch.setattr(soll, "vertrag_geaendert", scheitert)
    with pytest.raises(RuntimeError):
        stammdaten.uebergabe_abschliessen(rueckgabe)
    rueckgabe.refresh_from_db()
    aktiv.refresh_from_db()
    assert rueckgabe.status == Uebergabe.Status.BEGONNEN
    assert aktiv.status == Vertrag.Status.AKTIV
    assert rueckgabe.zaehlerstaende.get().stand == Decimal(612)

    aufgerufen: list[Vertrag] = []

    def merken(vertrag: Vertrag) -> None:
        aufgerufen.append(vertrag)
        echt(vertrag)

    monkeypatch.setattr(soll, "vertrag_geaendert", merken)
    stammdaten.uebergabe_abschliessen(rueckgabe)
    rueckgabe.refresh_from_db()
    assert rueckgabe.status == Uebergabe.Status.ABGESCHLOSSEN
    assert aufgerufen and aufgerufen[-1].pk == aktiv.pk
    assert rueckgabe.zaehlerstaende.get().datum == D(2025, 9, 30)
