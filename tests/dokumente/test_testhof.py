"""Testhof-Modul der Dokumente: Briefkopf und freigegebene Beispielvorlagen (Schritt 3c)."""

from __future__ import annotations

import datetime

import pytest

from apps.dokumente import services
from apps.dokumente.models import Vorlage
from apps.dokumente.vorlagenarten import Versandweg
from testhof.aufbau import ZEITPUNKT, Testhof

pytestmark = pytest.mark.django_db


def test_testhof_hat_briefkopf_und_freigegebene_vorlagen(testhof: Testhof) -> None:
    """Schritt 3c: Der Testhof bringt einen Briefkopf und Beispielvorlagen mit, freigegeben
    vom Testbenutzer zum festen Zeitpunkt des Testhofs."""
    vorlagen = testhof.dokumente.vorlagen
    assert {"briefkopf", "mitteilung", "aushang", "zahlungserinnerung", "kuendigung"} <= set(
        vorlagen
    )
    for vorlage in vorlagen.values():
        assert vorlage.status == Vorlage.Status.FREIGEGEBEN
        assert vorlage.freigegeben_von == testhof.kern.benutzer
        assert vorlage.freigegeben_am == ZEITPUNKT
    assert services.briefkopf() == vorlagen["briefkopf"]


def test_mit_den_testhof_vorlagen_entstehen_schreiben(testhof: Testhof) -> None:
    """Abnahme Schritt 3 (Bauplan): Aus einer freigegebenen Vorlage entsteht ein PDF – mit
    Briefkopf und Vorlagen des Testhofs."""
    objekt = testhof.kern.objekte["HOF"]
    kontext = {
        "empfaenger": {
            "name": "Erika Beispiel",
            "anrede": "Guten Tag Frau Beispiel",
            "anrede_brief": "Guten Tag Frau Beispiel,",
            "anschrift": "Erfundene Straße 1\n12345 Testdorf",
            "email": "erika@testhof.invalid",
        },
        "vertrag": {"nummer": "V-1", "ende": datetime.date(2027, 3, 31)},
        "einheit": {"kuerzel": "HOF-G03", "bezeichnung": "Garage 3"},
        "objekt": {"kuerzel": objekt.kuerzel, "bezeichnung": objekt.bezeichnung},
        "betrag": {"offen": "85.00"},
        "forderung": {
            "posten": [
                {
                    "art": "Miete",
                    "monat": "Oktober 2026",
                    "faellig_am": datetime.date(2026, 10, 5),
                    "soll": "85.00",
                    "bezahlt": "0.00",
                    "offen": "85.00",
                }
            ],
            "summe": "85.00",
            "summe_soll": "85.00",
            "summe_bezahlt": "0.00",
        },
    }
    for art in ("mitteilung", "zahlungserinnerung", "kuendigung"):
        schreiben = services.schreiben_erzeugen(
            services.freigegebene_vorlage(art), objekt, kontext, Versandweg.BRIEF
        )
        assert schreiben.pdf_sha256
    erinnerung = services.freigegebene_vorlage("zahlungserinnerung")
    schreiben = services.schreiben_erzeugen(erinnerung, objekt, kontext, Versandweg.MAIL)
    assert "85,00 €" in schreiben.inhalt_html
    assert "Oktober 2026" in schreiben.inhalt_html
