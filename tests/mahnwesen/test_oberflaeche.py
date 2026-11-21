"""Seiten des Mahnwesens, Menüpunkt, Kachel im Hof-Cockpit, Tageslauf, Aufgaben im
Hof-Backlog."""

from __future__ import annotations

import datetime

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse

from apps.cockpit import kacheln, tageslauf
from apps.cockpit import services as cockpit
from apps.kern.models import Arbeitsposten, Benutzer
from apps.mahnwesen import services
from apps.mahnwesen.models import Mahnsperre, Mahnstufe
from tests.mahnwesen.conftest import (
    FAELLIG_OKT,
    Mahnhof,
    Vorlagen,
    fall_zu,
    lauf,
    letzter,
    mahnposten,
)

pytestmark = pytest.mark.django_db
D = datetime.date
TAG = datetime.timedelta(days=1)


def test_menuepunkt_und_seiten(mahnhof: Mahnhof, vorlagen: Vorlagen, angemeldet: Client) -> None:
    """Schritt 7: Menüpunkt „Mahnwesen“; Liste der Mahnfälle und Detail mit Stufenverlauf
    und Sperren. Die Liste zeigt den Mieter mit Namen (Max' Oberfläche, CLAUDE.md
    Abschnitt 1); Lauf-Meldung, Arbeitsposten (Notion) und Status-MCP bleiben ohne Namen."""
    vertrag = mahnhof.vertrag()
    ergebnis = lauf(FAELLIG_OKT + 5 * TAG)
    fall = fall_zu(vertrag)

    liste = angemeldet.get(reverse("mahnwesen:faelle")).content.decode()
    assert ">Mahnwesen</a>" in liste
    assert f"fall-{fall.pk}" in liste and "Zahlungserinnerung" in liste
    assert mahnhof.mieter.nachname in liste  # Oberfläche: mit Namen
    # Was nach Notion, in den MCP und in Lauf-Meldungen geht: ohne Namen
    name = mahnhof.mieter.nachname
    assert name not in ergebnis.text()
    assert all(name not in p.hinweis for p in Arbeitsposten.objects.all())
    assert name not in str(services.kachel_mahnfaelle())

    detail = angemeldet.get(reverse("mahnwesen:fall", args=[fall.pk])).content.decode()
    assert "Stufenverlauf" in detail and "Sperren" in detail
    schreiben = letzter(vertrag).schreiben
    assert schreiben is not None
    assert reverse("dokumente:schreiben", args=[schreiben.pk]) in detail


def test_von_hand_belegen_ueber_die_seite(
    mahnhof: Mahnhof, benutzer: Benutzer, stufen: None, angemeldet: Client
) -> None:
    """Probebetrieb: Der Schritt ohne Schreiben lässt sich auf der Seite des Falls belegen."""
    Vorlagen(benutzer).freigeben("briefkopf")
    vertrag = mahnhof.vertrag()
    lauf(FAELLIG_OKT + 5 * TAG)
    schritt = letzter(vertrag)
    assert schritt.probebetrieb
    url = reverse("mahnwesen:fall", args=[fall_zu(vertrag).pk])
    assert "Von Hand belegen" in angemeldet.get(url).content.decode()

    antwort = angemeldet.post(url, {"schritt": schritt.pk, "am": "2026-10-12", "vermerk": ""})
    assert antwort.status_code == 302
    schritt.refresh_from_db()
    assert schritt.belegt_am == D(2026, 10, 12)


def test_stufen_bearbeiten(stufen: None, angemeldet: Client) -> None:
    """Schritt 7, Aufgabe 2: Stufen änderbar in der Oberfläche."""
    seite = angemeldet.get(reverse("mahnwesen:stufen")).content.decode()
    assert "Garage (G)" in seite and "Fristlose Kündigung" in seite
    stufe = Mahnstufe.objects.get(vertragsart="G", schluessel="mahnung")
    antwort = angemeldet.post(
        reverse("mahnwesen:stufe", args=[stufe.pk]),
        {
            "titel": "Mahnung",
            "ausloeser": "tage_nach_faelligkeit",
            "ausloeser_wert": "21",
            "frist_tage": "10",
            "vorlagenart": "mahnung",
            "vorlage_name": "",
            "versandweg": "einwurf_einschreiben",
            "einseitige_erklaerung": "on",
            "aktiv": "on",
        },
    )
    assert antwort.status_code == 302
    stufe.refresh_from_db()
    assert (stufe.ausloeser_wert, stufe.versandweg) == (21, "einwurf_einschreiben")


