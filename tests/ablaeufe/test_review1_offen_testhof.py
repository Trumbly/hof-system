"""Review 1, Punkt 1 (Abnahme): Testhof neu aufbauen, Tageslauf laufen lassen; danach stimmen
Cockpit, Offene Posten, Rückstände, Mahnlauf und Status-MCP in Anzahl und Summe überein.

Der Tageslauf läuft am Montag 02.11.2026 (der Mahnlauf läuft nur montags), die Bankdaten
reichen bis 31.10.2026. „Heute“ der Seiten ist derselbe Tag.

Erwartung aus Seed 42: drei überfällige Posten in drei Verträgen, zusammen 139,05 €:
der Dauerauftrag mit alter Miete (Vortrag 21,90 € plus 4 × 3,65 € Differenz seit Juli,
zusammen 36,50 €, offen im Oktoberposten), der Vertrag mit falscher Einheit im
Verwendungszweck (Klärfall) und die Rückbuchung (Rechtsfall). Nichts vor dem Umzug am
01.07.2026 ist „offen“.
"""

from __future__ import annotations

import datetime
import io
from collections.abc import Iterator
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from apps.abgleich import services as abgleich
from apps.cockpit.notion import FakeNotion
from apps.mahnwesen import services as mahnwesen
from apps.mahnwesen.models import Mahnfall
from apps.soll import kacheln as soll_kacheln
from apps.soll import services as soll
from apps.soll.models import Sollposten
from apps.statusmcp import services as statusmcp
from apps.statusmcp.models import Kennzahl
from testhof import abgleich as testhof_abgleich
from testhof.aufbau import Testhof
from testhof.soll import SOLL_AB

D = datetime.date
E = Decimal
MONTAG = D(2026, 11, 2)
ANZAHL, SUMME = 3, E("139.05")


@pytest.fixture
def nach_tageslauf(testhof: Testhof, monkeypatch: pytest.MonkeyPatch) -> Iterator[Testhof]:
    for modul in (soll, abgleich, mahnwesen):
        monkeypatch.setattr(modul, "heute", lambda: MONTAG)
    FakeNotion.zuruecksetzen()
    call_command("tageslauf", "--stichtag", MONTAG.isoformat(), stdout=io.StringIO())
    yield testhof
    FakeNotion.zuruecksetzen()


def test_soll_abgleich_und_mahnlauf_beginnen_am_selben_tag(testhof: Testhof) -> None:
    """Review 1, Punkt 1: Im Testhof beginnt das Soll am Stichtag des Umzugs, und der ist
    der Beginn des Abgleichs. Was davor offen war, steht als Vortrag am Stichtag."""
    assert SOLL_AB == testhof_abgleich.BEGINN == abgleich.beginn()
    vor_dem_umzug = Sollposten.objects.filter(bis__lt=SOLL_AB)
    assert not vor_dem_umzug.exists()
    (vortrag,) = Sollposten.objects.filter(art=Sollposten.Art.VORTRAG)
    assert (vortrag.von, vortrag.betrag) == (SOLL_AB, E("21.90"))
    assert vortrag.vertrag.pk in testhof.soll.vortraege


def test_abnahme_eine_wahrheit_fuer_offen(nach_tageslauf: Testhof, angemeldet: Client) -> None:
    """Review 1, Punkt 1, Abnahme: Nach Testhof und Tageslauf stimmen Cockpit (Kacheln
    Offene Posten und Rückstand), Seite Offene Posten, Rückstände, Mahnlauf und Status-MCP
    in Anzahl und Summe überein."""
    stand = soll.offen(stichtag=MONTAG)
    assert (stand.anzahl, stand.summe_offen, stand.vertraege) == (ANZAHL, SUMME, 3)

    # Cockpit: Kachel Offene Posten (überfällig) und Kachel Rückstand
    kachel = soll_kacheln.offene_posten_kontext()
    assert (kachel["ueberfaellig"], kachel["summe_ueberfaellig"]) == (ANZAHL, SUMME)
    rueck_kachel = abgleich.kachel_rueckstaende()
    assert (rueck_kachel["anzahl"], rueck_kachel["summe"]) == (3, SUMME)
    assert rueck_kachel["nicht_pruefbar"] == E("0.00")
    start = angemeldet.get(reverse("kern:startseite")).content.decode()
    assert "139,05\u00a0€" in start  # |euro: geschütztes Leerzeichen

    # Seite Offene Posten, Filter überfällig: Zahl und Summe über alle Treffer
    seite = angemeldet.get(reverse("soll:liste"), {"status": "ueberfaellig"})
    assert (seite.context["anzahl"], seite.context["summe_offen"]) == (ANZAHL, SUMME)

    # Rückstände
    rueck = abgleich.rueckstaende(MONTAG)
    zahlen = rueck.kennzahlen()
    assert (zahlen["vertraege_mit_rueckstand"], zahlen["summe"]) == (3, SUMME)
    assert sum(len(r.posten) for r in rueck.vertraege) == ANZAHL

    # Mahnlauf: dieselben Posten als Auslöser, je Vertrag ein Fall
    lagen = mahnwesen.lagen(MONTAG)
    assert sum(lage.rueckstand for lage in lagen.values()) == SUMME
    assert sum(len(lage.posten) for lage in lagen.values()) == ANZAHL
    faelle = Mahnfall.objects.exclude(status=Mahnfall.Status.ERLEDIGT)
    assert set(faelle.values_list("vertrag_id", flat=True)) == set(lagen)
    assert faelle.count() == 3

    # Status-MCP: Kennzahlen aus denselben Rückständen
    statusmcp.kennzahlen_aktualisieren(MONTAG)
    gesamt = Kennzahl.objects.get(
        bereich=Kennzahl.Bereich.RUECKSTAND, objekt="", schluessel="summe"
    )
    assert (gesamt.anzahl, gesamt.betrag) == (3, SUMME)
