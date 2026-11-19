"""Review 3, Punkt 1: Eine Uhr im Testhof und drei Begriffe für offene Beträge.

Der Testhof steht auf Stichtag 01.11.2026 (``HOF_STICHTAG`` in den Test-Einstellungen wie in
dev). Alles, was nach „heute“ fragt, sieht diesen Tag; „offen“, „überfällig“ und „nicht
prüfbar“ kommen überall aus ``soll.services.aufteilung``.
"""

from __future__ import annotations

import datetime
import io
import re
from decimal import Decimal
from typing import Any

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from apps.abgleich import services as abgleich
from apps.bank import enablebanking
from apps.bank import services as bank
from apps.bank.models import Bankkonto
from apps.kern import zeit
from apps.kern.models import Arbeitsposten, Lauf
from apps.soll import kacheln as soll_kacheln
from apps.soll import services as soll
from apps.soll.models import Sollposten
from apps.stammdaten import einheitenliste
from apps.stammdaten.models import Einheit
from testhof.aufbau import STICHTAG, Testhof

D = datetime.date
E = Decimal


def _li(html: str, kennung: str) -> str:
    """Das ``<li>`` einer Kachel im Cockpit."""
    return html.split(f'id="{kennung}"', 1)[1].split("</li>", 1)[0]


def _teil_zeile(kachel: str, teil: str) -> str:
    return re.sub(r"\s+", " ", kachel.split(f'data-teil="{teil}"', 1)[1].split("</span></span>")[0])


def test_cockpit_kopf_gleich_band(testhof: Testhof, angemeldet: Client) -> None:
    """Review 3, Punkt 1: Der Cockpit-Kopf zeigt den Stichtag („Heute · Sonntag,
    1. November 2026“) und stimmt mit dem Band „Testhof · Stichtag 01.11.2026“ überein."""
    html = angemeldet.get(reverse("kern:startseite")).content.decode()
    assert "Testhof · Stichtag 01.11.2026" in html
    kopf = html.split('class="seitenkopf', 1)[1].split("</header>", 1)[0]
    assert "Sonntag, 1. November 2026" in kopf
    assert "November 2026" in kopf and "19." not in kopf


def test_arbeitsposten_faellig_am_stichtag(testhof: Testhof) -> None:
    """Review 3, Punkt 1: Arbeitsposten, die heute fällig werden, sind am Stichtag fällig,
    nicht am echten Datum (vorher „fällig 19.11.2026“)."""
    konto = testhof.bank.konten["vivid"]
    posten = bank.abruf_gescheitert_melden(konto, "Test", lauf=None)
    assert posten is not None
    assert posten.faellig_am == STICHTAG
    from apps.cockpit import services as cockpit

    cockpit.sicherung_pruefen()  # Statusdatei fehlt in Tests: Posten „Sicherung prüfen“
    sicherung = Arbeitsposten.objects.filter(
        art=cockpit.ART_SICHERUNG, status=Arbeitsposten.Status.OFFEN
    )
    assert sicherung.exists()
    assert {p.faellig_am for p in sicherung} == {STICHTAG}


