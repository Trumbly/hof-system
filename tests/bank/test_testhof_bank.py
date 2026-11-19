"""Testhof-Modul der Bank (Schritt 1, Aufgabe 9) und die Abnahme gegen den Testhof."""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any

import pytest
from django.core.management import call_command

from apps.bank import enablebanking, services
from apps.bank.models import Bankabruf, Bankkonto, Bankumsatz, Kontostand
from apps.kern import zeit
from apps.kern.models import Arbeitsposten
from testhof import aufbau, erfunden, register, sonderfaelle
from testhof import bank as testhof_bank
from testhof.aufbau import Testhof


def test_zwei_konten_24_monate_rund_1800_umsaetze(testhof: Testhof) -> None:
    """Schritt 1, Aufgabe 9: 2 Konten, 24 Monate bis zum Stichtag, rund 1.800 Umsätze."""
    bank = testhof.bank
    assert set(bank.konten) == {"vivid", "vr"}
    assert Bankkonto.objects.count() == 2
    assert bank.beginn == datetime.date(2024, 11, 1)
    assert bank.ende == aufbau.STICHTAG - datetime.timedelta(days=1)
    anzahl = Bankumsatz.objects.count()
    assert 1700 <= anzahl <= 1900
    tage = Bankumsatz.objects.dates("buchungsdatum", "month")
    assert len(tage) == 24
    assert min(tage) == bank.beginn
    zugang = {k: v.zugang for k, v in bank.konten.items()}
    assert zugang == {"vivid": "enable_banking", "vr": "zulieferung"}


def test_nur_erfundene_ibans(testhof: Testhof) -> None:
    """Testdaten: nur Test-IBANs mit erfundener Bankleitzahl."""
    for iban in Bankkonto.objects.values_list("iban", flat=True):
        assert erfunden.ist_erfundene_iban(iban)
    for iban in Bankumsatz.objects.values_list("gegen_iban", flat=True).distinct():
        assert erfunden.ist_erfundene_iban(iban), iban


def test_daten_entstehen_ueber_die_services(monkeypatch: pytest.MonkeyPatch, db: None) -> None:
    """Schritt 1, Aufgabe 9: Das Testhof-Modul schreibt nur über apps.bank.services."""
    aufrufe: list[str] = []
    for name in (
        "bankkonto_anlegen",
        "bankabruf_starten",
        "umsaetze_uebernehmen",
        "kontostaende_erfassen",
        "bankfreigabe_anlegen",
    ):
        original = getattr(services, name)

        def mitschreiben(*args: Any, _n: str = name, _f: Any = original, **kw: Any) -> Any:
            aufrufe.append(_n)
            return _f(*args, **kw)

        monkeypatch.setattr(services, name, mitschreiben)

    aufbau.aufbauen(seed=42)

    assert aufrufe.count("bankkonto_anlegen") == 2
    assert aufrufe.count("umsaetze_uebernehmen") == 48
    assert aufrufe.count("kontostaende_erfassen") == 2
    assert aufrufe.count("bankfreigabe_anlegen") == 2


def test_sonderfaelle_angemeldet() -> None:
    """Schritt 1, Aufgabe 9: Das Modul erzeugt „doppelter Umsatz“ und „Rückbuchung“."""
    module = sonderfaelle.erzeugende_module()
    assert module["doppelter_umsatz"] == ["bank"]
    assert module["rueckbuchung"] == ["bank"]
    assert register.laden()["bank"].abhaengig_von == ("kern", "soll")


def test_sonderfall_doppelter_umsatz(testhof: Testhof) -> None:
    """Sonderfall „Doppelter Umsatz nach erneutem Abruf“: Die Lieferungen überlappen, der
    Fingerabdruck lässt jeden Umsatz nur einmal ins System. Zwei echt gleiche Zahlungen am
    selben Tag bleiben beide."""
    bank = testhof.bank
    assert bank.doppelt_geliefert > 10
    gesamt = Bankabruf.objects.filter(konto__in=bank.konten.values())
    assert sum(a.doppelt for a in gesamt) == bank.doppelt_geliefert
    assert sum(a.neu for a in gesamt) == Bankumsatz.objects.count()

    erste, zweite = bank.gleiche_zahlungen
    assert (erste.laufende_nummer, zweite.laufende_nummer) == (1, 2)
    assert erste.fingerabdruck != zweite.fingerabdruck
    for feld in ("buchungsdatum", "betrag", "richtung", "gegen_iban", "verwendungszweck"):
        assert getattr(erste, feld) == getattr(zweite, feld)


def test_erneuter_abruf_im_testhof_ohne_doppelte(testhof: Testhof) -> None:
    """Abnahme Schritt 1: Zweimaliger Abruf desselben Zeitraums erzeugt keine Doppelten,
    hier mit einem ganzen Monat des Testhofs, erneut geliefert."""
    vivid = testhof.bank.konten["vivid"]
    monat = list(vivid.umsaetze.filter(buchungsdatum__year=2026, buchungsdatum__month=5))
    lieferung = [
        services.UmsatzDaten(
            buchungsdatum=u.buchungsdatum,
            betrag=u.betrag,
            richtung=u.richtung,
            gegen_iban=u.gegen_iban,
            verwendungszweck=u.verwendungszweck,
        )
        for u in sorted(monat, key=lambda u: (u.buchungsdatum, u.pk))
    ]
    vorher = Bankumsatz.objects.count()

    ergebnis = services.umsaetze_uebernehmen(vivid, lieferung)

    assert (ergebnis.neu, ergebnis.doppelt) == (0, len(monat))
    assert Bankumsatz.objects.count() == vorher


