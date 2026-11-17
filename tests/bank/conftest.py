"""Fixtures der Bank: Konten über die Services, Enable Banking als Fake."""

from __future__ import annotations

import datetime
import random
from decimal import Decimal
from typing import Any

import pytest

from apps.bank import enablebanking, services
from apps.bank.models import Bankkonto
from testhof import erfunden
from tests.factories import MandantFabrik

_zufall = random.Random("tests-bank")  # noqa: S311  (erfundene Test-IBANs)


def neue_iban() -> str:
    return erfunden.erfundene_iban(_zufall)


@pytest.fixture
def vivid(db: None) -> Bankkonto:
    """Ein Konto mit Enable Banking und gültiger Freigabe."""
    konto = services.bankkonto_anlegen(
        MandantFabrik(),
        bank="Vivid",
        bezeichnung="Vivid Test",
        iban=neue_iban(),
        zugang=Bankkonto.Zugang.ENABLE_BANKING,
        eb_bank_name="Vivid Money",
    )
    services.bankfreigabe_anlegen(
        konto,
        gueltig_bis=datetime.date.today() + datetime.timedelta(days=100),
        sitzung_id="s-1",
        eb_konto_uid="uid-vivid",
    )
    konto.refresh_from_db()
    return konto


@pytest.fixture
def vr(db: None) -> Bankkonto:
    """Ein Konto, das der hof-mac zuliefert."""
    return services.bankkonto_anlegen(
        MandantFabrik(),
        bank="VR",
        bezeichnung="VR Test",
        iban=neue_iban(),
        zugang=Bankkonto.Zugang.ZULIEFERUNG,
    )


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> enablebanking.FakeEnableBanking:
    """Der Fake, den Läufe und Seiten über ``enablebanking.client()`` bekommen."""
    instanz = enablebanking.FakeEnableBanking(seitengroesse=3)
    monkeypatch.setattr(enablebanking, "client", lambda: instanz)
    return instanz


def eb_umsatz(
    datum: datetime.date,
    betrag: str,
    *,
    eingang: bool = True,
    name: str = "Erika Beispiel",
    iban: str = "DE40012345670000000001",
    zweck: str = "Miete 11/2026 G-01",
    referenz: str = "",
    status: str = "BOOK",
) -> dict[str, Any]:
    """Ein Umsatz im Format der API von Enable Banking (erfundene Werte)."""
    gegen = "debtor" if eingang else "creditor"
    return {
        "entry_reference": referenz or f"ref-{datum.isoformat()}-{betrag}",
        "transaction_amount": {"currency": "EUR", "amount": betrag},
        "credit_debit_indicator": "CRDT" if eingang else "DBIT",
        "status": status,
        "booking_date": datum.isoformat(),
        "value_date": datum.isoformat(),
        gegen: {"name": name},
        f"{gegen}_account": {"iban": iban},
        "remittance_information": [zweck],
    }


def umsatz(
    datum: datetime.date,
    betrag: str,
    *,
    eingang: bool = True,
    iban: str = "DE40012345670000000001",
    zweck: str = "Miete",
) -> services.UmsatzDaten:
    return services.UmsatzDaten(
        buchungsdatum=datum,
        betrag=Decimal(betrag),
        richtung="eingang" if eingang else "ausgang",
        gegenpartei="Erika Beispiel",
        gegen_iban=iban,
        verwendungszweck=zweck,
    )