def test_bankfreigabe_gilt_am_stichtag(
    testhof: Testhof, angemeldet: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review 3, Punkt 1: Die Bankfreigabe des Testhofs gilt am Stichtag (sie läuft danach
    ab). Der Lauf bank_abruf scheitert deshalb nicht an „keine gültige Bankfreigabe“, und die
    Bankseite nennt sie nicht abgelaufen."""
    vivid = testhof.bank.konten["vivid"]
    freigabe = bank.aktuelle_freigabe(vivid)
    assert freigabe is not None and freigabe.gueltig_ab <= STICHTAG <= freigabe.gueltig_bis
    assert bank.gueltige_freigabe(vivid, zeit.heute()) == freigabe

    fake = enablebanking.FakeEnableBanking()
    monkeypatch.setattr(enablebanking, "client", lambda: fake)
    call_command("bank_abruf", stdout=io.StringIO())
    lauf = Lauf.objects.filter(name="bank_abruf").order_by("-pk").first()
    assert lauf is not None
    gescheitert = Arbeitsposten.objects.filter(
        art=bank.ART_ABRUF, status=Arbeitsposten.Status.OFFEN
    )
    assert not [p for p in gescheitert if bank.GRUND_KEINE_FREIGABE in p.hinweis]
    assert lauf.fehler == 0, lauf.meldung
    konten = Bankkonto.objects.filter(zugang=Bankkonto.Zugang.ENABLE_BANKING, aktiv=True)
    assert all(bank.gueltige_freigabe(k, STICHTAG) is not None for k in konten)

    seite = angemeldet.get(reverse("bank:konten")).content.decode()
    assert "abgelaufen" not in seite


def test_kacheln_und_seite_offene_posten_gleich(testhof: Testhof, angemeldet: Client) -> None:
    """Review 3, Punkt 1: Die Kacheln „Offene Posten“ und „Rückstand“ zeigen dieselbe
    Aufteilung (überfällig, nicht prüfbar, noch nicht fällig) aus einer Funktion, mit
    denselben Zahlen wie die Seite „Offene Posten“ und die Seite Rückstände."""
    aufteilung = soll.aufteilung()
    offen = soll.offen()
    assert aufteilung.offen == soll.Teil(offen.anzahl, offen.summe_offen)

    offene = soll_kacheln.offene_posten_kontext()
    rueck = abgleich.kachel_rueckstaende()
    for teil in soll.TEILE:
        assert offene[teil] == rueck[teil] == getattr(aufteilung, teil)
    # Der Rückstand ist genau „überfällig“.
    assert rueck["summe"] == aufteilung.ueberfaellig.summe
    stand = abgleich.rueckstaende().kennzahlen()
    assert stand["summe"] == aufteilung.ueberfaellig.summe
    assert stand["nicht_pruefbar"] == aufteilung.nicht_pruefbar.summe

    for teil in soll.TEILE:
        seite = angemeldet.get(reverse("soll:liste"), {"status": teil})
        assert (seite.context["anzahl"], seite.context["summe_offen"]) == (
            getattr(aufteilung, teil).anzahl,
            getattr(aufteilung, teil).summe,
        ), teil
    alle = angemeldet.get(reverse("soll:liste"))
    assert (alle.context["anzahl"], alle.context["summe_offen"]) == (
        aufteilung.offen.anzahl,
        aufteilung.offen.summe,
    )

    # Im Cockpit stehen in beiden Kacheln dieselben Zeilen, mit Erklärung im Tooltip.
    html = angemeldet.get(reverse("kern:startseite")).content.decode()
    kachel_offen, kachel_rueck = _li(html, "kachel-offene_posten"), _li(html, "kachel-rueckstaende")
    for teil in soll.TEILE:
        assert _teil_zeile(kachel_offen, teil) == _teil_zeile(kachel_rueck, teil)
    assert 'class="begriff" title="Vor heute fällig, aber nach dem letzten Bankabruf' in html


def test_drei_teile_ergeben_offen(testhof: Testhof, uhr: Any) -> None:
    """Review 3, Punkt 1: offen = überfällig + nicht prüfbar + noch nicht fällig; „nicht
    prüfbar“ heißt fällig nach dem letzten Bankabruf. Am 20.11. ist die Novembermiete nach
    dem Datenstand der Bank fällig, also nicht prüfbar, kein Rückstand."""
    uhr.stellen(D(2026, 11, 20))
    aufteilung = soll.aufteilung()
    datenstand = soll.datenstand_bank()
    assert datenstand is not None and datenstand < D(2026, 11, 20)
    for p in soll.offen().posten:
        teil = soll.einordnen(p.faellig_am, D(2026, 11, 20), datenstand)
        if p.faellig_am >= D(2026, 11, 20):
            assert teil == "noch_nicht_faellig"
        elif p.faellig_am > datenstand:
            assert teil == "nicht_pruefbar"
        else:
            assert teil == "ueberfaellig"
    assert aufteilung.nicht_pruefbar.anzahl > 0
    summe = sum(
        (getattr(aufteilung, t).summe for t in soll.TEILE),
        E("0.00"),
    )
    assert summe == soll.offen().summe_offen
    assert abgleich.kachel_rueckstaende()["summe"] == aufteilung.ueberfaellig.summe


def test_einheitenliste_ohne_laufende_miete(testhof: Testhof, angemeldet: Client, uhr: Any) -> None:
    """Review 3, Punkt 1: Die Spalte „offen“ (jetzt „überfällig“) der Einheitenliste zeigt
    nur Überfälliges, nicht die laufende Monatsmiete. Auch einige Tage nach ihrer Fälligkeit
    zählt sie nicht, solange die Bankdaten sie nicht prüfen können."""
    uhr.stellen(D(2026, 11, 20))
    heute = zeit.heute()
    # Die Novembermieten: fällig vor dem 20.11., aber nach dem letzten Bankabruf.
    laufende = list(
        Sollposten.objects.filter(
            status__in=soll.OFFENE_STATUS,
            art=Sollposten.Art.MIETE,
            von=D(2026, 11, 1),
            faellig_am__lt=heute,
        )
    )
    assert laufende
    erwartet: dict[int, Decimal] = {}
    for p in soll.teil_abfrage("ueberfaellig", stichtag=heute):
        erwartet[p.vertrag_id] = erwartet.get(p.vertrag_id, E("0.00")) + p.offen
    assert not {p.pk for p in laufende} & {
        p.pk for p in soll.teil_abfrage("ueberfaellig", stichtag=heute)
    }

    lagen = einheitenliste.lagen(heute, Einheit.objects.filter(aufgeloest_am__isnull=True))
    mit_vertrag = [lage for lage in lagen if lage.vertrag is not None]
    assert mit_vertrag
    for lage in mit_vertrag:
        assert lage.offen == erwartet.get(lage.vertrag.pk, E("0.00")), lage.vertrag.nummer
    # Die laufende Monatsmiete steht in keiner Zeile mehr (vorher bei fast jedem Vertrag):
    # Gegenüber dem alten Wert (alles vor heute Fällige) fehlt mindestens sie.
    for lage in mit_vertrag:
        miete = sum((p.offen for p in laufende if p.vertrag_id == lage.vertrag.pk), E("0.00"))
        vorher = sum((p.offen for p in soll.offen_abfrage(lage.vertrag, heute)), E("0.00"))
        assert vorher - lage.offen >= miete, lage.vertrag.nummer

    html = angemeldet.get(reverse("stammdaten:einheiten")).content.decode()
    assert '<th class="zahl"><span class="begriff"' in html
