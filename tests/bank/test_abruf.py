"""Lauf bank_abruf mit dem Fake von Enable Banking (Schritt 1, Aufgaben 3, 6, 7)."""

from __future__ import annotations

import configparser
import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.bank import enablebanking, services
from apps.bank.models import Bankabruf, Bankkonto, Bankumsatz, Kontostand
from apps.kern.models import Arbeitsposten, Lauf
from tests.bank.conftest import eb_umsatz

pytestmark = pytest.mark.django_db

WURZEL = Path(__file__).resolve().parents[2]


def _lieferung(heute: datetime.date) -> list[dict[str, object]]:
    gestern = heute - datetime.timedelta(days=1)
    return [
        eb_umsatz(gestern, "120.00"),
        eb_umsatz(gestern, "120.00"),  # echt zweimal dieselbe Zahlung
        eb_umsatz(gestern, "48.20", eingang=False, name="Stadtwerke Testdorf", zweck="Strom"),
        eb_umsatz(heute, "85.50", zweck="Miete G-02", iban="DE13012345670000000002"),
        eb_umsatz(heute, "9.99", status="PDNG"),  # vorgemerkt: zählt nicht
    ]


def test_zweimaliger_abruf_erzeugt_keine_doppelten(
    vivid: Bankkonto, fake: enablebanking.FakeEnableBanking
) -> None:
    """Abnahme Schritt 1: Ein Test zeigt, dass ein zweimaliger Abruf desselben Zeitraums
    keine Doppelten erzeugt (Lauf bank_abruf, Enable Banking als Fake mit Paginierung)."""
    heute = timezone.localdate()
    fake.umsaetze_je_konto["uid-vivid"] = _lieferung(heute)
    von = (heute - datetime.timedelta(days=3)).isoformat()

    call_command("bank_abruf", "--von", von)
    erste = sorted(vivid.umsaetze.values_list("fingerabdruck", flat=True))
    call_command("bank_abruf", "--von", von)

    assert vivid.umsaetze.count() == 4
    assert sorted(vivid.umsaetze.values_list("fingerabdruck", flat=True)) == erste
    zweiter = Bankabruf.objects.filter(konto=vivid).order_by("-pk").first()
    assert zweiter is not None
    assert (zweiter.geliefert, zweiter.neu, zweiter.doppelt) == (4, 0, 4)
    laeufe = Lauf.objects.filter(name="bank_abruf").order_by("pk")
    assert [lauf.gebucht for lauf in laeufe] == [4, 0]
    assert all(lauf.status == Lauf.Status.ERFOLGREICH for lauf in laeufe)
    assert ("seite", 1) in fake.aufrufe  # mehr als eine Seite


def test_abruf_ueberlappt_mit_dem_letzten_umsatz(
    vivid: Bankkonto, fake: enablebanking.FakeEnableBanking
) -> None:
    """Ohne --von beginnt der Abruf 7 Tage vor dem letzten Umsatz (Doppelte fängt der
    Fingerabdruck ab); beim ersten Abruf 90 Tage zurück."""
    heute = timezone.localdate()
    call_command("bank_abruf")
    assert ("umsaetze", ("uid-vivid", heute - datetime.timedelta(days=90), heute)) in fake.aufrufe

    fake.umsaetze_je_konto["uid-vivid"] = _lieferung(heute)
    call_command("bank_abruf", "--von", (heute - datetime.timedelta(days=2)).isoformat())
    fake.aufrufe.clear()
    call_command("bank_abruf")
    assert ("umsaetze", ("uid-vivid", heute - datetime.timedelta(days=7), heute)) in fake.aufrufe


def test_abruf_liest_saldo_mit(vivid: Bankkonto, fake: enablebanking.FakeEnableBanking) -> None:
    """Schritt 1, Aufgabe 6: Der Abruf liest den Kontostand mit (Ende des Vortags)."""
    heute = timezone.localdate()
    fake.umsaetze_je_konto["uid-vivid"] = _lieferung(heute)
    fake.salden_je_konto["uid-vivid"] = [enablebanking.Saldo(Decimal("1085.50"), "ITBD")]

    call_command("bank_abruf", "--von", (heute - datetime.timedelta(days=3)).isoformat())

    stand = Kontostand.objects.get(konto=vivid)
    assert stand.datum == heute - datetime.timedelta(days=1)
    assert stand.saldo == Decimal("1000.00")  # 1085,50 minus der heutige Eingang 85,50


