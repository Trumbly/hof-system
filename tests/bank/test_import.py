"""Import alter Umsätze (H3.3): bank_import und die Seite „Import“ (Schritt 1, Aufgabe 5)."""

from __future__ import annotations

import datetime
import io
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.urls import reverse

from apps.bank import importieren, services
from apps.bank.importformat import VIVID_CSV
from apps.bank.models import Bankabruf, Bankkonto
from apps.kern.models import Lauf
from testhof import pruefsumme
from tests.bank.conftest import umsatz

pytestmark = pytest.mark.django_db

BEISPIEL = Path(__file__).resolve().parents[1] / "fixtures" / "bank" / "vivid_beispiel.csv"
#: Personendaten und Beträge aus der Beispieldatei; nichts davon darf im Bericht stehen.
VERBOTEN = (
    "Erika",
    "Paul",
    "Stadtwerke",
    "DE40",
    "DE13",
    "Miete",
    "Strom",
    "120",
    "85,50",
    "85.50",
    "48.20",
)


def _import(konto: Bankkonto, *argumente: str) -> str:
    ausgabe = io.StringIO()
    call_command("bank_import", str(BEISPIEL), "--konto", str(konto.pk), *argumente, stdout=ausgabe)
    return ausgabe.getvalue()


def test_probelauf_aendert_nichts_und_meldet_nur_zaehler(vivid: Bankkonto) -> None:
    """Abnahme Schritt 1: Der Import läuft im Probelauf ohne Änderung und meldet nur Zähler.
    Probelauf ist der Standard; es entsteht nicht einmal ein Eintrag im Laufprotokoll."""
    vorher = pruefsumme.tabellen_hashes()
    laeufe = Lauf.objects.count()

    ausgabe = _import(vivid)

    assert pruefsumme.tabellen_hashes() == vorher
    assert Lauf.objects.count() == laeufe
    assert not vivid.umsaetze.exists()
    assert "Zeilen gelesen: 10" in ausgabe
    assert "gültig: 7" in ausgabe
    assert "fehlerhaft: 3" in ausgabe
    assert "Betrag ungültig: 1" in ausgabe
    assert "Buchungsdatum ungültig: 1" in ausgabe
    assert "Betrag mit mehr als zwei Nachkommastellen: 1" in ausgabe
    assert "neu: 7" in ausgabe
    assert "Probelauf: nichts geändert" in ausgabe
    for wert in VERBOTEN:
        assert wert not in ausgabe, wert

    # Ausdrücklich --probelauf ergibt dasselbe.
    assert _import(vivid, "--probelauf") == ausgabe


def test_ausfuehren_und_zweiter_import_ohne_doppelte(vivid: Bankkonto) -> None:
    """Schritt 1, Aufgabe 5: --ausfuehren übernimmt als Lauf; derselbe Import ein zweites
    Mal findet nur Doppelte."""
    ausgabe = _import(vivid, "--ausfuehren")

    assert vivid.umsaetze.count() == 7
    assert "neu: 7" in ausgabe
    lauf = Lauf.objects.get(name="bank_import")
    assert (lauf.gebucht, lauf.fehler) == (7, 3)
    abruf = Bankabruf.objects.get(konto=vivid, quelle=Bankabruf.Quelle.IMPORT)
    assert (abruf.von, abruf.bis) == (datetime.date(2026, 1, 2), datetime.date(2026, 2, 3))
    for wert in VERBOTEN:
        assert wert not in ausgabe and wert not in lauf.meldung, wert

    zweite = _import(vivid)
    assert "neu: 0" in zweite
    assert "schon vorhanden (doppelt): 7" in zweite
    # Zwei gleiche Zahlungen am selben Tag bleiben zwei Umsätze (laufende Nummer).
    assert vivid.umsaetze.filter(buchungsdatum=datetime.date(2026, 1, 2)).count() == 2


def test_import_nur_vor_dem_abrufbeginn(vivid: Bankkonto) -> None:
    """Schritt 1, Aufgabe 5: Import und Abruf überschneiden sich nicht. Zeilen ab dem ersten
    Abruf zählt der Bericht als übersprungen."""
    abruf = services.bankabruf_starten(
        vivid, Bankabruf.Quelle.ENABLE_BANKING, von=datetime.date(2026, 2, 1)
    )
    services.umsaetze_uebernehmen(vivid, [umsatz(datetime.date(2026, 2, 2), "1.00")], abruf=abruf)
    services.bankabruf_beenden(abruf)

    assert importieren.abrufbeginn(vivid) == datetime.date(2026, 2, 1)
    ausgabe = _import(vivid, "--ausfuehren")
    assert "ab Abrufbeginn übersprungen: 2" in ausgabe
    assert vivid.umsaetze.count() == 1 + 5

    # Mit --bis lässt sich die Grenze setzen.
    assert "ab Abrufbeginn übersprungen: 3" in _import(vivid, "--bis", "2026-01-06")


def test_fehlende_pflichtspalte(vivid: Bankkonto, tmp_path: Path) -> None:
    datei = tmp_path / "falsch.csv"
    datei.write_text("Datum,Wert\n2026-01-01,1.00\n", encoding="utf-8")
    with pytest.raises(CommandError, match="Pflichtspalten fehlen: Booking Date, Amount"):
        call_command("bank_import", str(datei), "--konto", str(vivid.pk), stdout=io.StringIO())


def test_spaltenzuordnung_an_einer_stelle() -> None:
    """Schritt 1, Aufgabe 5: Die Spaltenzuordnung steht in einer Konfiguration."""
    from apps.bank.importformat import FELDER, FORMATE

    assert set(VIVID_CSV.spalten) == set(FELDER)
    assert FORMATE["vivid"] is VIVID_CSV
    kopf = BEISPIEL.read_text(encoding="utf-8").splitlines()[0].split(",")
    assert set(VIVID_CSV.spalten.values()) == set(kopf)


def test_import_ueber_die_seite(angemeldet: Client, vivid: Bankkonto) -> None:
    """Schritt 1, Aufgabe 8: Seite „Import“ mit Probelauf (nur Zähler), dann Ausführen."""
    url = reverse("bank:import")
    datei = SimpleUploadedFile("vivid.csv", BEISPIEL.read_bytes(), content_type="text/csv")

    antwort = angemeldet.post(
        url, {"schritt": "pruefen", "konto": vivid.pk, "format": "vivid", "datei": datei}
    )
    html = antwort.content.decode()
    assert antwort.status_code == 200
    assert "neu: 7" in html and "Ausführen" in html
    for wert in ("Erika", "Paul", "Stadtwerke", "DE40"):
        assert wert not in html
    assert not vivid.umsaetze.exists()

    pruefsumme_wert = angemeldet.session["bank_import"]["pruefsumme"]
    antwort = angemeldet.post(url, {"schritt": "ausfuehren", "pruefsumme": pruefsumme_wert})
    assert antwort.status_code == 200
    assert vivid.umsaetze.count() == 7
    assert Lauf.objects.get(name="bank_import").gebucht == 7
    assert "bank_import" not in angemeldet.session


def test_ausfuehren_ohne_probelauf_geht_nicht(angemeldet: Client, vivid: Bankkonto) -> None:
    antwort = angemeldet.post(reverse("bank:import"), {"schritt": "ausfuehren", "pruefsumme": "x"})
    assert antwort.status_code == 302
    assert not vivid.umsaetze.exists()
