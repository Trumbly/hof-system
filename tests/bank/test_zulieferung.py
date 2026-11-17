"""Endpunkt /api/bank/zulieferung und Token-Befehl (Schritt 1, Aufgabe 4)."""

from __future__ import annotations

import datetime
import io
import json
from decimal import Decimal
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.urls import reverse

from apps.bank import services
from apps.bank.models import Bankabruf, Bankkonto, Kontostand
from apps.kern.models import Benutzer

pytestmark = pytest.mark.django_db

URL = "/api/bank/zulieferung"
HEUTE = datetime.date(2026, 11, 17)
GESTERN = HEUTE - datetime.timedelta(days=1)


def _lieferung(**extra: Any) -> dict[str, Any]:
    daten: dict[str, Any] = {
        "zeitraum": {"von": GESTERN.isoformat(), "bis": HEUTE.isoformat()},
        "umsaetze": [
            {
                "buchungsdatum": GESTERN.isoformat(),
                "betrag": "250.00",
                "gegenpartei": "Paul Erfunden",
                "gegen_iban": "DE13012345670000000002",
                "verwendungszweck": "Pacht 11/2026 F-01",
                "rohdaten": {"quelle": "test"},
            },
            {
                "buchungsdatum": GESTERN.isoformat(),
                "betrag": "-30.00",
                "gegenpartei": "Stadtwerke Testdorf",
                "verwendungszweck": "Wasser",
            },
            {
                "buchungsdatum": HEUTE.isoformat(),
                "betrag": "10.00",
                "richtung": "ausgang",
                "verwendungszweck": "Kontoführung",
            },
        ],
        "saldo": {"betrag": "1210.00", "stand_am": HEUTE.isoformat()},
    }
    daten.update(extra)
    return daten


def _senden(client: Client, daten: Any, token: str | None) -> Any:
    kopf = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token is not None else {}
    return client.post(URL, data=json.dumps(daten), content_type="application/json", **kopf)


@pytest.fixture
def token(vr: Bankkonto) -> str:
    return services.zulieferung_token_erzeugen(vr)


def test_url_und_ausnahme_von_der_totp_pflicht() -> None:
    """Der Endpunkt liegt unter /api/bank/zulieferung und ist von der TOTP-Middleware
    ausgenommen wie /gesund (ohne Sitzung)."""
    from apps.kern.middleware import OHNE_SITZUNG

    assert reverse("bank:zulieferung") == URL
    assert "bank:zulieferung" in OHNE_SITZUNG


def test_zulieferung_mit_token(client: Client, vr: Bankkonto, token: str) -> None:
    """Schritt 1, Aufgabe 4: Der hof-mac liefert Umsätze und Saldo mit eigenem Token."""
    antwort = _senden(client, _lieferung(), token)

    assert antwort.status_code == 200, antwort.content
    daten = antwort.json()
    assert (daten["geliefert"], daten["neu"], daten["doppelt"]) == (3, 3, 0)
    assert daten["konto"] == vr.pk
    assert vr.umsaetze.count() == 3
    assert sorted(vr.umsaetze.values_list("richtung", flat=True)) == [
        "ausgang",
        "ausgang",
        "eingang",
    ]
    abruf = Bankabruf.objects.get(pk=daten["abruf"])
    assert abruf.quelle == Bankabruf.Quelle.ZULIEFERUNG
    assert abruf.status == Bankabruf.Status.ERFOLGREICH
    # Saldo jetzt 1210,00 enthält die heutigen −10,00: Ende gestern 1220,00.
    stand = Kontostand.objects.get(konto=vr)
    assert (stand.datum, str(stand.saldo)) == (GESTERN, "1220.00")


def test_zulieferung_ist_idempotent(client: Client, vr: Bankkonto, token: str) -> None:
    """Schritt 1, Aufgabe 4: Dieselbe Lieferung zweimal ergibt keine Doppelten."""
    _senden(client, _lieferung(), token)
    antwort = _senden(client, _lieferung(), token)

    assert (antwort.json()["neu"], antwort.json()["doppelt"]) == (0, 3)
    assert vr.umsaetze.count() == 3


@pytest.mark.parametrize("falsch", [None, "", "hofz_falsch", "Basic abc"])
def test_ohne_gueltigen_token_401(client: Client, vr: Bankkonto, token: str, falsch: Any) -> None:
    """Schritt 1, Aufgabe 4: Ohne gültigen Token kommt 401 und nichts wird gespeichert."""
    if falsch == "Basic abc":
        antwort = client.post(
            URL,
            data=json.dumps(_lieferung()),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Basic {token}",
        )
    else:
        antwort = _senden(client, _lieferung(), falsch)
    assert antwort.status_code == 401
    assert antwort["WWW-Authenticate"] == "Bearer"
    assert not vr.umsaetze.exists()


def test_token_gilt_nur_fuer_sein_konto(client: Client, vr: Bankkonto, vivid: Bankkonto) -> None:
    """Schritt 1, Aufgabe 4: Ein Token gilt nur für ein Konto; ein inaktives Konto nimmt
    nichts mehr an."""
    token = services.zulieferung_token_erzeugen(vr)
    _senden(client, _lieferung(), token)
    assert vr.umsaetze.count() == 3
    assert not vivid.umsaetze.exists()

    services.bankkonto_aendern(vr, aktiv=False)
    assert _senden(client, _lieferung(), token).status_code == 401


