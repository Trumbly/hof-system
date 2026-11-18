"""Stufen nach der SOP als Startdaten, änderbar (H4.1 bis H4.3)."""

from __future__ import annotations

import pytest

from apps.dokumente import vorlagenarten
from apps.mahnwesen import services, startdaten
from apps.mahnwesen.models import Mahnstufe
from apps.stammdaten import konfiguration
from apps.stammdaten.models import Vertrag

pytestmark = pytest.mark.django_db


def test_startdaten_nach_sop_je_vertragsart(stufen: None) -> None:
    """Schritt 7, Aufgabe 2: Erinnerung 5 Tage nach Fälligkeit; Mahnung nach 14 Tagen mit
    10 Tagen Frist; Abmahnung bei der zweiten offenen Monatsmiete; fristlose Kündigung,
    hilfsweise ordentlich, ab zwei Monatsmieten Rückstand. Je Vertragsart der Stammdaten."""
    assert set(startdaten.VERTRAGSARTEN) == set(Vertrag.Art.values)
    assert set(startdaten.VERTRAGSARTEN) == set(konfiguration.laden().vertragsarten)
    for art in Vertrag.Art.values:
        folge = services.stufen(art)
        werte = [(s.schluessel, s.ausloeser, s.ausloeser_wert, s.frist_tage) for s in folge]
        assert werte == [
            ("erinnerung", "tage_nach_faelligkeit", 5, 7),
            ("mahnung", "tage_nach_faelligkeit", 14, 10),
            ("abmahnung", "offene_monatsmieten", 2, 14),
            ("kuendigung", "rueckstand_monatsmieten", 2, 14),
        ]
        erinnerung, mahnung, abmahnung, kuendigung = folge
        assert erinnerung.versandweg == mahnung.versandweg == vorlagenarten.Versandweg.MAIL
        assert abmahnung.ist_brief and kuendigung.ist_brief  # Abmahnung und Kündigung: Brief
        assert not erinnerung.einseitige_erklaerung
        assert mahnung.einseitige_erklaerung and abmahnung.einseitige_erklaerung
        assert kuendigung.anwalt_bestaetigung and not abmahnung.anwalt_bestaetigung
        assert kuendigung.vorlage_name == "Fristlose Kündigung"


def test_startdaten_idempotent(stufen: None) -> None:
    anzahl = Mahnstufe.objects.count()
    assert services.startdaten_anlegen() == 0
    assert Mahnstufe.objects.count() == anzahl == 4 * len(Vertrag.Art.values)


def test_stufe_aendern(stufen: None) -> None:
    """Schritt 7, Aufgabe 2: Die Stufen sind Daten, änderbar in der Oberfläche."""
    stufe = Mahnstufe.objects.get(vertragsart="L", schluessel="erinnerung")
    services.stufe_aendern(stufe, ausloeser_wert=7, frist_tage=10, vorlage_name="Erinnerung Lager")
    stufe.refresh_from_db()
    assert (stufe.ausloeser_wert, stufe.frist_tage, stufe.vorlage_name) == (
        7,
        10,
        "Erinnerung Lager",
    )
    with pytest.raises(services.MahnwesenFehler):
        services.stufe_aendern(stufe, vertragsart="G")
    with pytest.raises(services.MahnwesenFehler):
        services.stufe_aendern(stufe, versandweg="brieftaube")


def test_vorlagenarten_des_mahnwesens() -> None:
    """Die Arten zahlungserinnerung, mahnung, abmahnung und kuendigung; nur die Kündigung
    verlangt die Bestätigung des Anwalts."""
    arten = {a.schluessel: a for a in vorlagenarten.alle()}
    for art in ("zahlungserinnerung", "mahnung", "abmahnung", "kuendigung"):
        erlaubt = arten[art].erlaubte_platzhalter()
        assert {"forderung", "frist", "betrag", "mahnung"} <= erlaubt
    assert arten["kuendigung"].anwalt_bestaetigung
    assert not any(
        arten[a].anwalt_bestaetigung for a in ("zahlungserinnerung", "mahnung", "abmahnung")
    )
    assert arten["abmahnung"].einseitige_erklaerung
