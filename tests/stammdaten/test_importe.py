"""Importe mit Probelauf: Einheiten aus Notion, Parteien und Verträge aus HelloHousing."""

from __future__ import annotations

import csv
import datetime
import io
from decimal import Decimal
from pathlib import Path

import pytest
from django.core.management import call_command

from apps.kern.models import Lauf
from apps.stammdaten import importe
from apps.stammdaten.models import Einheit, Partei, Vertrag
from testhof import pruefsumme
from tests.factories import MandantFabrik, ObjektFabrik

pytestmark = pytest.mark.django_db

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "stammdaten"
NOTION = FIXTURES / "notion_einheiten.csv"
MIETER = FIXTURES / "hellohousing_mieter.csv"
VERLAUF = FIXTURES / "hellohousing_mietverlauf.csv"
STICHTAG = datetime.date(2026, 11, 1)


@pytest.fixture
def objekte(db: None) -> None:
    mandant = MandantFabrik()
    for kuerzel in ("HOF", "HALLE", "FELD"):
        ObjektFabrik(kuerzel=kuerzel, mandant=mandant)


def _personenwerte() -> set[str]:
    """Alle Namen, Anschriften, Mails und Telefonnummern der Beispieldatei (für die Prüfung,
    dass kein Bericht sie nennt)."""
    werte = set()
    with MIETER.open(encoding="utf-8") as datei:
        for zeile in csv.DictReader(datei, delimiter=";"):
            for spalte in ("Vorname", "Nachname", "Firma", "Straße", "Ort", "E-Mail", "Telefon"):
                if zeile[spalte] and len(zeile[spalte]) > 3:
                    werte.add(zeile[spalte])
            for spalte in ("Kaltmiete", "Nebenkosten"):
                if zeile[spalte] not in ("0,00", ""):
                    werte.add(zeile[spalte])
    return werte


def _stammdaten_hashes() -> dict[str, str]:
    return {k: v for k, v in pruefsumme.tabellen_hashes().items() if k.startswith("stammdaten.")}


def test_einheiten_probelauf_ohne_aenderung(objekte: None) -> None:
    """Abnahme Schritt 4: Der Import im Probelauf ändert nichts; der Bericht nennt Zähler und
    NW-IDs."""
    vorher = _stammdaten_hashes()

    bericht = importe.einheiten_importieren(NOTION, stichtag=STICHTAG)

    assert _stammdaten_hashes() == vorher
    assert not Einheit.objects.exists()
    assert not bericht.ausgefuehrt
    assert bericht.zeilen == 13
    assert bericht.zaehler["neu"] == 10
    assert bericht.zaehler["wohnraum_nur_nw_id"] == 1
    assert bericht.uebersprungen == 2
    assert bericht.abweichungen["typ_unbekannt"] == ["NW-X-0101"]
    assert bericht.abweichungen["objekt_unbekannt"] == ["NW-G-0104"]
    assert bericht.abweichungen["flaeche_fehlt"] == ["NW-G-0103", "NW-A-0101"]
    assert bericht.abweichungen["zielmiete_fehlt"] == ["NW-GA-0101"]
    assert bericht.abweichungen["nw_id_fremdes_format"] == ["G17"]
    assert bericht.abweichungen["nw_id_vergeben"] == ["NW-S-0102"]
    text = bericht.text()
    assert "PROBELAUF, nichts geändert" in text
    assert "NW-G-0101" in text


def test_einheiten_ausfuehren(objekte: None) -> None:
    bericht = importe.einheiten_importieren(NOTION, ausfuehren=True, stichtag=STICHTAG)

    assert bericht.ausgefuehrt
    assert Einheit.objects.count() == 11
    halle = Einheit.objects.get(nw_id="NW-H-0101")
    assert halle.flaeche_qm == Decimal("320.00")
    assert halle.zielmiete_am(STICHTAG).betrag == Decimal("1450.00")  # type: ignore[union-attr]
    assert halle.status_am(datetime.date(2023, 1, 1)).status == "vermietet"  # type: ignore[union-attr]
    wohnung = Einheit.objects.get(nw_id="NW-W-0101")
    assert (wohnung.flaeche_qm, wohnung.statusstaende.count()) == (None, 0)
    stellplatz = Einheit.objects.get(nw_id="NW-S-0101")
    assert stellplatz.status_am(STICHTAG).gueltig_ab == STICHTAG  # type: ignore[union-attr]

    # Zweiter Lauf: nichts doppelt, vorhandene NW-IDs bleiben unverändert.
    zweiter = importe.einheiten_importieren(NOTION, ausfuehren=True, stichtag=STICHTAG)
    assert zweiter.zaehler["vorhanden"] == 11 - 1  # die neu vergebene NW-S-0102 hat keine ID
    assert Einheit.objects.filter(bezeichnung="Stellplatz 2").count() == 2


