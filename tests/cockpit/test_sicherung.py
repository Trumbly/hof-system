"""Sicherung prüfen (Tageslauf, Schritt 9): Statusdatei aus Schritt 0d, mit Fake-Datei."""

from __future__ import annotations

import io

import pytest
from django.core.management import call_command

from apps.cockpit import services
from apps.kern.models import Arbeitsposten, Lauf
from tests.cockpit.conftest import Statusdatei

pytestmark = pytest.mark.django_db


def _pruefen() -> Lauf:
    call_command("sicherung_pruefen", stdout=io.StringIO())
    return Lauf.objects.filter(name="sicherung_pruefen").latest("gestartet_am", "pk")


def _offen() -> list[Arbeitsposten]:
    return list(Arbeitsposten.objects.filter(art=services.ART_SICHERUNG, status="offen"))


def test_gute_sicherung_ist_still(statusdatei: Statusdatei) -> None:
    """Sicherung von heute Nacht mit „ok“: Lauf erfolgreich, kein Arbeitsposten."""
    statusdatei(alter_stunden=3)
    lauf = _pruefen()
    assert lauf.status == Lauf.Status.ERFOLGREICH
    assert "in Ordnung (3 Stunden alt)" in lauf.meldung
    assert _offen() == []


@pytest.mark.parametrize(
    ("angaben", "grund"),
    [
        ({"alter_stunden": 27}, "älter als 26 Stunden"),
        (
            {"ergebnis": "fehler", "fehler_bei": "restic backup"},
            "Ergebnis „fehler“ bei restic backup",
        ),
    ],
)
def test_alte_oder_gescheiterte_sicherung_meldet_sich(
    statusdatei: Statusdatei, angaben: dict[str, object], grund: str
) -> None:
    """Schritt 9, Tageslauf „Sichern“: Älter als 26 Stunden oder Ergebnis nicht „ok“ ergibt
    einen Fehler und einen Arbeitsposten „Sicherung prüfen“ (nur einer, auch bei Wiederholung).
    Mit der nächsten guten Sicherung schließt er sich."""
    statusdatei(**angaben)  # type: ignore[arg-type]
    lauf = _pruefen()
    assert lauf.status == Lauf.Status.MIT_FEHLERN
    assert lauf.fehler == 1 and lauf.arbeitsposten == 1
    assert grund in lauf.meldung
    assert len(_offen()) == 1

    _pruefen()
    assert len(_offen()) == 1

    statusdatei(alter_stunden=1)
    lauf = _pruefen()
    assert lauf.status == Lauf.Status.ERFOLGREICH
    assert "1 Arbeitsposten geschlossen" in lauf.meldung
    assert _offen() == []


def test_fehlende_statusdatei(settings: object, tmp_path: object) -> None:
    lauf = _pruefen()  # Testeinstellung zeigt auf eine Datei, die es nicht gibt
    assert lauf.status == Lauf.Status.MIT_FEHLERN
    assert "Statusdatei fehlt" in lauf.meldung


def test_unlesbare_statusdatei(statusdatei: Statusdatei) -> None:
    pfad = statusdatei()
    pfad.write_text("{kaputt", encoding="utf-8")
    assert services.sicherung_lesen().grund == "Statusdatei unlesbar"


def test_genau_26_stunden_ist_noch_gut(statusdatei: Statusdatei) -> None:
    statusdatei(alter_stunden=25.9)
    assert services.sicherung_lesen().ok
