"""Review 3, Punkt 10: bank_abruf ohne echte Bank endet in dev „übersprungen (dev)“."""

from __future__ import annotations

import io
from typing import Any

import pytest
from django.core.management import call_command

from apps.bank import enablebanking, services
from apps.bank.models import Bankkonto
from apps.kern.models import Arbeitsposten, Lauf

pytestmark = pytest.mark.django_db


def _lauf() -> Lauf:
    return Lauf.objects.filter(name="bank_abruf").latest("gestartet_am", "pk")


def test_bank_abruf_in_dev_uebersprungen(
    vivid: Bankkonto, fake: enablebanking.FakeEnableBanking, settings: Any
) -> None:
    """Review 3, Punkt 10: Scheitert in dev der Abruf (keine echte Bank, Fake-Fehler), endet
    bank_abruf „übersprungen (dev)“ statt „mit Fehlern“, ohne Arbeitsposten zum Abruf."""
    settings.HOF_DEV_LAEUFE = True
    fake.fehler = "Umsätze abrufen: HTTP 500"
    call_command("bank_abruf", stdout=io.StringIO())
    lauf = _lauf()
    assert lauf.status == Lauf.Status.UEBERSPRUNGEN
    assert lauf.fehler == 0
    assert lauf.meldung.startswith(
        f"übersprungen (dev): kein Abruf ohne echte Bank (Konto #{vivid.pk})"
    )
    assert not Arbeitsposten.objects.filter(art=services.ART_ABRUF).exists()


def test_bank_abruf_in_prod_ein_fehler(
    vivid: Bankkonto,
    fake: enablebanking.FakeEnableBanking,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review 3, Punkt 10: In prod bleibt ein gescheiterter Abruf ein Fehler."""
    settings.HOF_DEV_LAEUFE = True
    monkeypatch.setenv("DJANGO_ENV", "prod")
    fake.fehler = "Umsätze abrufen: HTTP 500"
    call_command("bank_abruf", stdout=io.StringIO())
    assert _lauf().status == Lauf.Status.MIT_FEHLERN
    assert Arbeitsposten.objects.filter(art=services.ART_ABRUF).exists()
