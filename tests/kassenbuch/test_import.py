"""Import der Kategorien (Aufgabe 8): Probelauf als Standard, Bericht nur mit Zählern."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.kassenbuch import importieren, services
from apps.kassenbuch.importformat import KATEGORIEN_CSV
from apps.kassenbuch.models import Kategorie
from apps.kern import services as kern_services
from apps.kern.models import Lauf, Mandant

BEISPIEL = (
    Path(__file__).resolve().parents[1] / "fixtures" / "kassenbuch" / "kategorien_beispiel.csv"
)


@pytest.fixture
def gbr(db: None) -> Mandant:
    return kern_services.mandant_anlegen(Mandant.Art.GBR, "GBR", "GbR Beispiel")


def _befehl(*argumente: str) -> str:
    ausgabe = io.StringIO()
    call_command("kassenbuch_kategorien_import", str(BEISPIEL), *argumente, stdout=ausgabe)
    return ausgabe.getvalue()


def test_probelauf_ist_standard_und_aendert_nichts(gbr: Mandant) -> None:
    """Aufgabe 8: ``kassenbuch_kategorien_import`` liest eine Liste aus Name und Art; der
    Probelauf ist Standard, zählt nur und schreibt auch kein Laufprotokoll."""
    bericht = _befehl()

    assert not Kategorie.objects.exists()
    assert not Lauf.objects.exists()
    assert "Zeilen gelesen: 11" in bericht
    assert "gültig: 6" in bericht
    assert "neu: 6" in bericht
    assert "Probelauf: nichts geändert" in bericht


def test_ausfuehren_legt_an_und_bericht_nennt_keine_namen(gbr: Mandant) -> None:
    services.kategorie_anlegen("Strom", Kategorie.Art.EINNAHME)  # andere Art: Abweichung
    bericht = _befehl("--ausfuehren")

    arten = dict(Kategorie.objects.values_list("bezeichnung", "art"))
    assert arten == {
        "Strom": "einnahme",
        "Mieteinnahmen Garagen": "einnahme",
        "Pachteinnahmen": "einnahme",
        "Versicherungen": "ausgabe",
        "Entnahme GbR": "entnahme",
        "Umbuchung Tagesgeld": "umbuchung",
    }
    assert Kategorie.objects.get(bezeichnung="Entnahme GbR").mandant == gbr
    assert "neu: 5" in bericht
    assert "vorhanden mit anderer Art (nicht geändert): 1" in bericht
    for art in (
        "Name leer: 1",
        "Art unbekannt: 1",
        "Name doppelt in der Datei: 1",
        "Entnahme ohne Mandant: 1",
        "Mandant unbekannt: 1",
    ):
        assert art in bericht
    for name in ("Werbung", "Mieteinnahmen", "Entnahme ohne Kürzel", "XYZ"):
        assert name not in bericht
    assert Lauf.objects.get(name="kassenbuch_kategorien_import").gebucht == 5

    zweiter = _befehl("--ausfuehren")
    assert "neu: 0" in zweiter


def test_fehlende_spalte(tmp_path: Path, db: None) -> None:
    datei = tmp_path / "kaputt.csv"
    datei.write_text("Name;Typ\nStrom;Ausgabe\n", encoding="utf-8")
    with pytest.raises(CommandError, match="Spalten fehlen: Kategorie, Art"):
        call_command("kassenbuch_kategorien_import", str(datei), stdout=io.StringIO())


def test_spaltenzuordnung_an_einer_stelle(db: None) -> None:
    """Annahme zum Format: eine Zuordnung in ``apps/kassenbuch/importformat.py``."""
    assert KATEGORIEN_CSV.spalten["name"] == "Kategorie"
    assert KATEGORIEN_CSV.arten["kosten"] == Kategorie.Art.AUSGABE
    assert importieren.lesen("Kategorie;Art\nStrom;Kosten\n", KATEGORIEN_CSV)[1].gueltig == 1
