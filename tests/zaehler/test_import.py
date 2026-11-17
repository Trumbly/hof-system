"""Import vorhandener Zähler und Stände (Schritt 8, Aufgabe 7), mit erfundener Beispieldatei."""

from __future__ import annotations

import datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command

from apps.kern.models import Objekt
from apps.stammdaten import services as stammdaten
from apps.stammdaten.models import Einheit
from apps.zaehler import importe
from apps.zaehler.models import Zaehler, Zaehlerstand

pytestmark = pytest.mark.django_db
D = datetime.date
FIXTURES = Path(__file__).parents[1] / "fixtures" / "zaehler"
ZAEHLER = FIXTURES / "zaehler.csv"
STAENDE = FIXTURES / "zaehlerstaende.csv"


@pytest.fixture
def einheiten(objekt: Objekt) -> None:
    stammdaten.einheit_anlegen(objekt, Einheit.Typ.GARAGE)  # NW-G-0001
    stammdaten.einheit_anlegen(objekt, Einheit.Typ.HALLE)  # NW-H-0001


def test_probelauf_ist_standard_und_aendert_nichts(einheiten: None) -> None:
    """Schritt 8, Aufgabe 7: Import mit --probelauf als Standard; der Bericht nennt nur
    Anzahlen, Zählernummern und Zeilennummern."""
    ausgabe = StringIO()
    call_command("import_zaehler", str(ZAEHLER), "--staende", str(STAENDE), stdout=ausgabe)
    text = ausgabe.getvalue()

    assert "PROBELAUF, nichts geändert" in text
    assert "neu: 3 (TB-S-1001, TB-S-1002, TB-W-2001)" in text
    assert "staende_neu: 5" in text
    assert not Zaehler.objects.exists() and not Zaehlerstand.objects.exists()
    assert "linear geschätzt" not in text  # Vermerke stehen nie im Bericht


def test_ausfuehren_legt_zaehler_und_staende_an(einheiten: None) -> None:
    bericht = importe.zaehler_importieren(ZAEHLER, STAENDE, ausfuehren=True)

    assert bericht.zaehler["neu"] == 3
    assert bericht.zaehler["staende_neu"] == 5
    assert bericht.zaehler["staende_geschaetzt"] == 1
    assert dict(bericht.abweichungen) == {
        "einheit_unbekannt": ["TB-S-1003"],
        "art_unbekannt": ["TB-X-9001"],
        "ort_fehlt": ["TB-W-2002"],
        "eichfrist_fehlt": ["TB-W-2001"],
        "stand_datum_unlesbar": ["Zeile 9"],
        "stand_zaehler_unbekannt": ["Zeile 8"],
        "stand_unplausibel": ["Zeile 6"],  # 340,25 nach 355,0: kleiner als der letzte
    }
    assert bericht.uebersprungen == 6
    halle = Zaehler.objects.get(nummer="TB-S-1002")
    assert [(s.datum, s.stand, s.art) for s in halle.staende.order_by("datum")] == [
        (D(2019, 6, 15), Decimal("1250"), "einbau"),
        (D(2024, 12, 31), Decimal("8100.5"), "abgelesen"),
        (D(2025, 12, 31), Decimal("9870"), "geschaetzt"),
    ]
    haupt = Zaehler.objects.get(nummer="TB-W-2001")
    assert haupt.objekt is not None and haupt.objekt.kuerzel == "HOF"

    # Ein zweiter Import ändert nichts, er meldet nur „vorhanden“.
    zweiter = importe.zaehler_importieren(ZAEHLER, STAENDE, ausfuehren=True)
    assert zweiter.zaehler["vorhanden"] == 3
    assert zweiter.zaehler.get("staende_neu", 0) == 0
    assert zweiter.zaehler["stand_vorhanden"] == 5


def test_spaltenzuordnung_an_einer_stelle() -> None:
    """Schritt 8, Aufgabe 7: Die Spaltenzuordnung steht an einer Stelle (import_spalten.toml)."""
    assert importe.SPALTEN_DATEI.name == "import_spalten.toml"
    assert set(importe.zuordnung("zaehler")["arten"].values()) <= set(Zaehler.Art.values)
    assert importe.zuordnung("zaehlerstaende")["spalten"]["stand"] == "Stand"
