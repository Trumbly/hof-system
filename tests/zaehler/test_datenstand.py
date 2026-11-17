"""Datenstand, Bereich Zähler, und die Liste „für deinen nächsten Besuch“ (Schritt 8, Aufgabe 6)."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse

from apps.kern.models import Objekt
from apps.stammdaten import datenstand
from apps.stammdaten import services as stammdaten
from apps.stammdaten.models import Einheit
from apps.zaehler import services
from apps.zaehler.models import Zaehler
from tests.factories import ObjektFabrik
from tests.zaehler.conftest import foto

pytestmark = pytest.mark.django_db
D = datetime.date
HEUTE = datetime.date.today()
#: Der letzte Stichtag vor heute; die Tests rechnen von ihm aus, damit sie jedes Jahr gelten.
STICHTAG = services.letzter_stichtag(HEUTE)
EINBAU = D(STICHTAG.year - 3, 1, 1)
EICHFRIST = D(STICHTAG.year + 5, 12, 31)


def _zaehler(nummer: str, objekt: Objekt, **felder: object) -> Zaehler:
    werte: dict[str, object] = {
        "einbaudatum": EINBAU,
        "eichfrist_bis": EICHFRIST,
        "einbau_stand": "0",
    }
    werte.update(felder)
    return services.zaehler_anlegen(nummer, "wasser", objekt=objekt, **werte)  # type: ignore[arg-type]


@pytest.fixture
def lage(objekt: Objekt) -> dict[str, Zaehler]:
    """Vier Zähler auf zwei Objekten: einer vollständig, drei mit je einer Lücke."""
    feld = ObjektFabrik(kuerzel="FELD")
    voll = _zaehler("TZ-1", objekt)
    services.stand_erfassen(voll, STICHTAG, "10", foto=foto())
    ohne_stand = _zaehler("TZ-2", objekt)
    services.stand_erfassen(ohne_stand, STICHTAG - datetime.timedelta(days=60), "3")
    ohne_foto = _zaehler("TZ-3", objekt)
    services.stand_erfassen(ohne_foto, STICHTAG, "7", art="geschaetzt")
    ohne_eichfrist = _zaehler("TZ-4", feld, eichfrist_bis=None, einbaudatum=None, einbau_stand=None)
    services.stand_erfassen(ohne_eichfrist, STICHTAG, "4", foto=foto())
    return {"voll": voll, "stand": ohne_stand, "foto": ohne_foto, "eichfrist": ohne_eichfrist}


def test_bereich_zaehler_mit_pflichtfeldern(lage: dict[str, Zaehler]) -> None:
    """Schritt 8, Aufgabe 6: Bereich Zähler mit Nummer, Art, Einbaudatum, Eichfrist und Stand
    zum letzten Stichtag mit Foto."""
    stand = {b.schluessel: b for b in datenstand.ermitteln(HEUTE)}["zaehler"]
    luecken = {luecke.kennung: [f for f, _ in luecke.felder] for luecke in stand.luecken}
    assert stand.geprueft == 4
    assert luecken == {
        "Zähler TZ-2": ["stand_stichtag"],
        "Zähler TZ-3": ["foto_stichtag"],
        "Zähler TZ-4": ["einbaudatum", "eichfrist"],
    }
    assert datenstand.zaehler(HEUTE)["zaehler"] == 4
    # Ein Zähler, der erst nach dem Stichtag eingebaut wurde, braucht keinen Stand dazu.
    if STICHTAG < HEUTE:
        neu = _zaehler("TZ-5", lage["voll"].objekt, einbaudatum=HEUTE)
        stand = {b.schluessel: b for b in datenstand.ermitteln(HEUTE)}["zaehler"]
        assert f"Zähler {neu.nummer}" not in {luecke.kennung for luecke in stand.luecken}


def test_besuchsliste_zeigt_zaehler_ohne_stand_foto_oder_eichfrist_nach_objekt(
    lage: dict[str, Zaehler], angemeldet: Client
) -> None:
    """Abnahme Schritt 8: Der Datenstand zeigt Zähler ohne Stand zum Stichtag, ohne Foto oder
    ohne Eichfrist als Liste für den nächsten Besuch, sortiert nach Objekt, druckbar."""
    datenstand_seite = angemeldet.get(reverse("stammdaten:datenstand")).content.decode()
    assert 'id="bereich-zaehler"' in datenstand_seite
    assert f'href="{reverse("zaehler:besuch")}"' in datenstand_seite
    assert "fehlt Stand zum letzten Stichtag" in datenstand_seite

    seite = angemeldet.get(reverse("zaehler:besuch")).content.decode()
    assert "TZ-1<" not in seite  # vollständig: nicht auf der Liste
    # Sortiert nach Objekt: FELD vor HOF, darin nach Ort und Nummer.
    positionen = [seite.index(n) for n in ("TZ-4", "TZ-2", "TZ-3")]
    assert positionen == sorted(positionen)
    assert seite.index("<h2>FELD") < seite.index("<h2>HOF")
    assert "Stand zum letzten Stichtag" in seite
    assert "Foto zum Stand am Stichtag" in seite
    assert "Eichfrist" in seite
    assert f"Stichtag {STICHTAG:%d.%m.%Y}" in seite
    assert 'class="druckseite"' in seite and "window.print()" in seite
    css = (settings.BASE_DIR / "static" / "css" / "hof.css").read_text()
    assert "@media print" in css


def test_halle_ohne_zaehler_ist_eine_luecke_der_einheit(objekt: Objekt) -> None:
    """Bauplan, Seite Datenstand: Einheit vollständig, wenn ein Zähler zugeordnet ist, wo einer
    vorgesehen ist (Annahme: Hallen)."""
    halle = stammdaten.einheit_anlegen(
        objekt,
        Einheit.Typ.HALLE,
        flaeche_qm=Decimal("300"),
        status="frei",
        status_ab=D(2024, 1, 1),
        zielmiete=Decimal("1200"),
        zielmiete_ab=D(2024, 1, 1),
    )

    def luecken() -> dict[str, tuple[str, ...]]:
        stand = {b.schluessel: b for b in datenstand.ermitteln(HEUTE)}["einheit"]
        return {lk.kennung: tuple(f for f, _ in lk.felder) for lk in stand.luecken}

    assert luecken() == {halle.nw_id: ("zaehler",)}
    services.zaehler_anlegen("TZ-H-1", "strom", einheit=halle)
    assert luecken() == {}
