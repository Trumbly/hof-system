"""Review 1, Punkt 10: Bagatellgrenze im Mahnwesen (Einstellung, Standard 20 €).

Ein Rest unter der Grenze löst keine eigene Stufe aus und wird beim nächsten überfälligen
Posten mitgenannt."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from apps.mahnwesen import services
from apps.mahnwesen.models import Mahnfall
from apps.soll import services as soll
from apps.soll.models import Sollposten
from apps.stammdaten.models import Vertrag
from tests.mahnwesen.conftest import (
    FAELLIG_NOV,
    FAELLIG_OKT,
    OKTOBER,
    Mahnhof,
    Vorlagen,
    fall_zu,
    lauf,
    schritte,
)

pytestmark = pytest.mark.django_db
D = datetime.date
TAG = datetime.timedelta(days=1)
E = Decimal


def _rest_im_oktober(mahnhof: Mahnhof, bezahlt: str) -> Vertrag:
    vertrag = mahnhof.vertrag("85.00")
    oktober = Sollposten.objects.get(vertrag=vertrag, von=OKTOBER, art=Sollposten.Art.MIETE)
    soll.zahlungsstand_setzen(oktober, E(bezahlt))
    return vertrag


def test_standard_ist_zwanzig_euro() -> None:
    """Review 1, Punkt 10: Die Bagatellgrenze ist eine Einstellung, Standard 20 €."""
    assert services.bagatellgrenze() == E("20.00")


def test_rest_unter_der_grenze_loest_keine_stufe_aus(mahnhof: Mahnhof, vorlagen: Vorlagen) -> None:
    """Review 1, Punkt 10: Ein Rest von 15 € (unter 20 €) löst keine Stufe aus; er bleibt
    offen und steht im Rückstand (dieselbe Wahrheit wie überall), aber kein Fall entsteht."""
    vertrag = _rest_im_oktober(mahnhof, "70.00")
    ergebnis = lauf(FAELLIG_OKT + 20 * TAG)
    assert not Mahnfall.objects.exists()
    assert ergebnis.faelle_neu == 0
    lage = services.lagen(FAELLIG_OKT + 20 * TAG)[vertrag.pk]
    assert lage.rueckstand == E("15.00") and lage.ist_bagatelle


def test_rest_wird_beim_naechsten_posten_mitgenannt(mahnhof: Mahnhof, vorlagen: Vorlagen) -> None:
    """Review 1, Punkt 10: Mit dem nächsten überfälligen Posten erreicht der Rückstand die
    Grenze. Die Frist zählt ab dessen Fälligkeit (nicht ab dem alten Rest), und die
    Erinnerung nennt beide Posten: 15 € Rest und 85 € Novembermiete."""
    vertrag = _rest_im_oktober(mahnhof, "70.00")
    mahnhof.datenstand(D(2026, 11, 30))
    lauf(FAELLIG_NOV + 4 * TAG)
    assert not Mahnfall.objects.exists()

    lauf(FAELLIG_NOV + 5 * TAG)
    fall = fall_zu(vertrag)
    (schritt,) = schritte(vertrag)
    assert schritt.stufe.schluessel == "erinnerung"
    forderung = services.forderung(vertrag, FAELLIG_NOV + 5 * TAG)
    assert [p.offen for p in forderung.posten] == [E("15.00"), E("85.00")]
    assert forderung.summe_offen == E("100.00")
    assert {p.pk for p in fall.offene_posten.all()} == {p.sollposten.pk for p in forderung.posten}


def test_grenze_ist_einstellbar(mahnhof: Mahnhof, vorlagen: Vorlagen, settings: object) -> None:
    """Review 1, Punkt 10: Mit einer Grenze von 10 € mahnt der Lauf schon den Rest von 15 €."""
    settings.MAHNWESEN_BAGATELLGRENZE = E("10.00")  # type: ignore[attr-defined]
    vertrag = _rest_im_oktober(mahnhof, "70.00")
    lauf(FAELLIG_OKT + 5 * TAG)
    assert fall_zu(vertrag).stufe is not None


def test_rest_unter_der_grenze_zaehlt_nicht_als_offene_monatsmiete(mahnhof: Mahnhof) -> None:
    """Review 1, Punkt 10: Für die Abmahnung (offene Monatsmieten) zählt ein Monat mit einem
    Rest unter der Grenze nicht mit."""
    vertrag = _rest_im_oktober(mahnhof, "70.00")
    mahnhof.datenstand(D(2026, 11, 30))
    lage = services.lagen(FAELLIG_NOV + 5 * TAG)[vertrag.pk]
    assert lage.rueckstand == E("100.00")
    assert lage.offene_monatsmieten == 1
