"""Kennzahlen des Status-MCP (Schritt 11): eine Wahrheit mit dem Python-Code der Fach-Apps.

Der Lauf ``statusmcp_aktualisieren`` rechnet Rückstände, Datenstand, Leerstand und NW-IDs
der Fristen mit denselben Funktionen wie das Hof-Cockpit. Die Werkzeuge geben dieselben
Zahlen aus, aber keine Beträge je Vertrag.
"""

from __future__ import annotations

import io
from decimal import Decimal
from typing import Any

import pytest
from django.core.management import call_command, get_commands, load_command_class

from apps.abgleich import services as abgleich
from apps.cockpit import tageslauf
from apps.kern.laeufe import LaufBefehl
from apps.kern.models import Lauf
from apps.stammdaten import datenstand, leerstand
from apps.statusmcp import werkzeuge
from apps.statusmcp.models import Kennzahl
from testhof.aufbau import STICHTAG
from tests.statusmcp.conftest import RollenQuelle

pytestmark = pytest.mark.django_db


def _werkzeug(quelle: RollenQuelle, funktion: Any, *argumente: Any) -> dict[str, Any]:
    with quelle.lesen() as leser:
        ergebnis: dict[str, Any] = funktion(leser, *argumente)
    return ergebnis


def test_lauf_im_tageslauf() -> None:
    """Schritt 11: Der Lauf meldet sich beim Tageslauf an (Rang 85, nach dem Cockpit, vor
    der Sicherung) und ist ein LaufBefehl mit eigenem Laufprotokoll."""
    (schritt,) = [s for s in tageslauf.schritte() if s.name == "statusmcp_aktualisieren"]
    assert tageslauf.COCKPIT < schritt.reihenfolge < tageslauf.SICHERUNG
    assert set(schritt.abhaengig_von) == {"abgleich", "fristen"}
    befehl = load_command_class(get_commands()[schritt.befehl], schritt.befehl)
    assert isinstance(befehl, LaufBefehl)


def test_rueckstaende_wie_im_abgleich(betrieb: Any, quelle: RollenQuelle) -> None:
    """Abnahme Schritt 11: ``rueckstaende`` nennt Anzahl Verträge und Summen je Objekt und
    gesamt wie ``abgleich.rueckstaende()``; die NW-IDs ohne Beträge je Vertrag."""
    erwartet = abgleich.rueckstaende(STICHTAG).kennzahlen()
    antwort = _werkzeug(quelle, werkzeuge.rueckstaende)

    gesamt = antwort["gesamt"]
    assert gesamt["vertraege"] == erwartet["vertraege_mit_rueckstand"] > 0
    assert gesamt["vertraege_in_klaerung"] == erwartet["vertraege_in_klaerung"]
    for feld, schluessel in (
        ("summe", "summe"),
        ("summe_0_30", "summe_0_30"),
        ("summe_31_60", "summe_31_60"),
        ("summe_ueber_60", "summe_ueber_60"),
        ("nicht_pruefbar", "nicht_pruefbar"),
    ):
        assert Decimal(gesamt[feld]) == erwartet[schluessel], feld
    assert sum(Decimal(o["summe"]) for o in antwort["je_objekt"]) == erwartet["summe"]
    assert sum(o["vertraege"] for o in antwort["je_objekt"]) == gesamt["vertraege"]
    assert antwort["datenstand_bank"] == erwartet["datenstand_bank"]
    assert sorted(e["nw_id"] for e in antwort["einheiten"]) == erwartet["nw_ids"]
    # Je Einheit kein Betrag, nur Alter und Klasse.
    for eintrag in antwort["einheiten"]:
        assert set(eintrag) == {"nw_id", "objekt", "alter_tage", "klasse"}
        assert eintrag["alter_tage"] > 0


