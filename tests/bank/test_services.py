"""Services der Bank: Fingerabdruck, Übernahme, Kontostände, Saldo-Prüfung, Freigaben."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from apps.bank import fingerabdruck, services
from apps.bank.models import Bankabruf, Bankfreigabe, Bankkonto, Bankumsatz, Kontostand
from apps.kern.models import Arbeitsposten
from tests.bank.conftest import umsatz

pytestmark = pytest.mark.django_db

TAG = datetime.date(2026, 11, 2)


# --- Fingerabdruck -------------------------------------------------------------------


def test_fingerabdruck_normalisiert_darstellung() -> None:
    """Schritt 1, Aufgabe 2: IBAN mit Leerzeichen, Zweck mit doppelten Leerzeichen und
    Betrag mit einer Nachkommastelle ergeben denselben Fingerabdruck."""
    a = umsatz(TAG, "12.5", iban="DE40 0123 4567 0000 0000 01", zweck="Miete  11/2026 ")
    b = umsatz(TAG, "12.50", iban="de40012345670000000001", zweck="Miete 11/2026")
    assert fingerabdruck.laufende_nummern("DE00X", [a]) == fingerabdruck.laufende_nummern(
        "DE00X", [b]
    )


@pytest.mark.parametrize(
    "aenderung",
    [
        {"buchungsdatum": TAG + datetime.timedelta(days=1)},
        {"betrag": Decimal("12.51")},
        {"richtung": "ausgang"},
        {"gegen_iban": "DE13012345670000000002"},
        {"verwendungszweck": "Miete 12/2026"},
    ],
)
def test_fingerabdruck_haengt_an_jedem_teil(aenderung: dict[str, object]) -> None:
    """Schritt 1, Aufgabe 2: Konto, Datum, Betrag, Richtung, Gegen-IBAN und Zweck gehen ein."""
    from dataclasses import replace

    basis = umsatz(TAG, "12.50")
    anders = replace(basis, **aenderung)  # type: ignore[arg-type]
    assert fingerabdruck.laufende_nummern("K", [basis]) != fingerabdruck.laufende_nummern(
        "K", [anders]
    )
    assert fingerabdruck.laufende_nummern("K1", [basis]) != fingerabdruck.laufende_nummern(
        "K2", [basis]
    )


def test_laufende_nummer_trennt_gleiche_umsaetze_am_tag() -> None:
    """Schritt 1, Aufgabe 2: Zwei gleiche Umsätze am selben Tag bekommen Nummer 1 und 2."""
    a = umsatz(TAG, "50.00")
    ergebnis = fingerabdruck.laufende_nummern("K", [a, umsatz(TAG, "9.99"), a])
    assert [n for n, _ in ergebnis] == [1, 1, 2]
    assert ergebnis[0][1] != ergebnis[2][1]


def test_unique_constraint_in_der_datenbank(vivid: Bankkonto) -> None:
    """Schritt 1, Aufgabe 2: Der Fingerabdruck ist in der Datenbank eindeutig."""
    ergebnis = services.umsaetze_uebernehmen(vivid, [umsatz(TAG, "10.00")])
    doppelt = Bankumsatz.objects.get(pk=ergebnis.umsaetze[0].pk)
    doppelt.pk = None
    with pytest.raises(IntegrityError), transaction.atomic():
        doppelt.save()


# --- Übernahme -----------------------------------------------------------------------


def test_zweimal_dieselbe_lieferung_keine_doppelten(vivid: Bankkonto) -> None:
    """Abnahme Schritt 1: Zweimaliger Abruf desselben Zeitraums erzeugt keine Doppelten
    (Ebene Service)."""
    lieferung = [umsatz(TAG, "10.00"), umsatz(TAG, "10.00"), umsatz(TAG, "7.00", eingang=False)]

    erste = services.umsaetze_uebernehmen(vivid, lieferung)
    zweite = services.umsaetze_uebernehmen(vivid, lieferung)

    assert (erste.geliefert, erste.neu, erste.doppelt) == (3, 3, 0)
    assert (zweite.geliefert, zweite.neu, zweite.doppelt) == (3, 0, 3)
    assert vivid.umsaetze.count() == 3
    assert sorted(vivid.umsaetze.values_list("laufende_nummer", flat=True)) == [1, 1, 2]


def test_doppelter_umsatz_spaeter_nachgeliefert(vivid: Bankkonto) -> None:
    """Sonderfall „doppelter Umsatz“: Kommt die zweite von zwei gleichen Zahlungen erst mit
    dem nächsten Abruf, ist sie neu; die erste bleibt einmal im System."""
    zahlung = umsatz(TAG, "10.00")
    services.umsaetze_uebernehmen(vivid, [zahlung])

    ergebnis = services.umsaetze_uebernehmen(vivid, [zahlung, zahlung])

    assert (ergebnis.neu, ergebnis.doppelt) == (1, 1)
    assert vivid.umsaetze.count() == 2


def test_uebernahme_zaehlt_am_abruf(vivid: Bankkonto) -> None:
    abruf = services.bankabruf_starten(vivid, Bankabruf.Quelle.ENABLE_BANKING, von=TAG, bis=TAG)
    services.umsaetze_uebernehmen(vivid, [umsatz(TAG, "1.00")], abruf=abruf)
    services.umsaetze_uebernehmen(vivid, [umsatz(TAG, "1.00")], abruf=abruf)
    services.bankabruf_beenden(abruf, meldung="ok")

    abruf.refresh_from_db()
    assert (abruf.geliefert, abruf.neu, abruf.doppelt) == (2, 1, 1)
    assert abruf.status == Bankabruf.Status.ERFOLGREICH
    assert vivid.umsaetze.get().abruf == abruf


def test_vorschau_schreibt_nichts(vivid: Bankkonto) -> None:
    services.umsaetze_uebernehmen(vivid, [umsatz(TAG, "1.00")])
    vorschau = services.umsaetze_vorschau(vivid, [umsatz(TAG, "1.00"), umsatz(TAG, "2.00")])
    assert (vorschau.geliefert, vorschau.neu, vorschau.doppelt) == (2, 1, 1)
    assert vivid.umsaetze.count() == 1


@pytest.mark.parametrize(
    ("betrag", "richtung", "meldung"),
    [
        ("-1.00", "eingang", "positiv"),
        ("1.001", "eingang", "zwei Nachkommastellen"),
        ("1.00", "seitwaerts", "Richtung"),
    ],
)
def test_ungueltige_umsaetze_werden_abgewiesen(
    vivid: Bankkonto, betrag: str, richtung: str, meldung: str
) -> None:
    daten = services.UmsatzDaten(
        buchungsdatum=TAG,
        betrag=Decimal(betrag),
        richtung=richtung,
        gegenpartei="Erika Beispiel",
    )
    with pytest.raises(services.BankFehler, match=meldung) as fehler:
        services.umsaetze_uebernehmen(vivid, [daten])
    assert "Erika" not in str(fehler.value)
    assert not vivid.umsaetze.exists()


# --- Kontostände und Saldo-Prüfung ---------------------------------------------------


def test_kontostand_aus_saldo_rechnet_auf_tagesende_des_vortags(vivid: Bankkonto) -> None:
    """Schritt 1, Aufgabe 6: Der Saldo jetzt enthält schon die heutigen Umsätze; der
    Tagesend-Saldo des Vortags ist Saldo minus heutige Umsätze."""
    heute = TAG
    services.umsaetze_uebernehmen(
        vivid, [umsatz(heute, "30.00"), umsatz(heute, "5.00", eingang=False)]
    )

    stand = services.kontostand_aus_saldo(vivid, Decimal("1025.00"), stand_am=heute)

    assert stand.datum == heute - datetime.timedelta(days=1)
    assert stand.saldo == Decimal("1000.00")


def test_kontostand_erfassen_aktualisiert_denselben_tag(vivid: Bankkonto) -> None:
    services.kontostand_erfassen(vivid, TAG, Decimal("1.00"))
    services.kontostand_erfassen(vivid, TAG, Decimal("2.00"))
    assert Kontostand.objects.get(konto=vivid, datum=TAG).saldo == Decimal("2.00")


def _konto_mit_salden(konto: Bankkonto) -> None:
    tag1, tag2, tag3 = TAG, TAG + datetime.timedelta(days=1), TAG + datetime.timedelta(days=2)
    services.umsaetze_uebernehmen(
        konto,
        [umsatz(tag2, "100.00"), umsatz(tag2, "40.00", eingang=False), umsatz(tag3, "10.00")],
    )
    services.kontostaende_erfassen(
        konto,
        {tag1: Decimal("500.00"), tag2: Decimal("560.00"), tag3: Decimal("570.00")},
    )


def test_saldenpruefung_ohne_abweichung(vivid: Bankkonto) -> None:
    _konto_mit_salden(vivid)
    assert services.saldenabweichungen(vivid) == []
    assert services.saldenabweichungen_melden(vivid) == []


def test_saldenpruefung_meldet_luecke_ohne_betraege(vivid: Bankkonto) -> None:
    """Schritt 1, Aufgabe 6: Laufen Saldo und Umsätze auseinander, entsteht ein Arbeitsposten
    mit Konto-ID und Datum, ohne Beträge im Text. Ein zweiter Lauf legt keinen weiteren an;
    passt der Tag wieder (Umsatz nachgeliefert), ist der Posten erledigt."""
    _konto_mit_salden(vivid)
    tag3 = TAG + datetime.timedelta(days=2)
    services.kontostand_erfassen(vivid, tag3, Decimal("580.00"))  # 10,00 fehlen

    posten = services.saldenabweichungen_melden(vivid)

    assert len(posten) == 1
    assert posten[0].art == services.ART_SALDO
    assert posten[0].bezug == Kontostand.objects.get(konto=vivid, datum=tag3)
    assert f"Konto #{vivid.pk}" in posten[0].hinweis
    assert f"{tag3:%d.%m.%Y}" in posten[0].hinweis
    for betrag in ("580", "570", "10,00", "10.00", "500"):
        assert betrag not in posten[0].hinweis
    assert services.saldenabweichungen_melden(vivid) == []

    services.umsaetze_uebernehmen(vivid, [umsatz(tag3, "10.00", zweck="nachgeliefert")])
    services.saldenabweichungen_melden(vivid)
    posten[0].refresh_from_db()
    assert posten[0].status == Arbeitsposten.Status.ERLEDIGT


# --- Bankfreigaben (H3.2) -------------------------------------------------------------


def test_freigabe_14_tage_vorher_ein_arbeitsposten(vivid: Bankkonto) -> None:
    """Abnahme Schritt 1: Eine ablaufende Bankfreigabe erzeugt 14 Tage vorher einen
    Arbeitsposten, mit Link zur Erneuerung. Der Lauf ist idempotent."""
    freigabe = services.aktuelle_freigabe(vivid)
    assert freigabe is not None
    ablauf = freigabe.gueltig_bis

    assert services.freigaben_pruefen(ablauf - datetime.timedelta(days=15)) == []
    posten = services.freigaben_pruefen(ablauf - datetime.timedelta(days=14))
    assert len(posten) == 1
    assert posten[0].art == services.ART_FREIGABE
    assert posten[0].bezug == freigabe
    assert posten[0].faellig_am == ablauf
    assert f"/bank/konten/{vivid.pk}/freigabe/" in posten[0].hinweis

    # Idempotent: auch an den Folgetagen kein zweiter Posten.
    assert services.freigaben_pruefen(ablauf - datetime.timedelta(days=13)) == []
    assert services.freigaben_pruefen(ablauf) == []
    assert Arbeitsposten.objects.filter(art=services.ART_FREIGABE).count() == 1


def test_neue_freigabe_erledigt_den_posten(vivid: Bankkonto) -> None:
    freigabe = services.aktuelle_freigabe(vivid)
    assert freigabe is not None
    (posten,) = services.freigaben_pruefen(freigabe.gueltig_bis)

    neu = services.bankfreigabe_anlegen(
        vivid, gueltig_bis=freigabe.gueltig_bis + datetime.timedelta(days=180)
    )

    posten.refresh_from_db()
    assert posten.status == Arbeitsposten.Status.ERLEDIGT
    assert services.aktuelle_freigabe(vivid) == neu
    assert services.freigaben_pruefen(freigabe.gueltig_bis) == []


def test_konto_ohne_freigabe_meldet_einmal(db: None) -> None:
    from tests.bank.conftest import neue_iban
    from tests.factories import MandantFabrik

    konto = services.bankkonto_anlegen(
        MandantFabrik(),
        bank="Vivid",
        bezeichnung="ohne",
        iban=neue_iban(),
        zugang=Bankkonto.Zugang.ENABLE_BANKING,
    )
    heute = datetime.date(2026, 11, 17)
    (posten,) = services.freigaben_pruefen(heute)
    assert posten.art == services.ART_FREIGABE_FEHLT
    assert services.freigaben_pruefen(heute) == []
    services.bankfreigabe_anlegen(konto, gueltig_bis=heute + datetime.timedelta(days=90))
    posten.refresh_from_db()
    assert posten.status == Arbeitsposten.Status.ERLEDIGT


def test_freigabe_gueltig_bis_vor_gueltig_ab(vivid: Bankkonto) -> None:
    with pytest.raises(services.BankFehler):
        services.bankfreigabe_anlegen(
            vivid, gueltig_ab=TAG, gueltig_bis=TAG - datetime.timedelta(days=1)
        )
    assert Bankfreigabe.objects.filter(konto=vivid).count() == 1


# --- Token ---------------------------------------------------------------------------


def test_token_nur_als_hash_und_nur_fuer_ein_konto(vr: Bankkonto, vivid: Bankkonto) -> None:
    """Schritt 1, Aufgabe 4: Gespeichert wird nur der Hash; ein neuer Token ersetzt den alten."""
    token = services.zulieferung_token_erzeugen(vr)
    vr.refresh_from_db()

    assert token.startswith(services.TOKEN_PRAEFIX)
    assert token not in vr.zulieferung_token_hash
    assert len(vr.zulieferung_token_hash) == 64
    assert services.konto_zum_token(token) == vr
    assert services.konto_zum_token(token + "x") is None
    assert services.konto_zum_token("") is None

    neuer = services.zulieferung_token_erzeugen(vr)
    assert services.konto_zum_token(token) is None
    assert services.konto_zum_token(neuer) == vr

    with pytest.raises(services.BankFehler):
        services.zulieferung_token_erzeugen(vivid)