def test_angemeldeter_benutzer_ohne_token_kommt_nicht_durch(
    angemeldet: Client, vr: Bankkonto
) -> None:
    """Die Sitzung ersetzt den Token nicht."""
    assert _senden(angemeldet, _lieferung(), None).status_code == 401


def test_nur_post(client: Client, token: str) -> None:
    antwort = client.get(URL, HTTP_AUTHORIZATION=f"Bearer {token}")
    assert antwort.status_code == 405


@pytest.mark.parametrize(
    ("aenderung", "erwartet"),
    [
        ({"umsaetze": "keine Liste"}, "umsaetze: Liste fehlt"),
        (
            {"umsaetze": [{"buchungsdatum": "17.11.2026", "betrag": "1.00"}]},
            "umsaetze[0].buchungsdatum",
        ),
        ({"umsaetze": [{"buchungsdatum": "2026-11-16", "betrag": "1.234"}]}, "umsaetze[0].betrag"),
        ({"umsaetze": [{"buchungsdatum": "2026-11-16", "betrag": "x"}]}, "umsaetze[0].betrag"),
        (
            {"umsaetze": [{"buchungsdatum": "2026-11-16", "betrag": "-1", "richtung": "eingang"}]},
            "umsaetze[0].richtung",
        ),
        (
            {"umsaetze": [{"buchungsdatum": "2026-10-01", "betrag": "1.00"}]},
            "vor zeitraum.von",
        ),
        ({"saldo": {"betrag": "1.00", "stand_am": "2026-11-18"}}, "saldo"),
    ],
)
def test_fehler_nennen_feld_aber_keine_werte(
    client: Client, vr: Bankkonto, token: str, aenderung: dict[str, Any], erwartet: str
) -> None:
    """Schritt 1, Aufgabe 4: Fehlermeldungen nennen Position und Feld, nie Rohdaten oder
    Personenfelder; bei einem Fehler wird nichts gespeichert."""
    lieferung = _lieferung(**aenderung)
    for eintrag in lieferung["umsaetze"] if isinstance(lieferung["umsaetze"], list) else []:
        eintrag.setdefault("gegenpartei", "Geheime Person")
        eintrag.setdefault("verwendungszweck", "Geheimer Zweck")

    antwort = _senden(client, lieferung, token)

    assert antwort.status_code == 400
    text = antwort.content.decode()
    assert erwartet in antwort.json()["fehler"]
    for wert in ("Geheime", "Paul", "Erfunden", "DE13", "250", "1.234", "17.11.2026"):
        assert wert not in text
    assert not vr.umsaetze.exists()


def test_kaputtes_json(client: Client, token: str) -> None:
    antwort = client.post(
        URL,
        data="{kein json",
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert antwort.status_code == 400


def test_zulieferung_meldet_saldoabweichung(client: Client, vr: Bankkonto, token: str) -> None:
    """Schritt 1, Aufgabe 6: Auch die Zulieferung prüft den Saldo des VR-Kontos."""
    services.kontostand_erfassen(vr, GESTERN - datetime.timedelta(days=1), Decimal("1000.00"))
    antwort = _senden(client, _lieferung(), token)  # erwartet 1220,00, Bank sagt 1220,00
    assert antwort.json()["arbeitsposten"] == 0

    lieferung = _lieferung(saldo={"betrag": "1500.00", "stand_am": HEUTE.isoformat()})
    antwort = _senden(client, lieferung, token)
    assert antwort.json()["arbeitsposten"] == 1


# --- Token-Befehl ------------------------------------------------------------------------


def test_token_befehl_gibt_token_einmal_aus(vr: Bankkonto) -> None:
    """Schritt 1, Aufgabe 4: Der Befehl legt den Token an und gibt ihn genau einmal aus;
    gespeichert ist nur der Hash."""
    ausgabe = io.StringIO()
    call_command("bank_zulieferung_token", "--konto", str(vr.pk), stdout=ausgabe)

    zeilen = ausgabe.getvalue().splitlines()
    token = zeilen[-1]
    assert token.startswith("hofz_")
    assert sum(token in z for z in zeilen) == 1
    vr.refresh_from_db()
    assert token not in vr.zulieferung_token_hash
    assert services.konto_zum_token(token) == vr


def test_token_befehl_nur_fuer_zulieferungskonten(vivid: Bankkonto) -> None:
    with pytest.raises(CommandError, match="keinen Zugang über Zulieferung"):
        call_command("bank_zulieferung_token", "--konto", str(vivid.pk), stdout=io.StringIO())
    with pytest.raises(CommandError, match="gibt es nicht"):
        call_command("bank_zulieferung_token", "--konto", "999999", stdout=io.StringIO())


def test_zulieferung_laedt_keine_sitzung(client: Client, token: str, benutzer: Benutzer) -> None:
    """Wie /gesund: Ohne Sitzung, ohne Umleitung zur Anmeldung."""
    antwort = _senden(client, _lieferung(), token)
    assert antwort.status_code == 200
    assert "sessionid" not in antwort.cookies
