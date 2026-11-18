"""Anbindung an Tageslauf, Hof-Cockpit und Hof-Backlog (Schritt 9b)."""

from __future__ import annotations

import datetime
import io
from collections.abc import Iterator

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from apps.abgleich import services
from apps.abgleich.management.commands.abgleich import Command
from apps.cockpit import kacheln, tageslauf
from apps.cockpit import services as cockpit
from apps.cockpit.models import NotionAufgabe
from apps.cockpit.notion import FakeNotion
from apps.kassenbuch.models import Buchung
from apps.kern.laeufe import LaufBefehl
from tests.abgleich.conftest import Abgleichhof, klaerfaelle, neue_iban

D = datetime.date


@pytest.fixture(autouse=True)
def _fake_notion() -> Iterator[None]:
    FakeNotion.zuruecksetzen()
    yield
    FakeNotion.zuruecksetzen()


def test_abgleich_im_tageslauf_nach_einordnen_und_soll() -> None:
    """Der Lauf abgleich steht im Tageslauf nach Bankabruf, Einordnen und Sollstellung und
    vor den Fristen; er ist ein LaufBefehl mit --stichtag."""
    schritte = tageslauf.schritte()
    namen = [s.name for s in schritte]
    schritt = next(s for s in schritte if s.name == "abgleich")
    assert schritt.reihenfolge == tageslauf.ABGLEICH
    assert set(schritt.abhaengig_von) == {"bank_abruf", "kassenbuch_einordnen"}
    for vorher in ("bank_abruf", "kassenbuch_einordnen", "soll_monat"):
        assert namen.index(vorher) < namen.index("abgleich")
    assert namen.index("abgleich") < namen.index("fristen")
    assert issubclass(Command, LaufBefehl)


def test_lauf_mit_stichtag(hof: Abgleichhof) -> None:
    vertrag = hof.vertrag()
    iban = neue_iban()
    hof.regel(iban, vertrag=vertrag)
    hof.zahlung(D(2026, 7, 2), "85.00", iban=iban)

    call_command("abgleich", "--stichtag", "2026-11-20", stdout=io.StringIO())

    assert services.offener_betrag(Buchung.objects.get(umsatz__gegen_iban=iban)) == 0


def test_startseite_zeigt_die_echte_kachel(hof: Abgleichhof, angemeldet: Client) -> None:
    """Die Startseite zeigt die Kachel „Rückstände“ des Abgleichs statt des Platzhalters:
    Zähler und Summe, Link auf die Rückstände, keine Namen."""
    kachel = next(k for k in kacheln.kacheln() if k.name == "rueckstaende")
    assert not kachel.platzhalter
    assert kachel.template == "abgleich/_kachel_rueckstaende.html"
    hof.vertrag()
    hof.zahlung(D(2026, 11, 18), "0.01", iban=neue_iban(), zweck="Stand")

    html = angemeldet.get(reverse("kern:startseite")).content.decode()

    teil = html.split('id="kachel-rueckstaende"', 1)[1].split("</li>", 1)[0]
    assert reverse("abgleich:rueckstaende") in teil
    assert "1</strong> Verträge mit Rückstand" in teil
    assert "Kommen mit dem Abgleich" not in teil and "Erika" not in teil


def test_zahlungen_klaeren_im_hof_backlog_mit_nw_id(hof: Abgleichhof) -> None:
    """„Zahlungen klären“ erscheint im Hof-Backlog (HOF_NOTION_ARTEN) mit der NW-ID des
    Vertrags aus dem Vorschlag, ohne Vertrag mit dem Objekt-Kürzel. Keine Namen."""
    vertrag = hof.vertrag()
    nw_id = vertrag.einheiten.get().nw_id
    hof.zahlung(D(2026, 7, 2), "85.00", iban=neue_iban(), zweck=f"Miete {vertrag.nummer}")
    hof.zahlung(D(2026, 7, 3), "3.21", iban=neue_iban(), zweck="Unbekannt")
    services.abgleichen()
    mit, ohne = klaerfaelle()
    posten_mit, posten_ohne = services.klaerfall_posten(mit), services.klaerfall_posten(ohne)
    assert posten_mit is not None and posten_ohne is not None
    assert cockpit.kennungen(posten_mit) == [nw_id]
    assert cockpit.kennungen(posten_ohne) == ["HOF"]

    call_command("aufgaben_abgleich", stdout=io.StringIO())

    aufgabe = NotionAufgabe.objects.get(art=services.ART_KLAEREN)
    seite = FakeNotion.seiten[aufgabe.page_id]
    assert seite["Name"]["title"][0]["text"]["content"] == "2 Zahlungen klären"
    text = seite["Details"]["rich_text"][0]["text"]["content"]
    assert nw_id in text and "HOF" in text and "Erika" not in text
