"""Review 1, Punkt 1: Eine Wahrheit für „offen“ und der Vortrag aus dem alten System.

Einzeltests mit erfundenen Verträgen (Beginn 01.01.2026, Abgleich ab 01.07.2026 wie in den
Test-Einstellungen). Der durchgehende Test gegen den Testhof steht in
``tests/ablaeufe/test_review1_offen_testhof.py``.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from apps.abgleich import services as abgleich
from apps.soll import kacheln, views
from apps.soll import services as soll
from apps.soll.models import Sollposten
from apps.stammdaten.models import Vertrag
from tests.soll.conftest import HEUTE, VertragBauer

D = datetime.date
E = Decimal
UMZUG = D(2026, 7, 1)


@pytest.fixture(autouse=True)
def _abgleich_heute(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(abgleich, "heute", lambda: HEUTE)


def _mit_umzug(neuer_vertrag: VertragBauer, miete: str = "85.00") -> Vertrag:
    vertrag = neuer_vertrag(miete=miete)
    soll.sollregel_setzen(vertrag, soll_ab=UMZUG)
    return vertrag


def test_vortrag_setzen_am_stichtag_des_umzugs(neuer_vertrag: VertragBauer) -> None:
    """Review 1, Punkt 1: Die Sollstellung beginnt mit dem Stichtag des Umzugs; was davor
    offen war, ist ein Posten „Vortrag“ je Vertrag, fällig am Stichtag."""
    vertrag = _mit_umzug(neuer_vertrag)
    # Posten vor dem Umzug gehören dem alten System und zählen nirgends als offen.
    assert all(p.sollposten.von >= UMZUG for p in soll.offen(vertrag).posten)

    posten = soll.vortrag_setzen(vertrag, E("120.50"))
    assert posten is not None
    assert (posten.art, posten.quelle, posten.von, posten.bis, posten.faellig_am) == (
        "vortrag",
        "uebernahme",
        UMZUG,
        UMZUG,
        UMZUG,
    )
    assert posten.betrag == E("120.50")
    stand = soll.offen(vertrag)
    assert stand.posten[0].sollposten == posten  # ältester zuerst
    assert stand.posten[0].monat == "bis 30.06.2026"


def test_vortrag_idempotent_aendern_und_loeschen(neuer_vertrag: VertragBauer) -> None:
    """Review 1, Punkt 1: Gleicher Vortrag ändert nichts; ein anderer Betrag storniert den
    alten (Ersatz zeigt auf ihn); 0 storniert ihn; bezahlter Vortrag bleibt."""
    vertrag = _mit_umzug(neuer_vertrag)
    erster = soll.vortrag_setzen(vertrag, E("50.00"))
    assert erster is not None
    assert soll.vortrag_setzen(vertrag, E("50.00")) == erster

    zweiter = soll.vortrag_setzen(vertrag, E("60.00"))
    assert zweiter is not None and zweiter.ersetzt == erster
    erster.refresh_from_db()
    assert erster.status == Sollposten.Status.STORNIERT

    soll.zahlungsstand_setzen(zweiter, E("10.00"))
    with pytest.raises(soll.SollFehler, match="schon gezahlt"):
        soll.vortrag_setzen(vertrag, E("70.00"))
    soll.zahlungsstand_setzen(zweiter, E("0.00"))
    assert soll.vortrag_setzen(vertrag, E("0.00")) is None
    assert soll.vortrag(vertrag) is None


def test_vortrag_nur_mit_stichtag_und_bei_altem_vertrag(neuer_vertrag: VertragBauer) -> None:
    """Review 1, Punkt 1: Ohne Stichtag des Umzugs und für Verträge, die erst danach
    beginnen, gibt es keinen Vortrag."""
    ohne = neuer_vertrag()
    with pytest.raises(soll.SollFehler, match="Stichtag des Umzugs"):
        soll.vortrag_setzen(ohne, E("10.00"))
    spaeter = neuer_vertrag(beginn=D(2026, 8, 1))
    soll.sollregel_setzen(spaeter, soll_ab=UMZUG)
    with pytest.raises(soll.SollFehler, match="keinen Vortrag"):
        soll.vortrag_setzen(spaeter, E("10.00"))


def test_alle_lesen_offen(neuer_vertrag: VertragBauer, angemeldet: Client) -> None:
    """Review 1, Punkt 1: Kachel Offene Posten, Seite Offene Posten (überfällig),
    Rückstände (geprüft und nicht prüfbar), Mietkonto und Vertragskopf nennen dieselbe
    Summe wie ``soll.offen``; Posten vor dem Beginn des Abgleichs zählen nirgends."""
    # Soll ab Januar, Abgleich ab Juli: Januar bis Juni zählen nirgends.
    vertrag = neuer_vertrag(miete="85.00")
    stand = soll.offen(stichtag=HEUTE)
    assert stand.anzahl == 5  # Juli bis November (fällig vor dem 20.11.)
    assert stand.summe_offen == E("425.00")
    assert soll.offen().anzahl == 5  # Dezember ist noch nicht gestellt

    # Review 3, Punkt 1: Ohne Bankdaten ist nichts prüfbar; die fünf Posten sind „nicht
    # prüfbar“, kein Rückstand. Kachel und Seite zeigen dieselbe Aufteilung.
    kachel = kacheln.offene_posten_kontext()
    assert kachel["nicht_pruefbar"] == soll.Teil(5, E("425.00"))
    assert kachel["ueberfaellig"] == soll.Teil(0, E("0.00"))

    seite = angemeldet.get(reverse("soll:liste"), {"status": "nicht_pruefbar"})
    assert (seite.context["anzahl"], seite.context["summe_offen"]) == (5, E("425.00"))

    rueck = abgleich.rueckstaende(HEUTE)
    zahlen = rueck.kennzahlen()
    assert zahlen["summe"] + zahlen["nicht_pruefbar"] == E("425.00")
    assert sum(len(r.posten) for r in rueck.vertraege) == 5

    konto = abgleich.mietkonto(vertrag)
    assert konto.rueckstand == E("425.00")
    assert konto.saldo == konto.bezahlt + konto.guthaben_summe - konto.soll_faellig
    assert konto.saldo == E("-425.00")

    kopf = angemeldet.get(reverse("stammdaten:vertrag", args=[vertrag.pk])).content.decode()
    assert "425,00\u00a0€</strong> in 5 Posten" in kopf


def test_summe_zaehlt_alle_treffer(
    neuer_vertrag: VertragBauer, angemeldet: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review 1, Punkt 1: Die Summe auf der Seite Offene Posten zählt alle Treffer des
    Filters, nicht nur die angezeigten."""
    monkeypatch.setattr(views, "GRENZE", 3)
    neuer_vertrag(miete="85.00")
    neuer_vertrag(miete="40.00")
    # Ohne Bankdaten sind die fälligen Posten „nicht prüfbar“ (Review 3, Punkt 1).
    seite = angemeldet.get(reverse("soll:liste"), {"status": "nicht_pruefbar"})
    assert len(seite.context["posten"]) == 3
    assert seite.context["abgeschnitten"]
    assert seite.context["anzahl"] == 10
    assert seite.context["summe_offen"] == E("625.00")  # 5 × 85 + 5 × 40
    assert "10 Posten (angezeigt die ersten 3" in seite.content.decode()
    assert "625,00\u00a0€" in seite.content.decode()