def test_sonderfall_rueckbuchung(testhof: Testhof) -> None:
    """Sonderfall „Rückbuchung einer Zahlung“: Eingang und Ausgang über denselben Betrag
    von derselben Gegen-IBAN, wenige Tage später."""
    eingang, zurueck = testhof.bank.rueckbuchung
    assert eingang.richtung == "eingang" and zurueck.richtung == "ausgang"
    assert eingang.betrag == zurueck.betrag
    assert eingang.gegen_iban == zurueck.gegen_iban
    assert 0 < (zurueck.buchungsdatum - eingang.buchungsdatum).days <= 5
    assert zurueck.verwendungszweck.startswith("Rückbuchung")


def test_saldo_und_umsaetze_stimmen_je_tag_und_luecke_wird_arbeitsposten(
    testhof: Testhof,
) -> None:
    """Abnahme Schritt 1: Saldo und Umsätze stimmen im Testhof je Tag überein, und eine
    absichtliche Lücke wird ein Arbeitsposten (mit Konto-ID und Datum, ohne Beträge)."""
    bank = testhof.bank
    tage = (bank.ende - bank.beginn).days + 1
    for konto in bank.konten.values():
        assert konto.kontostaende.count() == tage + 1  # dazu der Anfangssaldo am Vortag
        assert services.saldenabweichungen(konto) == []
        assert services.saldenabweichungen_melden(konto) == []

    # Absichtliche Lücke: Ein Umsatz fehlt (die Bank hat ihn nie geliefert).
    vr = bank.konten["vr"]
    fehlt = vr.umsaetze.filter(buchungsdatum__year=2026, buchungsdatum__month=8).first()
    assert fehlt is not None
    datum = fehlt.buchungsdatum
    betrag = fehlt.betrag
    fehlt.delete()

    posten = services.saldenabweichungen_melden(vr)

    assert len(posten) == 1
    assert posten[0].art == services.ART_SALDO
    assert posten[0].bezug == Kontostand.objects.get(konto=vr, datum=datum)
    assert f"Konto #{vr.pk}" in posten[0].hinweis
    assert f"{datum:%d.%m.%Y}" in posten[0].hinweis
    assert str(betrag) not in posten[0].hinweis
    assert f"{betrag:.2f}".replace(".", ",") not in posten[0].hinweis
    assert services.saldenabweichungen(bank.konten["vivid"]) == []


def test_freigabe_im_testhof_erzeugt_arbeitsposten(testhof: Testhof) -> None:
    """Abnahme Schritt 1: Eine ablaufende Bankfreigabe erzeugt 14 Tage vorher einen
    Arbeitsposten. Im Testhof läuft die Vivid-Freigabe 10 Tage nach dem Stichtag ab."""
    stichtag = aufbau.STICHTAG
    vivid = testhof.bank.freigaben["vivid"]
    assert vivid.gueltig_bis == stichtag + datetime.timedelta(days=10)

    posten = services.freigaben_pruefen(stichtag)

    assert [p.bezug for p in posten] == [vivid]
    assert posten[0].faellig_am == vivid.gueltig_bis
    assert services.freigaben_pruefen(stichtag) == []


def test_bank_abruf_im_testhof(testhof: Testhof, monkeypatch: pytest.MonkeyPatch) -> None:
    """Der Lauf bank_abruf läuft gegen den Testhof: Ein erneuter Abruf bekannter Umsätze
    bringt nichts Neues; die Saldo-Prüfung findet keine Abweichung."""
    vivid = testhof.bank.konten["vivid"]
    heute = zeit.heute()
    services.bankfreigabe_anlegen(vivid, gueltig_bis=heute + datetime.timedelta(days=100))
    letzte = list(vivid.umsaetze.filter(buchungsdatum__gte=datetime.date(2026, 10, 20)))
    fake = enablebanking.FakeEnableBanking(
        umsaetze_je_konto={
            "testhof-vivid-uid": [
                {
                    "transaction_amount": {"amount": str(u.betrag), "currency": "EUR"},
                    "credit_debit_indicator": "CRDT" if u.richtung == "eingang" else "DBIT",
                    "booking_date": u.buchungsdatum.isoformat(),
                    "debtor_account" if u.richtung == "eingang" else "creditor_account": {
                        "iban": u.gegen_iban
                    },
                    "remittance_information": [u.verwendungszweck],
                }
                for u in sorted(letzte, key=lambda u: (u.buchungsdatum, u.pk))
            ]
        }
    )
    monkeypatch.setattr(enablebanking, "client", lambda: fake)

    call_command(
        "bank_abruf", "--konto", str(vivid.pk), "--von", "2026-10-20", "--bis", "2026-10-31"
    )

    abruf = Bankabruf.objects.filter(konto=vivid).order_by("-pk").first()
    assert abruf is not None
    assert (abruf.neu, abruf.doppelt) == (0, len(letzte))
    assert not Arbeitsposten.objects.filter(art=services.ART_SALDO).exists()


def test_kontostand_anfang_und_ende(testhof: Testhof) -> None:
    for plan in testhof_bank.PLAENE:
        konto = testhof.bank.konten[plan.schluessel]
        anfang = konto.kontostaende.order_by("datum").first()
        assert anfang is not None
        assert anfang.saldo == plan.anfangssaldo
        summe = sum(
            (u.betrag if u.richtung == "eingang" else -u.betrag for u in konto.umsaetze.all()),
            Decimal("0"),
        )
        ende = konto.kontostaende.order_by("-datum").first()
        assert ende is not None
        assert ende.saldo == plan.anfangssaldo + summe
