"""Probebetrieb: Eine Stufe wird erst scharf mit einer freigegebenen Vorlage."""

from __future__ import annotations

import datetime

import pytest

from apps.dokumente import services as dokumente
from apps.dokumente.models import Schreiben
from apps.kern.models import Arbeitsposten, Benutzer
from apps.mahnwesen import services
from apps.stammdaten import services as stammdaten
from tests.mahnwesen.conftest import (
    FAELLIG_OKT,
    Mahnhof,
    Vorlagen,
    fall_zu,
    freigeben,
    lauf,
    letzter,
    mahnposten,
    schritte,
)

pytestmark = pytest.mark.django_db
TAG = datetime.timedelta(days=1)


@pytest.fixture
def nur_briefkopf(benutzer: Benutzer, stufen: None) -> Vorlagen:
    v = Vorlagen(benutzer)
    v.freigeben("briefkopf")
    return v


def test_ohne_freigegebene_vorlage_nur_arbeitsposten(
    mahnhof: Mahnhof, nur_briefkopf: Vorlagen
) -> None:
    """Abnahme Schritt 7: Ohne freigegebene Vorlage bleibt die Stufe im Probebetrieb: Es
    entsteht nur der Arbeitsposten mit „Probebetrieb: Vorlage fehlt“, kein Schreiben."""
    vertrag = mahnhof.vertrag()
    # Ein Entwurf reicht nicht.
    dokumente.vorlage_anlegen("Zahlungserinnerung", "zahlungserinnerung", "<p>{{ datum }}</p>")

    ergebnis = lauf(FAELLIG_OKT + 5 * TAG)
    (schritt,) = schritte(vertrag)
    assert ergebnis.probebetrieb == 1 and ergebnis.schreiben == 0
    assert schritt.probebetrieb and schritt.schreiben is None
    assert not Schreiben.objects.exists()
    (posten,) = mahnposten(fall_zu(vertrag))
    assert "Probebetrieb: Vorlage fehlt" in posten.hinweis
    assert "zahlungserinnerung" in posten.hinweis

    lauf(FAELLIG_OKT + 14 * TAG)  # noch immer keine Vorlage: nichts Neues, keine Mahnung
    assert len(schritte(vertrag)) == 1 and len(mahnposten()) == 1


def test_freigabe_macht_die_stufe_scharf(
    mahnhof: Mahnhof, nur_briefkopf: Vorlagen, benutzer: Benutzer
) -> None:
    """Wird die Vorlage freigegeben, holt der nächste Lauf das Schreiben für denselben
    Schritt nach (kein zweiter Arbeitsposten)."""
    vertrag = mahnhof.vertrag()
    lauf(FAELLIG_OKT + 5 * TAG)
    nur_briefkopf.freigeben("zahlungserinnerung")

    ergebnis = lauf(FAELLIG_OKT + 7 * TAG)
    (schritt,) = schritte(vertrag)
    assert ergebnis.schreiben == 1
    assert not schritt.probebetrieb and schritt.schreiben is not None
    (posten,) = mahnposten()
    assert f"Schreiben #{schritt.schreiben.pk} bereit" in posten.hinweis
    assert "Probebetrieb" not in posten.hinweis


def test_von_hand_belegen_im_probebetrieb(mahnhof: Mahnhof, nur_briefkopf: Vorlagen) -> None:
    """Im Probebetrieb geht die Mahnung auf dem alten Weg hinaus; der Benutzer belegt die
    Stufe von Hand, bei einseitigen Erklärungen nur mit Vermerk zum Zustellnachweis."""
    vertrag = mahnhof.vertrag()
    lauf(FAELLIG_OKT + 5 * TAG)
    erinnerung = letzter(vertrag)
    services.schritt_von_hand_belegen(erinnerung, am=FAELLIG_OKT + 5 * TAG)
    assert mahnposten()[0].status == Arbeitsposten.Status.ERLEDIGT
    with pytest.raises(services.MahnwesenFehler):
        services.schritt_von_hand_belegen(erinnerung, am=FAELLIG_OKT + 6 * TAG)

    lauf(FAELLIG_OKT + 14 * TAG)
    mahnung = letzter(vertrag)
    assert mahnung.stufe.schluessel == "mahnung" and mahnung.probebetrieb
    with pytest.raises(services.MahnwesenFehler, match="Zustellnachweis"):
        services.schritt_von_hand_belegen(mahnung, am=FAELLIG_OKT + 15 * TAG)
    services.schritt_von_hand_belegen(
        mahnung, am=FAELLIG_OKT + 16 * TAG, vermerk="Einwurf durch Boten"
    )
    fall = fall_zu(vertrag)
    assert fall.stufe_belegt_am == FAELLIG_OKT + 16 * TAG
    assert fall.naechste_stufe_fruehestens == FAELLIG_OKT + 26 * TAG


def test_schritt_mit_schreiben_nicht_von_hand(
    mahnhof: Mahnhof, nur_briefkopf: Vorlagen, benutzer: Benutzer
) -> None:
    freigeben("Zahlungserinnerung", "zahlungserinnerung", "<p>{{ vertrag.nummer }}</p>", benutzer)
    vertrag = mahnhof.vertrag()
    lauf(FAELLIG_OKT + 5 * TAG)
    with pytest.raises(services.MahnwesenFehler, match="Versand"):
        services.schritt_von_hand_belegen(letzter(vertrag), am=FAELLIG_OKT + 5 * TAG)


def test_ohne_anschrift_kein_brief(mahnhof: Mahnhof, vorlagen: Vorlagen) -> None:
    """Abmahnung und Kündigung entstehen als Brief: Fehlt die Anschrift, entsteht kein
    Schreiben, der Arbeitsposten nennt den Grund (ohne Namen)."""
    from apps.mahnwesen.models import Mahnstufe

    stufe = Mahnstufe.objects.get(vertragsart="G", schluessel="erinnerung")
    services.stufe_aendern(stufe, versandweg="selbst")
    stammdaten.partei_aendern(mahnhof.mieter, strasse="")
    vertrag = mahnhof.vertrag()
    ergebnis = lauf(FAELLIG_OKT + 5 * TAG)
    assert ergebnis.ohne_schreiben == 1
    (posten,) = mahnposten()
    assert "Anschrift des Mieters fehlt" in posten.hinweis
    assert letzter(vertrag).schreiben is None
