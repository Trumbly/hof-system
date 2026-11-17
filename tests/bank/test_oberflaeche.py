"""Seiten der Bank: Konten, Umsätze mit Filter, Freigabe erneuern (Schritt 1, Aufgaben 7, 8)."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.bank import enablebanking, services
from apps.bank.models import Bankkonto
from tests.bank.conftest import umsatz

pytestmark = pytest.mark.django_db

TAG = datetime.date(2026, 11, 2)


@pytest.mark.parametrize("name", ["bank:konten", "bank:umsaetze", "bank:import"])
def test_seiten_nur_mit_anmeldung(client: Client, name: str) -> None:
    antwort = client.get(reverse(name))
    assert antwort.status_code == 302
    assert reverse("account_login") in antwort["Location"]


def test_konten_mit_saldo_und_letztem_abruf(angemeldet: Client, vivid: Bankkonto) -> None:
    """Schritt 1, Aufgabe 8: Konten mit Saldo und letztem Abruf."""
    from apps.bank.models import Bankabruf

    abruf = services.bankabruf_starten(vivid, Bankabruf.Quelle.ENABLE_BANKING)
    services.umsaetze_uebernehmen(vivid, [umsatz(TAG, "5.00")], abruf=abruf)
    services.bankabruf_beenden(abruf)
    services.kontostand_erfassen(vivid, TAG, Decimal("1234.56"))

    html = angemeldet.get(reverse("bank:konten")).content.decode()

    assert f'id="konto-{vivid.pk}"' in html
    assert "1.234,56" in html
    assert "02.11.2026" in html
    assert "erfolgreich" in html
    assert reverse("bank:freigabe", args=[vivid.pk]) in html


def test_umsaetze_mit_filter(angemeldet: Client, vivid: Bankkonto, vr: Bankkonto) -> None:
    """Schritt 1, Aufgabe 8: Umsätze mit Filter nach Konto, Zeitraum, Richtung und Suche."""
    services.umsaetze_uebernehmen(
        vivid,
        [
            umsatz(TAG, "10.00", zweck="Miete G-01"),
            umsatz(TAG, "3.00", eingang=False, zweck="Strom"),
        ],
    )
    services.umsaetze_uebernehmen(
        vr, [umsatz(TAG + datetime.timedelta(days=5), "7.00", zweck="Pacht")]
    )
    url = reverse("bank:umsaetze")

    alle = angemeldet.get(url).content.decode()
    assert "Miete G-01" in alle and "Pacht" in alle and "Strom" in alle

    nur_vivid = angemeldet.get(url, {"konto": vivid.pk}).content.decode()
    assert "Pacht" not in nur_vivid and "Miete G-01" in nur_vivid

    ausgaenge = angemeldet.get(url, {"richtung": "ausgang"}).content.decode()
    assert "Strom" in ausgaenge and "Miete G-01" not in ausgaenge

    zeitraum = angemeldet.get(url, {"von": "2026-11-05"}).content.decode()
    assert "Pacht" in zeitraum and "Strom" not in zeitraum

    suche = angemeldet.get(url, {"suche": "miete"}).content.decode()
    assert "Miete G-01" in suche and "Strom" not in suche


def test_freigabe_erneuern_ueber_enable_banking(
    angemeldet: Client, vivid: Bankkonto, fake: enablebanking.FakeEnableBanking
) -> None:
    """Schritt 1, Aufgabe 7: Der Link im Arbeitsposten führt zur Erneuerung; nach der
    Rückkehr von der Bank gilt die neue Freigabe, der Posten ist erledigt."""
    alte = services.aktuelle_freigabe(vivid)
    assert alte is not None
    (posten,) = services.freigaben_pruefen(alte.gueltig_bis)

    liste = angemeldet.get(reverse("kern:arbeitslisten")).content.decode()
    url = reverse("bank:freigabe", args=[vivid.pk])
    assert f'href="{url}"' in liste
    assert "Freigabe erneuern" in liste

    start = angemeldet.post(url)
    assert start.status_code == 302
    zur_bank = start["Location"]
    assert zur_bank.startswith("http://testserver/bank/freigabe/rueckkehr/?code=fake-")
    neu_bis = timezone.localdate() + datetime.timedelta(days=180)
    code = zur_bank.split("code=")[1].split("&")[0]
    fake.sitzungen[code] = enablebanking.Sitzung(
        sitzung_id="neu",
        gueltig_bis=neu_bis,
        konten=[enablebanking.SitzungsKonto(uid="uid-neu", iban=vivid.iban)],
    )

    zurueck = angemeldet.get(zur_bank)

    assert zurueck.status_code == 302
    neue = services.aktuelle_freigabe(vivid)
    assert neue is not None and neue.gueltig_bis == neu_bis and neue.sitzung_id == "neu"
    vivid.refresh_from_db()
    assert vivid.eb_konto_uid == "uid-neu"
    posten.refresh_from_db()
    assert posten.status == "erledigt"


def test_rueckkehr_ohne_passenden_state(angemeldet: Client, vivid: Bankkonto) -> None:
    antwort = angemeldet.get(reverse("bank:freigabe_rueckkehr"), {"code": "x", "state": "y"})
    assert antwort.status_code == 302
    assert vivid.freigaben.count() == 1


def test_rueckkehr_mit_fremdem_konto(
    angemeldet: Client, vivid: Bankkonto, fake: enablebanking.FakeEnableBanking
) -> None:
    start = angemeldet.post(reverse("bank:freigabe", args=[vivid.pk]))
    code = start["Location"].split("code=")[1].split("&")[0]
    fake.sitzungen[code] = enablebanking.Sitzung(
        sitzung_id="x",
        gueltig_bis=TAG,
        konten=[enablebanking.SitzungsKonto(uid="u", iban="DE02012345670000000006")],
    )
    angemeldet.get(start["Location"])
    assert vivid.freigaben.count() == 1


def test_freigabe_von_hand_fuer_vr(angemeldet: Client, vr: Bankkonto) -> None:
    """Schritt 1, Aufgabe 7: Für das VR-Konto (FinTS) wird der Ablauf eingetragen."""
    url = reverse("bank:freigabe", args=[vr.pk])
    assert "Eintragen" in angemeldet.get(url).content.decode()

    antwort = angemeldet.post(url, {"gueltig_bis": "2027-02-15"})

    assert antwort.status_code == 302
    freigabe = services.aktuelle_freigabe(vr)
    assert freigabe is not None and freigabe.gueltig_bis == datetime.date(2027, 2, 15)


def test_saldoposten_verweist_auf_umsaetze(angemeldet: Client, vivid: Bankkonto) -> None:
    services.kontostand_erfassen(vivid, TAG, Decimal("1.00"))
    services.kontostand_erfassen(vivid, TAG + datetime.timedelta(days=1), Decimal("2.00"))
    services.saldenabweichungen_melden(vivid)

    html = angemeldet.get(reverse("kern:arbeitslisten")).content.decode()

    assert "Umsätze ansehen" in html
    assert f"konto={vivid.pk}" in html


def test_navigation_zeigt_bank(angemeldet: Client) -> None:
    assert reverse("bank:konten") in angemeldet.get(reverse("kern:startseite")).content.decode()
