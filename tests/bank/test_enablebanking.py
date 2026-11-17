"""Client für Enable Banking gegen eine nachgebaute API (httpx.MockTransport), ohne Netz.

Der private Schlüssel entsteht nur im Test und wird nie gespeichert.
"""

from __future__ import annotations

import datetime
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import override_settings

from apps.bank import enablebanking
from apps.bank.enablebanking import EnableBankingClient, EnableBankingFehler
from tests.bank.conftest import eb_umsatz

APP_ID = "00000000-test-app-id"
TAG = datetime.date(2026, 11, 2)


@pytest.fixture(scope="module")
def schluessel() -> tuple[str, Any]:
    privat = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = privat.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    return pem, privat.public_key()


class NachgebauteApi:
    """Antwortet wie die API, merkt sich jede Anfrage."""

    def __init__(self, seiten: list[list[dict[str, Any]]], status: int = 200) -> None:
        self.seiten = seiten
        self.status = status
        self.anfragen: list[httpx.Request] = []

    def __call__(self, anfrage: httpx.Request) -> httpx.Response:
        self.anfragen.append(anfrage)
        if self.status != 200:
            # Fehlerinhalte können Daten enthalten; der Client darf sie nicht weitergeben.
            return httpx.Response(self.status, json={"message": "Erika Beispiel DE40..."})
        pfad = anfrage.url.path
        if pfad.endswith("/transactions"):
            schluessel = anfrage.url.params.get("continuation_key")
            index = int(schluessel) if schluessel else 0
            antwort: dict[str, Any] = {"transactions": self.seiten[index]}
            if index + 1 < len(self.seiten):
                antwort["continuation_key"] = str(index + 1)
            return httpx.Response(200, json=antwort)
        if pfad.endswith("/balances"):
            return httpx.Response(
                200,
                json={
                    "balances": [
                        {
                            "balance_amount": {"currency": "EUR", "amount": "99.00"},
                            "balance_type": "ITAV",
                        },
                        {
                            "balance_amount": {"currency": "EUR", "amount": "1234.56"},
                            "balance_type": "ITBD",
                            "reference_date": "2026-11-17",
                        },
                    ]
                },
            )
        if pfad == "/auth":
            return httpx.Response(
                200, json={"url": "https://bank.invalid/start", "authorization_id": "a"}
            )
        if pfad == "/sessions":
            return httpx.Response(
                200,
                json={
                    "session_id": "sitzung-1",
                    "access": {"valid_until": "2027-05-16T10:00:00.000000+00:00"},
                    "accounts": [
                        {"uid": "uid-1", "account_id": {"iban": "DE40012345670000000001"}}
                    ],
                },
            )
        return httpx.Response(404)


def _client(pem: str, api: NachgebauteApi) -> EnableBankingClient:
    return EnableBankingClient(
        app_id=APP_ID,
        privater_schluessel=pem,
        basis_url="https://api.enablebanking.invalid",
        transport=httpx.MockTransport(api),
    )


def test_jwt_rs256_mit_app_id(schluessel: tuple[str, Any]) -> None:
    """Schritt 1, Aufgabe 3: JWT mit RS256, kid = App-ID, iss/aud nach der Doku, höchstens
    24 Stunden gültig; signiert mit dem privaten Schlüssel der App."""
    pem, oeffentlich = schluessel
    token = _client(pem, NachgebauteApi([[]])).jwt(jetzt=1_700_000_000)

    kopf = jwt.get_unverified_header(token)
    assert kopf["alg"] == "RS256"
    assert kopf["kid"] == APP_ID
    assert kopf["typ"] == "JWT"
    inhalt = jwt.decode(
        token,
        oeffentlich,
        algorithms=["RS256"],
        audience="api.enablebanking.com",
        issuer="enablebanking.com",
        options={"verify_exp": False},
    )
    assert inhalt["iat"] == 1_700_000_000
    assert 0 < inhalt["exp"] - inhalt["iat"] <= 86400


def test_umsaetze_ueber_alle_seiten(schluessel: tuple[str, Any]) -> None:
    """Schritt 1, Aufgabe 3: Umsätze mit Paginierung über continuation_key."""
    pem, _ = schluessel
    seiten = [
        [eb_umsatz(TAG, "1.00"), eb_umsatz(TAG, "2.00")],
        [eb_umsatz(TAG, "3.00")],
        [eb_umsatz(TAG, "4.00", eingang=False)],
    ]
    api = NachgebauteApi(seiten)

    umsaetze = list(_client(pem, api).umsaetze("uid-1", TAG, TAG))

    assert [u["transaction_amount"]["amount"] for u in umsaetze] == ["1.00", "2.00", "3.00", "4.00"]
    assert len(api.anfragen) == 3
    erste, zweite, dritte = api.anfragen
    assert erste.url.path == "/accounts/uid-1/transactions"
    assert erste.url.params["date_from"] == "2026-11-02"
    assert erste.url.params["date_to"] == "2026-11-02"
    assert "continuation_key" not in erste.url.params
    assert zweite.url.params["continuation_key"] == "1"
    assert dritte.url.params["continuation_key"] == "2"
    assert erste.headers["Authorization"].startswith("Bearer ey")


def test_salden_und_bester_saldo(schluessel: tuple[str, Any]) -> None:
    """Schritt 1, Aufgabe 6: Salden lesen; bevorzugt der gebuchte Saldo (ITBD)."""
    pem, _ = schluessel
    salden = _client(pem, NachgebauteApi([[]])).salden("uid-1")
    besten = enablebanking.bester_saldo(salden)
    assert besten is not None
    assert besten.betrag == Decimal("1234.56")
    assert besten.art == "ITBD"
    assert besten.datum == datetime.date(2026, 11, 17)


