"""Vorlagen mit Version und Freigabe (Schritt 3c, Aufgabe 1; H8.1)."""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from apps.dokumente import services, vorlagenarten
from apps.dokumente.models import Schreiben, Vorlage
from apps.dokumente.platzhalter import PlatzhalterFehler
from apps.kern.models import Benutzer, Objekt
from tests.dokumente.conftest import MITTEILUNG, kontext

pytestmark = pytest.mark.django_db


def test_vorlage_entsteht_als_entwurf_und_wird_freigegeben(benutzer: Benutzer) -> None:
    """Schritt 3c, Aufgabe 1: Vorlage mit Name, Art, Version, Text, Status, freigegeben
    von und am."""
    vorlage = services.vorlage_anlegen("Mitteilung", "mitteilung", MITTEILUNG)
    assert (vorlage.version, vorlage.status) == (1, Vorlage.Status.ENTWURF)
    assert vorlage.freigegeben_von is None

    services.vorlage_freigeben(vorlage, benutzer, freigegeben_von="Test")

    vorlage.refresh_from_db()
    assert vorlage.status == Vorlage.Status.FREIGEGEBEN
    assert vorlage.freigegeben_von == benutzer
    assert vorlage.freigegeben_am is not None


def test_neue_version_loest_die_alte_ab_und_aendert_sie_nicht(benutzer: Benutzer) -> None:
    """Schritt 3c, Aufgabe 1: Änderungen entstehen als neue Version; freigegebene Versionen
    bleiben unverändert und werden bei der nächsten Freigabe „abgelöst“."""
    v1 = services.vorlage_freigeben(
        services.vorlage_anlegen("Mitteilung", "mitteilung", MITTEILUNG),
        benutzer,
        freigegeben_von="Test",
    )
    with pytest.raises(services.SchreibenFehler):
        services.vorlage_aendern(v1, text="<p>anders</p>")

    v2 = services.vorlage_neue_version(v1)
    services.vorlage_aendern(v2, text="<p>{{ empfaenger.anrede }}, neu.</p>")
    assert (v2.version, v2.status) == (2, Vorlage.Status.ENTWURF)
    assert services.freigegebene_vorlage("mitteilung") == v1

    services.vorlage_freigeben(v2, benutzer, freigegeben_von="Test")
    v1.refresh_from_db()
    assert v1.status == Vorlage.Status.ABGELOEST
    assert v1.text == MITTEILUNG
    assert services.freigegebene_vorlage("mitteilung") == v2


def test_nur_freigegebene_vorlagen_lassen_sich_scharf_verwenden(
    briefkopf: Vorlage, benutzer: Benutzer, objekt: Objekt
) -> None:
    """Schritt 3c, Aufgabe 1: Nur freigegebene Vorlagen lassen sich scharf verwenden."""
    entwurf = services.vorlage_anlegen("Mitteilung", "mitteilung", MITTEILUNG)
    with pytest.raises(services.VorlageNichtFreigegeben):
        services.schreiben_erzeugen(entwurf, objekt, kontext(objekt))

    services.vorlage_freigeben(entwurf, benutzer, freigegeben_von="Test")
    neu = services.vorlage_neue_version(entwurf)
    services.vorlage_freigeben(neu, benutzer, freigegeben_von="Test")
    entwurf.refresh_from_db()  # jetzt abgelöst
    with pytest.raises(services.VorlageNichtFreigegeben):
        services.schreiben_erzeugen(entwurf, objekt, kontext(objekt))
    assert not Schreiben.objects.exists()


def test_vorlage_mit_unbekanntem_platzhalter_wird_abgelehnt() -> None:
    """Schritt 3c, Aufgabe 1: Eine Vorlage nutzt nur die Platzhalter ihrer Art."""
    with pytest.raises(PlatzhalterFehler, match=r"partei\.iban"):
        services.vorlage_anlegen("Mitteilung", "mitteilung", "{{ partei.iban }}")
    with pytest.raises(PlatzhalterFehler):
        services.vorlage_anlegen("Mitteilung", "mitteilung", "x", betreff="{{ unbekannt }}")
    with pytest.raises(vorlagenarten.UnbekannteVorlagenart):
        services.vorlage_anlegen("Mitteilung", "gibtsnicht", "x")
    assert not Vorlage.objects.exists()


def test_freigabe_braucht_wer_und_wann_auch_in_der_datenbank() -> None:
    """Schritt 3c, Aufgabe 1: Freigegeben heißt immer „von wem“ und „wann“ (Prüfbedingung)."""
    vorlage = services.vorlage_anlegen("Mitteilung", "mitteilung", MITTEILUNG)
    vorlage.status = Vorlage.Status.FREIGEGEBEN
    with pytest.raises(IntegrityError), transaction.atomic():
        vorlage.save()


def test_vorlagenarten_sind_konfiguration() -> None:
    """Schritt 3c: Die Vorlagenarten stehen als Liste an einer Stelle; Briefkopf, einseitige
    Erklärungen und Versandweg sind dort markiert."""
    arten = {art.schluessel: art for art in vorlagenarten.alle()}
    assert arten["briefkopf"].briefkopf
    assert arten["kuendigung"].einseitige_erklaerung
    assert arten["kuendigung"].versandweg == vorlagenarten.Versandweg.EINWURF_EINSCHREIBEN
    assert not arten["kuendigungsbestaetigung"].einseitige_erklaerung
    assert vorlagenarten.finden("Kündigung durch den Hof") == arten["kuendigung"]

    neu = vorlagenarten.Vorlagenart("testart", "Testart", platzhalter=("x",))
    vorlagenarten.registrieren(neu)
    try:
        assert vorlagenarten.holen("testart") == neu
        with pytest.raises(ValueError):
            vorlagenarten.registrieren(vorlagenarten.Vorlagenart("testart", "Andere"))
    finally:
        vorlagenarten._ARTEN.pop("testart")
