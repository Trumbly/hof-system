"""Review 1, Punkt 2: Bankabruf und Bankfreigabe widersprechen sich nicht mehr.

- Der Abruf nimmt die jüngste Freigabe des Kontos, die heute gilt.
- Eine neue Freigabe erledigt den Posten „Bankabruf gescheitert“, der an der fehlenden
  Freigabe hing; ein erfolgreicher Abruf erledigt jeden offenen dieser Posten.
- Der Saldo steht mit dem Datum des letzten erfolgreichen Abrufs auf der Kontenseite.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.bank import enablebanking, services
from apps.bank.models import Bankabruf, Bankkonto
from apps.kern import zeit
from apps.kern.models import Arbeitsposten, Lauf
from tests.bank.conftest import eb_umsatz

pytestmark = pytest.mark.django_db

TAG = datetime.timedelta(days=1)


def _freigabe_abgelaufen(konto: Bankkonto, heute: datetime.date) -> None:
    """Die Freigabe aus der Fixture ist vor sechs Tagen abgelaufen (wie im Testhof)."""
    konto.freigaben.update(gueltig_ab=heute - 180 * TAG, gueltig_bis=heute - 6 * TAG)


def _abruf_posten(konto: Bankkonto) -> list[Arbeitsposten]:
    return [p for p in Arbeitsposten.objects.filter(art=services.ART_ABRUF) if p.bezug == konto]


def test_abgelaufene_alte_und_gueltige_neue_freigabe_abruf_erfolgreich(
    vivid: Bankkonto, fake: enablebanking.FakeEnableBanking
) -> None:
    """Review 1, Punkt 2: Neben einer abgelaufenen alten Freigabe gilt die neue; der Abruf
    ist erfolgreich und der Posten „Bankabruf gescheitert“ ist erledigt."""
    heute = zeit.heute()
    _freigabe_abgelaufen(vivid, heute)
    call_command("bank_abruf")
    (posten,) = _abruf_posten(vivid)
    assert services.GRUND_KEINE_FREIGABE in posten.hinweis

    services.bankfreigabe_anlegen(vivid, gueltig_bis=heute + 180 * TAG, sitzung_id="s-2")
    call_command("bank_abruf")

    letzter = Lauf.objects.filter(name="bank_abruf").order_by("-pk").first()
    assert letzter is not None and letzter.status == Lauf.Status.ERFOLGREICH
    abruf = vivid.abrufe.order_by("-pk").first()
    assert abruf is not None and abruf.status == Bankabruf.Status.ERFOLGREICH
    assert [p.status for p in _abruf_posten(vivid)] == [Arbeitsposten.Status.ERLEDIGT]
    assert ("umsaetze", ("uid-vivid", heute - 90 * TAG, heute)) in fake.aufrufe


def test_neue_freigabe_erledigt_posten_wegen_fehlender_freigabe(
    vivid: Bankkonto, fake: enablebanking.FakeEnableBanking
) -> None:
    """Review 1, Punkt 2: Nach dem Erneuern zeigt das System keinen offenen Posten „keine
    gültige Bankfreigabe“ mehr, auch bevor der nächste Abruf läuft."""
    heute = zeit.heute()
    _freigabe_abgelaufen(vivid, heute)
    call_command("bank_abruf")

    services.bankfreigabe_anlegen(vivid, gueltig_bis=heute + 180 * TAG)

    assert [p.status for p in _abruf_posten(vivid)] == [Arbeitsposten.Status.ERLEDIGT]


def test_erfolgreicher_abruf_erledigt_gescheiterten_posten(
    vivid: Bankkonto, fake: enablebanking.FakeEnableBanking
) -> None:
    """Review 1, Punkt 2: Ist ein Abruf aus anderem Grund gescheitert (HTTP 500), erledigt
    der nächste erfolgreiche Abruf den Posten."""
    fake.fehler = "Umsätze abrufen: HTTP 500"
    call_command("bank_abruf")
    assert [p.status for p in _abruf_posten(vivid)] == [Arbeitsposten.Status.OFFEN]

    fake.fehler = None
    call_command("bank_abruf")

    assert [p.status for p in _abruf_posten(vivid)] == [Arbeitsposten.Status.ERLEDIGT]


def test_freigabe_die_erst_spaeter_beginnt_gilt_heute_nicht(
    vivid: Bankkonto, fake: enablebanking.FakeEnableBanking
) -> None:
    """Review 1, Punkt 2: Der Abruf prüft „gilt heute“, nicht nur den spätesten Ablauf.
    Eine Freigabe, die erst in fünf Tagen beginnt, macht den Abruf heute nicht gültig."""
    heute = zeit.heute()
    _freigabe_abgelaufen(vivid, heute)
    services.bankfreigabe_anlegen(vivid, gueltig_ab=heute + 5 * TAG, gueltig_bis=heute + 300 * TAG)

    call_command("bank_abruf")

    assert not any(a[0] == "umsaetze" for a in fake.aufrufe)
    (posten,) = _abruf_posten(vivid)
    assert services.GRUND_KEINE_FREIGABE in posten.hinweis


def test_gueltige_freigabe_ist_die_juengste(vivid: Bankkonto) -> None:
    """Review 1, Punkt 2: Gelten zwei Freigaben, zählt die zuletzt erteilte, auch wenn die
    ältere länger läuft."""
    heute = zeit.heute()
    alte = vivid.freigaben.get()
    vivid.freigaben.update(gueltig_ab=heute - 30 * TAG, gueltig_bis=heute + 300 * TAG)
    neue = services.bankfreigabe_anlegen(vivid, gueltig_bis=heute + 180 * TAG)

    assert services.gueltige_freigabe(vivid, heute) == neue
    assert services.gueltige_freigabe(vivid, heute - 10 * TAG) == alte
    assert services.gueltige_freigabe(vivid, heute + 250 * TAG) == alte
    assert services.gueltige_freigabe(vivid, heute + 400 * TAG) is None


def test_saldo_mit_datum_des_letzten_erfolgreichen_abrufs(
    vivid: Bankkonto, fake: enablebanking.FakeEnableBanking
) -> None:
    """Review 1, Punkt 2: Der Saldo gilt bis zum Tag des letzten erfolgreichen Abrufs; die
    Umsätze nach dem letzten Kontostand der Bank sind eingerechnet."""
    heute = zeit.heute()
    alter_stand = heute - 19 * TAG
    services.kontostand_erfassen(vivid, alter_stand, Decimal("1000.00"))
    fake.umsaetze_je_konto["uid-vivid"] = [
        eb_umsatz(heute - 3 * TAG, "120.00"),
        eb_umsatz(heute - 2 * TAG, "20.50", eingang=False, name="Stadtwerke Testdorf"),
    ]

    call_command("bank_abruf", "--von", (heute - 10 * TAG).isoformat())

    stand = services.saldo_stand(vivid)
    assert stand is not None
    assert stand.stand == heute
    assert stand.kontostand_datum == alter_stand
    assert stand.saldo == Decimal("1099.50")
    assert stand.abgerufen_am is not None
    assert timezone.localdate(stand.abgerufen_am) == heute


def test_kontenseite_zeigt_stand_und_abrufzeit(
    angemeldet: Client, vivid: Bankkonto, fake: enablebanking.FakeEnableBanking
) -> None:
    """Review 1, Punkt 2: Die Kontenseite zeigt „Stand <Tag des Abrufs>, abgerufen …“ statt
    „Ende <Tag des letzten Kontostands>“."""
    heute = zeit.heute()
    services.kontostand_erfassen(vivid, heute - 19 * TAG, Decimal("1234.56"))
    call_command("bank_abruf")

    html = angemeldet.get(reverse("bank:konten")).content.decode()

    assert f"Stand {heute:%d.%m.%Y}, abgerufen {heute:%d.%m.%Y}" in html
    assert "1.234,56" in html
    assert "Ende " not in html