def test_freigabe_und_sitzung(schluessel: tuple[str, Any]) -> None:
    """Schritt 1, Aufgabe 7: Freigabe starten (POST /auth) und Sitzung anlegen (POST /sessions)."""
    pem, _ = schluessel
    api = NachgebauteApi([[]])
    client = _client(pem, api)

    url = client.freigabe_starten(
        bank_name="Vivid Money",
        land="DE",
        redirect_url="https://hof.invalid/bank/freigabe/rueckkehr/",
        state="abc",
        gueltig_bis=datetime.datetime(2027, 5, 1, tzinfo=datetime.UTC),
    )
    sitzung = client.sitzung_anlegen("code-1")

    assert url == "https://bank.invalid/start"
    koerper = json.loads(api.anfragen[0].content)
    assert koerper["aspsp"] == {"name": "Vivid Money", "country": "DE"}
    assert koerper["state"] == "abc"
    assert koerper["access"]["valid_until"].startswith("2027-05-01")
    assert json.loads(api.anfragen[1].content) == {"code": "code-1"}
    assert sitzung.sitzung_id == "sitzung-1"
    assert sitzung.gueltig_bis == datetime.date(2027, 5, 16)
    assert sitzung.konten[0].uid == "uid-1"


def test_fehler_nennen_nur_den_status(schluessel: tuple[str, Any]) -> None:
    """Fehlermeldungen nennen den Statuscode, nie den Inhalt der Antwort."""
    pem, _ = schluessel
    with pytest.raises(EnableBankingFehler) as fehler:
        list(_client(pem, NachgebauteApi([[]], status=403)).umsaetze("uid-1", TAG, TAG))
    assert fehler.value.status == 403
    assert "HTTP 403" in str(fehler.value)
    assert "Erika" not in str(fehler.value)


def test_ohne_einrichtung_klare_meldung(tmp_path: Path) -> None:
    with (
        override_settings(ENABLE_BANKING_APP_ID="", ENABLE_BANKING_SCHLUESSEL=""),
        pytest.raises(EnableBankingFehler, match="nicht eingerichtet"),
    ):
        EnableBankingClient.aus_einstellungen()
    with (
        override_settings(
            ENABLE_BANKING_APP_ID=APP_ID, ENABLE_BANKING_SCHLUESSEL=str(tmp_path / "fehlt.pem")
        ),
        pytest.raises(EnableBankingFehler, match="lässt sich nicht lesen"),
    ):
        EnableBankingClient.aus_einstellungen()


def test_schluessel_aus_datei(tmp_path: Path, schluessel: tuple[str, Any]) -> None:
    """Schritt 1, Aufgabe 3: Schlüsselpfad und App-ID kommen aus den Einstellungen."""
    pem, _ = schluessel
    datei = tmp_path / "eb.pem"
    datei.write_text(pem, encoding="ascii")
    with override_settings(ENABLE_BANKING_APP_ID=APP_ID, ENABLE_BANKING_SCHLUESSEL=str(datei)):
        client = EnableBankingClient.aus_einstellungen()
    assert client.app_id == APP_ID
    assert jwt.get_unverified_header(client.jwt())["kid"] == APP_ID


def test_tests_nutzen_den_fake() -> None:
    """Keine echten Aufrufe: In Tests liefert client() den Fake."""
    assert isinstance(enablebanking.client(), enablebanking.FakeEnableBanking)


# --- Umwandlung --------------------------------------------------------------------------


def test_umsatz_aus_enablebanking_eingang_und_ausgang() -> None:
    eingang = enablebanking.umsatz_aus_enablebanking(eb_umsatz(TAG, "12.50", referenz="r1"))
    assert eingang.richtung == "eingang"
    assert eingang.betrag == Decimal("12.50")
    assert eingang.gegenpartei == "Erika Beispiel"
    assert eingang.gegen_iban == "DE40012345670000000001"
    assert eingang.verwendungszweck == "Miete 11/2026 G-01"
    assert eingang.bank_referenz == "r1"
    assert eingang.buchungsdatum == TAG

    ausgang = enablebanking.umsatz_aus_enablebanking(
        eb_umsatz(TAG, "8.00", eingang=False, name="Stadtwerke Testdorf")
    )
    assert ausgang.richtung == "ausgang"
    assert ausgang.gegenpartei == "Stadtwerke Testdorf"

    ohne_kennzeichen = eb_umsatz(TAG, "-3.00")
    del ohne_kennzeichen["credit_debit_indicator"]
    umgewandelt = enablebanking.umsatz_aus_enablebanking(ohne_kennzeichen)
    assert (umgewandelt.richtung, umgewandelt.betrag) == ("ausgang", Decimal("3.00"))


def test_vorgemerkte_umsaetze_zaehlen_nicht() -> None:
    assert enablebanking.ist_gebucht(eb_umsatz(TAG, "1.00"))
    assert not enablebanking.ist_gebucht(eb_umsatz(TAG, "1.00", status="PDNG"))


def test_unlesbarer_umsatz_nennt_nur_das_feld() -> None:
    kaputt = eb_umsatz(TAG, "kein-betrag")
    with pytest.raises(enablebanking.UmsatzUngueltig) as fehler:
        enablebanking.umsatz_aus_enablebanking(kaputt)
    assert str(fehler.value) == "transaction_amount"