@pytest.fixture
def einheiten(objekte: None) -> None:
    importe.einheiten_importieren(NOTION, ausfuehren=True, stichtag=STICHTAG)


def test_hellohousing_probelauf_ohne_aenderung(einheiten: None) -> None:
    """Abnahme Schritt 4: Probelauf des HelloHousing-Imports ohne Änderung; der Bericht nennt
    nur Zähler, Vertragsnummern und NW-IDs, keine Namen, Anschriften oder Beträge."""
    vorher = _stammdaten_hashes()

    bericht = importe.hellohousing_importieren(MIETER, VERLAUF, stichtag=STICHTAG)

    assert _stammdaten_hashes() == vorher
    assert not Vertrag.objects.exists() and not Partei.objects.exists()
    assert bericht.zaehler["vertraege_neu"] == 7
    assert bericht.zaehler["parteien_neu"] == 6
    assert bericht.zaehler["parteien_vorhanden"] == 1
    assert bericht.zaehler["wohnraum_uebersprungen"] == 1
    assert bericht.zaehler["beendet_uebersprungen"] == 1
    assert bericht.abweichungen["einheit_unbekannt"] == ["Zeile 8"]
    assert bericht.abweichungen["email_ungueltig"] == ["NW-L-0101"]
    assert bericht.abweichungen["anschrift_fehlt"] == ["G17"]
    assert bericht.abweichungen["staffel_oder_verlauf"] == ["NW-H-0101"]
    assert bericht.abweichungen["status_widerspruch"] == ["NW-G-0102"]
    assert bericht.uebersprungen == 1

    text = bericht.text()
    for wert in _personenwerte():
        assert wert not in text, wert


def test_hellohousing_ausfuehren(einheiten: None) -> None:
    """Schritt 4, Aufgabe 8: Parteien und laufende Verträge außerhalb von Wohnraum; Fristen,
    Kaution und Zahlweise bleiben offen (Datenstand), Mietverlauf wird zur Staffel."""
    importe.hellohousing_importieren(MIETER, VERLAUF, ausfuehren=True, stichtag=STICHTAG)

    assert Vertrag.objects.count() == 7
    assert set(Vertrag.objects.values_list("status", flat=True)) == {"aktiv"}
    assert not Vertrag.objects.filter(einheiten__typ=Einheit.Typ.WOHNUNG).exists()
    halle = Vertrag.objects.get(herkunft="hellohousing:MV-1003")
    assert [k.miete for k in halle.konditionen.order_by("gueltig_ab")] == [
        Decimal("1350.00"),
        Decimal("1400.00"),
    ]
    assert halle.kuendigungsregel == "" and halle.kaution.betrag is None
    assert halle.kondition_am(STICHTAG).zahlweise == ""  # type: ignore[union-attr]
    lena = Partei.objects.get(herkunft="hellohousing:M-501")
    assert lena.vertragsparteien.count() == 2
    firma = Partei.objects.get(herkunft="hellohousing:M-503")
    assert firma.art == Partei.Art.FIRMA

    zweiter = importe.hellohousing_importieren(MIETER, VERLAUF, ausfuehren=True, stichtag=STICHTAG)
    assert zweiter.zaehler["vertraege_vorhanden"] == 7
    assert Vertrag.objects.count() == 7


def test_fehlende_spalte(tmp_path: Path, objekte: None) -> None:
    datei = tmp_path / "falsch.csv"
    datei.write_text("Name,Objekt\nGarage,HOF\n", encoding="utf-8")
    with pytest.raises(importe.ImportFehler, match="Typ"):
        importe.einheiten_importieren(datei)


def test_befehle_mit_probelauf_als_standard(einheiten: None) -> None:
    """Schritt 4, Aufgabe 8: Jeder Import hat --probelauf (Standard) und --ausfuehren; der
    Lauf steht im Laufprotokoll mit Zählern."""
    ausgabe = io.StringIO()
    call_command(
        "import_hellohousing",
        str(MIETER),
        "--mietverlauf",
        str(VERLAUF),
        "--stichtag",
        "2026-11-01",
        stdout=ausgabe,
    )
    assert "PROBELAUF" in ausgabe.getvalue()
    assert not Vertrag.objects.exists()
    lauf = Lauf.objects.get(name="import_hellohousing")
    assert (lauf.gebucht, lauf.fehler) == (7, 1)
    assert "PROBELAUF" in lauf.meldung

    call_command("import_hellohousing", str(MIETER), "--ausfuehren", stdout=io.StringIO())
    assert Vertrag.objects.exists()

    ausgabe = io.StringIO()
    call_command("import_einheiten", str(NOTION), "--probelauf", stdout=ausgabe)
    assert "vorhanden: 10" in ausgabe.getvalue()
