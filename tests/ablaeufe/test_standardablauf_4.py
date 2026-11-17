"""Standardablauf 4: Kündigung und Rückgabe (Prozessliste), mit dem Testhof.

Ergebnis laut Prozessliste: Die Einheit ist frei, die Kaution abgerechnet. Offen bis zu
ihren Schritten: Bestätigung als PDF (3c), Ende des Solls (5), offene Posten in der
Kautionsabrechnung (5/6), Fristen-App (9), Meldung an die Website (10).
"""

from __future__ import annotations

import datetime
import importlib
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from apps.stammdaten import fristen, schreiben, services
from apps.stammdaten.models import Checkliste, Einheit, Kaution, Kuendigung, Uebergabe, Vertrag
from testhof.aufbau import Testhof
from tests.ablaeufe.hilfen import posten, schritt
from tests.stammdaten.conftest import foto

D = datetime.date
ZUGANG = D(2026, 11, 3)
FRISTENDE = D(2027, 1, 31)


def _garagenvertrag(testhof: Testhof) -> Vertrag:
    for vertrag in testhof.stammdaten.aktive_vertraege():
        kaution = Kaution.objects.get(vertrag=vertrag)
        if (
            vertrag.vertragsart == Vertrag.Art.G
            and vertrag.kuendigungsregel == "monat_580a"
            and vertrag.ende is None
            and kaution.betrag
            and kaution.eingang_am
            and vertrag.einheiten.get().schluessel.filter(inhaber="partei").exists()
        ):
            return vertrag
    raise AssertionError("Kein passender Garagenvertrag im Testhof")


def test_standardablauf_4_kuendigung_und_rueckgabe(testhof: Testhof, angemeldet: Client) -> None:
    """Abnahme Schritt 4 (Standardablauf 4): Die Kündigung erzeugt Fristende, Bestätigung und
    Rückgabetermin; die Rückgabe gibt die Einheit frei und stößt die Kautionsabrechnung an."""
    vertrag = _garagenvertrag(testhof)
    einheit: Einheit = vertrag.einheiten.get()
    url = reverse("stammdaten:vertrag", args=[vertrag.pk])

    # 1. Du: erfasst die Kündigung mit Zugangsdatum am Vertrag.
    antwort = angemeldet.post(
        url,
        {
            "aktion": "kuendigung",
            "kuendigung-zugang_am": ZUGANG.isoformat(),
            "kuendigung-von": "mieter",
        },
    )
    assert antwort.status_code == 302

    # 2. System: Fristende, Bestätigung mit Rückgabetermin, Vertragsende (beendet das Soll).
    kuendigung = Kuendigung.objects.get(vertrag=vertrag)
    assert (kuendigung.fristende, kuendigung.rueckgabetermin) == (FRISTENDE, FRISTENDE)
    vertrag.refresh_from_db()
    assert (vertrag.status, vertrag.ende) == (Vertrag.Status.GEKUENDIGT, FRISTENDE)
    (bestaetigung,) = posten(schreiben.ART, kuendigung)
    assert "kuendigungsbestaetigung" in bestaetigung.hinweis
    kontext = schreiben.kontext_kuendigung(kuendigung)
    assert kontext["kuendigung"]["rueckgabetermin"] == FRISTENDE
    (frist,) = posten(fristen.ART, vertrag, status="offen")
    assert frist.faellig_am == FRISTENDE and frist.hinweis.startswith("Rückgabetermin")

    # 3. Vor Ort: Rückgabe mit Protokoll, Fotos, Zählerstand und allen Schlüsseln.
    angemeldet.post(reverse("stammdaten:uebergabe_starten", args=[vertrag.pk, "rueckgabe"]))
    rueckgabe = Uebergabe.objects.get(vertrag=vertrag, art="rueckgabe")
    schluessel = services.schluessel_der_uebergabe(rueckgabe)
    assert schluessel
    schritt(angemeldet, rueckgabe.pk, 1, {"datum": FRISTENDE.isoformat()})
    schritt(angemeldet, rueckgabe.pk, 2, {"nummer_0": "Z-R-1", "stand_0": "2210"})
    schritt(angemeldet, rueckgabe.pk, 3, {"fotos": [foto("rueckgabe.png")]})
    schritt(angemeldet, rueckgabe.pk, 4, {f"schluessel_{s.pk}": str(s.anzahl) for s in schluessel})
    schritt(angemeldet, rueckgabe.pk, 5, {"zustand": "besenrein", "zurueckgelassen": "nichts"})
    schritt(angemeldet, rueckgabe.pk, 6, {})

    # 4. System: Einheit frei, Frist für die Kautionsabrechnung.
    vertrag.refresh_from_db()
    kaution = Kaution.objects.get(vertrag=vertrag)
    assert vertrag.status == Vertrag.Status.BEENDET
    assert einheit.status_am(FRISTENDE).status == "frei"  # type: ignore[union-attr]
    assert not einheit.schluessel.filter(inhaber="partei").exists()
    assert kaution.abrechnung_faellig_am == D(2027, 7, 31)
    (abrechnen,) = posten(services.ART_KAUTION_ABRECHNEN, kaution)
    assert abrechnen.faellig_am == D(2027, 7, 31)
    assert posten(fristen.ART, kaution, hinweis__startswith="Kautionsabrechnung")
    assert not posten(fristen.ART, vertrag, status="offen")  # Rückgabetermin erfüllt

    # 5. Du: rechnest die Kaution über die Vorlage ab.
    antwort = angemeldet.post(
        url,
        {
            "aktion": "kaution_abrechnen",
            "kaution_abrechnen-datum": "2027-02-15",
            "kaution_abrechnen-einbehalt": "20.00",
        },
    )
    assert antwort.status_code == 302
    kaution.refresh_from_db()
    assert kaution.abgerechnet_am == D(2027, 2, 15)
    assert kaution.ausgezahlt == kaution.betrag - Decimal("20.00")  # type: ignore[operator]
    assert posten(schreiben.ART, kaution, hinweis__startswith="Vorlagenart kautionsabrechnung")
    abrechnen.refresh_from_db()
    assert abrechnen.status == "erledigt"

    # 6. System: legt die Checkliste für die Neuvermietung an.
    assert einheit.checklisten.filter(phase=Checkliste.Phase.VORBEREITUNG).exists()


@pytest.mark.xfail(
    reason="Schritt 3c: Die Bestätigung entsteht noch nicht als PDF (App dokumente)."
)
def test_standardablauf_4_bestaetigung_als_pdf() -> None:
    importlib.import_module("apps.dokumente.services")
    assert "dokumente" in schreiben._anfordern.__code__.co_names


@pytest.mark.xfail(reason="Schritt 5: Das Soll endet zum Vertragsende (App soll).")
def test_standardablauf_4_soll_endet() -> None:
    importlib.import_module("apps.soll.services")


@pytest.mark.xfail(reason="Schritt 9: Rückgabetermin und Kautionsfrist stehen in der App fristen.")
def test_standardablauf_4_fristen_in_der_fristen_app() -> None:
    importlib.import_module("apps.fristen.services")
    assert "fristen" in fristen.fristen_anfordern.__code__.co_names


@pytest.mark.xfail(reason="Schritt 10: Die freie Einheit wird an die Website gemeldet.")
def test_standardablauf_4_website_meldung() -> None:
    importlib.import_module("apps.anbindungen.services")
