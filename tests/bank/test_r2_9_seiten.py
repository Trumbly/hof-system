"""Bank-Seiten in der neuen Gestaltung (PR R2-9): „Erneut abrufen“ an der Kontenseite über
``services.abruf_starten``, Kontonamen statt IDs, Unternavigation als Reiter."""

from __future__ import annotations

import json

import pytest
from django.test import Client
from django.urls import reverse

from apps.bank import enablebanking, services
from apps.bank.models import Bankabruf, Bankkonto
from apps.kern import zeit
from apps.kern.models import Lauf
from tests.bank.conftest import eb_umsatz

pytestmark = pytest.mark.django_db
HTMX = {"HTTP_HX_REQUEST": "true"}


def test_abruf_starten_ruft_nur_dieses_konto_als_lauf_ab(
    vivid: Bankkonto, vr: Bankkonto, fake: enablebanking.FakeEnableBanking
) -> None:
    """R2-9 (gemeinsam mit R2-6): ``abruf_starten(konto)`` ist der Lauf ``bank_abruf`` für ein
    Konto, mit eigenem Eintrag im Laufprotokoll und dem Abruf dazu."""
    heute = zeit.heute()
    fake.umsaetze_je_konto["uid-vivid"] = [eb_umsatz(heute, "12.00")]

    ergebnis = services.abruf_starten(vivid)

    assert ergebnis.lauf.name == "bank_abruf" and ergebnis.lauf.status == Lauf.Status.ERFOLGREICH
    assert ergebnis.abruf is not None and ergebnis.abruf.konto == vivid
    assert (ergebnis.abruf.status, ergebnis.abruf.neu) == (Bankabruf.Status.ERFOLGREICH, 1)
    with pytest.raises(services.BankFehler, match="nicht selbst ab"):
        services.abruf_starten(vr)


def test_erneut_abrufen_an_der_kontenseite(
    vivid: Bankkonto, vr: Bankkonto, fake: enablebanking.FakeEnableBanking, angemeldet: Client
) -> None:
    """R2-9: Knopf „Erneut abrufen“ je Konto mit Enable Banking; die Meldung sagt, was
    passiert ist, und verlinkt auf die Umsätze. Die Liste der Konten wird ersetzt."""
    seite = angemeldet.get(reverse("bank:konten")).content.decode()
    assert reverse("bank:abrufen", args=[vivid.pk]) in seite
    assert reverse("bank:abrufen", args=[vr.pk]) not in seite  # liefert der hof-mac

    fake.umsaetze_je_konto["uid-vivid"] = [eb_umsatz(zeit.heute(), "12.00")]
    antwort = angemeldet.post(reverse("bank:abrufen", args=[vivid.pk]), **HTMX)
    html = antwort.content.decode()
    assert "Vivid Test abgerufen: 1 neue Umsätze" in html
    assert f"{reverse('bank:umsaetze')}?konto={vivid.pk}" in html
    assert 'id="konten-liste" hx-swap-oob="true"' in html
    assert "dialogSchliessen" in json.loads(antwort["HX-Trigger"])

    fake.fehler = "Status 500"
    antwort = angemeldet.post(reverse("bank:abrufen", args=[vivid.pk]), follow=True)
    assert "Abruf für Vivid Test gescheitert" in antwort.content.decode()

    antwort = angemeldet.post(reverse("bank:abrufen", args=[vr.pk]), follow=True)
    assert "ruft das Hof-System nicht selbst ab" in antwort.content.decode()


def test_kontonamen_statt_ids_und_unternavigation(
    vivid: Bankkonto, vr: Bankkonto, angemeldet: Client
) -> None:
    """Review 2, B11: Kontonamen statt „Bankkonto #3“; die Unternavigation ist eine Zeile
    Reiter statt loser Links mit „·“."""
    for name in ("bank:konten", "bank:umsaetze", "bank:import"):
        html = angemeldet.get(reverse(name)).content.decode()
        assert f"Bankkonto #{vivid.pk}" not in html and f"Konto #{vivid.pk}" not in html
        assert '<nav class="reiter unternavigation" aria-label="Bank">' in html
        assert f'href="{reverse(name)}" aria-current="page"' in html
    filter_seite = angemeldet.get(reverse("bank:umsaetze")).content.decode()
    assert f'<option value="{vivid.pk}">Vivid Test (Vivid)</option>' in filter_seite
    freigabe = angemeldet.get(reverse("bank:freigabe", args=[vr.pk])).content.decode()
    assert "Bankfreigabe · VR Test" in freigabe
    assert f'href="{reverse("bank:konten")}" aria-current="page"' in freigabe

    antwort = angemeldet.post(
        reverse("bank:freigabe", args=[vr.pk]), {"gueltig_bis": "2027-02-15"}, follow=True
    )
    assert "Freigabe für VR Test eingetragen, gilt bis 15.02.2027" in antwort.content.decode()