def test_sperren_setzen_und_aufheben(mahnhof: Mahnhof, angemeldet: Client) -> None:
    vertrag = mahnhof.vertrag()
    antwort = angemeldet.post(
        reverse("mahnwesen:sperren"),
        {"vertrag": vertrag.pk, "grund": "ratenplan", "von": "2026-10-01", "hinweis": "Raten"},
    )
    assert antwort.status_code == 302
    sperre = Mahnsperre.objects.get(vertrag=vertrag)
    assert sperre.grund == "ratenplan" and not sperre.automatisch
    seite = angemeldet.get(reverse("mahnwesen:sperren")).content.decode()
    # „Heute“ ist echt; die Sperre ab 01.10.2026 gilt also.
    assert f"sperre-{sperre.pk}" in seite

    angemeldet.post(reverse("mahnwesen:sperre_aufheben", args=[sperre.pk]), {"am": "2026-10-20"})
    sperre.refresh_from_db()
    assert sperre.aufgehoben_am == D(2026, 10, 20)


def test_klaerfall_laesst_sich_nicht_von_hand_setzen(mahnhof: Mahnhof, angemeldet: Client) -> None:
    vertrag = mahnhof.vertrag()
    angemeldet.post(
        reverse("mahnwesen:sperren"),
        {"vertrag": vertrag.pk, "grund": "klaerfall", "von": "2026-10-01"},
    )
    assert not Mahnsperre.objects.exists()


def test_kachel_mahnfaelle(mahnhof: Mahnhof, vorlagen: Vorlagen, angemeldet: Client) -> None:
    """Schritt 7: Kachel „Mahnfälle“ im Hof-Cockpit (Anzahl je Stufe, gesperrte), Rang 35."""
    kachel = {k.name: k for k in kacheln.kacheln()}["mahnfaelle"]
    assert kachel.reihenfolge == kacheln.MAHNFAELLE == 35
    erster = mahnhof.vertrag()
    zweiter = mahnhof.vertrag(miete="50.00")
    services.sperre_setzen(zweiter, Mahnsperre.Grund.HAND, von=D(2026, 10, 1))
    lauf(FAELLIG_OKT + 5 * TAG)
    assert fall_zu(erster).stufe is not None

    kontext = services.kachel_mahnfaelle()
    assert kontext["anzahl"] == 2 and kontext["gesperrt"] == 1
    assert kontext["je_stufe"] == [{"titel": "Zahlungserinnerung", "anzahl": 1}]
    seite = angemeldet.get(reverse("kern:startseite")).content.decode()
    assert 'id="kachel-mahnfaelle"' in seite and 'Mahnfälle <span class="zaehler">2</span>' in seite


def test_tageslauf_montags_nach_dem_abgleich() -> None:
    """Schritt 7: Der Mahnlauf läuft im Tageslauf montags nach dem Abgleich."""
    montag, dienstag = D(2026, 11, 2), D(2026, 11, 3)
    namen = [s.name for s in tageslauf.schritte_am(montag)]
    assert namen.index("abgleich") < namen.index("mahnlauf")
    assert "mahnlauf" not in [s.name for s in tageslauf.schritte_am(dienstag)]
    (schritt,) = [s for s in tageslauf.schritte() if s.name == "mahnlauf"]
    assert schritt.abhaengig_von == ("abgleich",)
    assert schritt.reihenfolge == tageslauf.MAHNWESEN


def test_aufgaben_im_hof_backlog(mahnhof: Mahnhof, vorlagen: Vorlagen) -> None:
    """„Mahnungen senden“ geht ins Hof-Backlog und in die Benachrichtigung; die Aufgabe
    nennt die NW-IDs der Einheiten des Vertrags, keine Namen."""
    assert services.ART in settings.HOF_NOTION_ARTEN
    assert services.ART in settings.HOF_BENACHRICHTIGUNG_ARTEN
    vertrag = mahnhof.vertrag()
    lauf(FAELLIG_OKT + 5 * TAG)
    (posten,) = mahnposten()
    assert cockpit.kennungen(posten) == [e.nw_id for e in vertrag.einheiten.all()]
    assert mahnhof.mieter.nachname not in posten.hinweis