def test_datenstand_wie_auf_der_seite(betrieb: Any, quelle: RollenQuelle) -> None:
    """``datenstand`` nennt die Lücken je Bereich wie ``stammdaten.datenstand.zaehler`` und
    die NW-IDs der Einheiten mit Lücken."""
    antwort = _werkzeug(quelle, werkzeuge.datenstand)
    luecken = {b["bereich"]: b["luecken"] for b in antwort["bereiche"]}
    assert luecken == datenstand.zaehler(STICHTAG)
    (einheiten,) = [b for b in antwort["bereiche"] if b["bereich"] == "einheit"]
    assert einheiten["kennungen"] == sorted(datenstand.einheiten_mit_luecken(STICHTAG))
    alle = " ".join(antwort["kennungen_mit_luecken"])
    assert "Partei" not in alle and "Vertrag" not in alle


def test_leerstand_wie_berechnet(betrieb: Any, quelle: RollenQuelle) -> None:
    """``leerstand`` nennt die freien Einheiten am Stichtag mit Typ und Tagen frei."""
    erwartet = {z.einheit.nw_id: z for z in leerstand.berechnen(STICHTAG, STICHTAG)}
    antwort = _werkzeug(quelle, werkzeuge.leerstand)
    assert antwort["anzahl"] == len(erwartet) > 0
    for eintrag in antwort["einheiten"]:
        zeile = erwartet[eintrag["nw_id"]]
        assert eintrag["typ"] == zeile.einheit.typ
        assert zeile.frei_seit is not None
        assert eintrag["tage_frei"] == (STICHTAG - zeile.frei_seit).days + 1


def test_fristen_mit_kennungen(betrieb: Any, quelle: RollenQuelle) -> None:
    """``fristen`` nennt offene Fristen mit Art und NW-ID bzw. Objekt-Kürzel; erledigte
    nicht."""
    from apps.fristen.models import Frist

    antwort = _werkzeug(quelle, werkzeuge.fristen, STICHTAG, "2024-06-01", "2027-05-31")
    offen = Frist.objects.filter(
        erledigt_am__isnull=True, datum__range=("2024-06-01", "2027-05-31")
    )
    assert antwort["anzahl"] == offen.count() > 0
    kennungen = {k for f in antwort["fristen"] for k in f["kennungen"]}
    assert kennungen and all(k.startswith("NW-") or k.isupper() for k in kennungen)


def test_zweiter_lauf_ersetzt_die_momentaufnahme(betrieb: Any) -> None:
    """Ein zweiter Lauf schreibt die Kennzahlen neu, statt sie zu verdoppeln; der Lauf steht
    im Laufprotokoll mit Zählern."""
    vorher = Kennzahl.objects.count()
    call_command("statusmcp_aktualisieren", f"--stichtag={STICHTAG}", stdout=io.StringIO())
    assert Kennzahl.objects.count() == vorher > 0
    lauf = Lauf.objects.filter(name="statusmcp_aktualisieren").latest("gestartet_am", "pk")
    assert lauf.status == Lauf.Status.ERFOLGREICH
    assert lauf.gebucht == vorher
    assert "rueckstand" in lauf.meldung


def test_kassenbuch_summen_wie_buchungen(betrieb: Any, quelle: RollenQuelle) -> None:
    """``kassenbuch_monat``: Summe mit Vorzeichen je Objekt und Kategorie wie die Buchungen
    des Monats; Kategorien nur als ID und Art."""
    from apps.kassenbuch.models import Buchung

    antwort = _werkzeug(quelle, werkzeuge.kassenbuch_monat, "2026-10")
    erwartet = Decimal(0)
    for buchung in Buchung.objects.filter(datum__year=2026, datum__month=10):
        erwartet += buchung.betrag if buchung.richtung == "eingang" else -buchung.betrag
    assert Decimal(antwort["gesamt"]["saldo"]) == erwartet
    for objekt in antwort["je_objekt"]:
        for kategorie in objekt["kategorien"]:
            assert set(kategorie) == {"kategorie_id", "art", "wohnraum", "buchungen", "summe"}