def test_abruf_meldet_saldoluecke(vivid: Bankkonto, fake: enablebanking.FakeEnableBanking) -> None:
    """Schritt 1, Aufgabe 6: Laufen Saldo und Umsätze auseinander, legt der Lauf einen
    Arbeitsposten mit Konto-ID und Datum an, ohne Beträge."""
    heute = timezone.localdate()
    vorgestern = heute - datetime.timedelta(days=2)
    services.kontostand_erfassen(vivid, vorgestern, Decimal("500.00"))
    fake.umsaetze_je_konto["uid-vivid"] = _lieferung(heute)
    # Der Saldo enthält einen Umsatz, den die Bank nicht geliefert hat (Lücke).
    fake.salden_je_konto["uid-vivid"] = [enablebanking.Saldo(Decimal("800.00"), "ITBD")]

    call_command("bank_abruf", "--von", vorgestern.isoformat())

    posten = Arbeitsposten.objects.get(art=services.ART_SALDO)
    assert f"Konto #{vivid.pk}" in posten.hinweis
    assert f"{heute - datetime.timedelta(days=1):%d.%m.%Y}" in posten.hinweis
    assert "800" not in posten.hinweis and "500" not in posten.hinweis
    assert Lauf.objects.get().arbeitsposten >= 1


def test_gescheiterter_abruf_wird_arbeitsposten(
    vivid: Bankkonto, fake: enablebanking.FakeEnableBanking
) -> None:
    """Schritt 1: Ein gescheiterter Abruf zählt als Fehler und wird ein Arbeitsposten
    (höchstens einer offen je Konto); seine Änderungen sind zurückgerollt."""
    fake.fehler = "Umsätze abrufen: HTTP 500"

    call_command("bank_abruf")
    call_command("bank_abruf")

    erster = Lauf.objects.order_by("pk").first()
    assert erster is not None
    assert erster.status == Lauf.Status.MIT_FEHLERN
    assert erster.fehler == 1
    posten = Arbeitsposten.objects.get(art=services.ART_ABRUF)
    assert "HTTP 500" in posten.hinweis
    assert posten.bezug == vivid
    assert not Bankumsatz.objects.exists()
    assert Bankabruf.objects.filter(status=Bankabruf.Status.GESCHEITERT).count() == 2


def test_abgelaufene_freigabe_scheitert(
    vivid: Bankkonto, fake: enablebanking.FakeEnableBanking
) -> None:
    gestern = timezone.localdate() - datetime.timedelta(days=1)
    services.bankfreigabe_anlegen(
        vivid, gueltig_ab=gestern - datetime.timedelta(days=5), gueltig_bis=gestern
    )
    vivid.freigaben.exclude(gueltig_bis=gestern).update(gueltig_bis=gestern)

    call_command("bank_abruf")

    assert "keine gültige Bankfreigabe" in Arbeitsposten.objects.get(art=services.ART_ABRUF).hinweis
    assert not any(a[0] == "umsaetze" for a in fake.aufrufe)


def test_lauf_prueft_freigaben(vivid: Bankkonto, fake: enablebanking.FakeEnableBanking) -> None:
    """Schritt 1, Aufgabe 7: Der tägliche Lauf legt 14 Tage vor Ablauf den Posten an."""
    heute = timezone.localdate()
    services.bankfreigabe_anlegen(vivid, gueltig_bis=heute + datetime.timedelta(days=10))
    vivid.freigaben.filter(gueltig_bis__gt=heute + datetime.timedelta(days=10)).update(
        gueltig_bis=heute + datetime.timedelta(days=10)
    )

    call_command("bank_abruf")
    call_command("bank_abruf")

    assert Arbeitsposten.objects.filter(art=services.ART_FREIGABE).count() == 1


def test_konto_ohne_zulieferung_wird_gemeldet(
    vr: Bankkonto, fake: enablebanking.FakeEnableBanking
) -> None:
    """Beide Konten laufen ohne Lücke ein: Kam für ein Konto seit zwei Tagen kein Abruf an,
    meldet der Lauf das (einmal)."""
    call_command("bank_abruf")
    call_command("bank_abruf")
    posten = Arbeitsposten.objects.get(art=services.ART_ZULIEFERUNG)
    assert posten.bezug == vr


def test_timer_taeglich_um_sechs() -> None:
    """Schritt 1, Aufgabe 10: bank_abruf läuft täglich um 06:00, seit Schritt 9 als erster
    Schritt des Tageslaufs (eigener Timer entfällt)."""
    from apps.cockpit import tageslauf

    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string((WURZEL / "deploy/systemd/laeufe/tageslauf.conf").read_text())
    assert parser["Timer"]["OnCalendar"] == "*-*-* 06:00:00"
    assert tageslauf.schritte()[0].name == "bank_abruf"
    assert not (WURZEL / "deploy/systemd/laeufe/bank_abruf.conf").exists()
