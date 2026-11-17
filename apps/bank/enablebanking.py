"""Anbindung an Enable Banking (PSD2, nur lesend) für Vivid.

Nach der öffentlichen Doku (https://enablebanking.com/docs/api/reference/):

- Jede Anfrage trägt ein JWT (RS256) mit ``kid`` = App-ID, ``iss`` = „enablebanking.com“,
  ``aud`` = „api.enablebanking.com“, höchstens 24 Stunden gültig. Signiert wird mit dem
  privaten Schlüssel der App.
- ``POST /auth`` startet die Freigabe bei der Bank und liefert die URL, auf die der
  Benutzer geht. Die Bank leitet danach mit ``?code=…&state=…`` zurück.
- ``POST /sessions`` tauscht den Code gegen eine Sitzung mit Konten und ``valid_until``.
- ``GET /accounts/{uid}/transactions`` liefert Umsätze seitenweise (``continuation_key``).
- ``GET /accounts/{uid}/balances`` liefert die Salden.

Der Client ist austauschbar: Läufe und Seiten holen ihn über ``client()``, das die Klasse
aus der Einstellung ``HOF_BANK_CLIENT`` nimmt. In Entwicklung und Tests ist das
``FakeEnableBanking``, der nie ins Netz geht.

Fehlermeldungen nennen nur Statuscodes und Pfade ohne Kontonummern, nie Inhalte der
Antwort (sie können Namen und Zwecke enthalten).
"""

from __future__ import annotations

import datetime
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol

import httpx
import jwt
from django.conf import settings
from django.utils.module_loading import import_string

from apps.bank.models import Bankumsatz
from apps.bank.services import UmsatzDaten

JWT_GUELTIG_SEKUNDEN = 3600
MAX_SEITEN = 500

#: Salden in dieser Reihenfolge bevorzugt: nur gebuchte Umsätze, wie der Abruf sie holt.
SALDO_ARTEN = ("ITBD", "CLBD", "XPCD", "OPBD", "ITAV", "CLAV")


class EnableBankingFehler(Exception):
    """Ein Aufruf ist gescheitert. Die Meldung nennt nur Statuscode und Art."""

    def __init__(self, meldung: str, status: int | None = None) -> None:
        super().__init__(meldung)
        self.status = status


@dataclass(frozen=True)
class SitzungsKonto:
    uid: str
    iban: str


@dataclass(frozen=True)
class Sitzung:
    sitzung_id: str
    gueltig_bis: datetime.date
    konten: list[SitzungsKonto]


@dataclass(frozen=True)
class Saldo:
    betrag: Decimal
    art: str
    datum: datetime.date | None = None


class BankClient(Protocol):
    """Was das Hof-System von Enable Banking braucht."""

    def freigabe_starten(
        self,
        *,
        bank_name: str,
        land: str,
        redirect_url: str,
        state: str,
        gueltig_bis: datetime.datetime,
    ) -> str:
        """Startet die Freigabe; Rückgabe ist die URL der Bank für den Benutzer."""
        ...

    def sitzung_anlegen(self, code: str) -> Sitzung: ...

    def umsaetze(
        self, konto_uid: str, von: datetime.date, bis: datetime.date
    ) -> Iterator[dict[str, Any]]:
        """Alle Umsätze im Zeitraum, über alle Seiten (``continuation_key``)."""
        ...

    def salden(self, konto_uid: str) -> list[Saldo]: ...


# --- Echter Client ------------------------------------------------------------------


class EnableBankingClient:
    """HTTP-Client für die API von Enable Banking."""

    def __init__(
        self,
        *,
        app_id: str,
        privater_schluessel: str,
        basis_url: str = "https://api.enablebanking.com",
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        if not app_id or not privater_schluessel:
            raise EnableBankingFehler(
                "Enable Banking ist nicht eingerichtet (ENABLE_BANKING_APP_ID und "
                "ENABLE_BANKING_SCHLUESSEL)."
            )
        self.app_id = app_id
        self._schluessel = privater_schluessel
        self._http = httpx.Client(base_url=basis_url, transport=transport, timeout=timeout)

    @classmethod
    def aus_einstellungen(cls) -> EnableBankingClient:
        pfad = settings.ENABLE_BANKING_SCHLUESSEL
        try:
            schluessel = Path(pfad).read_text(encoding="ascii") if pfad else ""
        except OSError as fehler:
            raise EnableBankingFehler(
                "Der private Schlüssel für Enable Banking lässt sich nicht lesen "
                "(ENABLE_BANKING_SCHLUESSEL)."
            ) from fehler
        return cls(
            app_id=settings.ENABLE_BANKING_APP_ID,
            privater_schluessel=schluessel,
            basis_url=settings.ENABLE_BANKING_URL,
        )

    def jwt(self, jetzt: int | None = None) -> str:
        iat = int(time.time()) if jetzt is None else jetzt
        return jwt.encode(
            {
                "iss": "enablebanking.com",
                "aud": "api.enablebanking.com",
                "iat": iat,
                "exp": iat + JWT_GUELTIG_SEKUNDEN,
            },
            self._schluessel,
            algorithm="RS256",
            headers={"kid": self.app_id},
        )

    def _anfrage(self, methode: str, pfad: str, *, art: str, **kwargs: Any) -> dict[str, Any]:
        try:
            antwort = self._http.request(
                methode, pfad, headers={"Authorization": f"Bearer {self.jwt()}"}, **kwargs
            )
        except httpx.HTTPError as fehler:
            raise EnableBankingFehler(
                f"{art}: keine Verbindung ({type(fehler).__name__})"
            ) from fehler
        if antwort.status_code >= 400:
            raise EnableBankingFehler(
                f"{art}: HTTP {antwort.status_code}", status=antwort.status_code
            )
        try:
            daten = antwort.json()
        except ValueError as fehler:
            raise EnableBankingFehler(f"{art}: Antwort ist kein JSON") from fehler
        if not isinstance(daten, dict):
            raise EnableBankingFehler(f"{art}: unerwartete Antwort")
        return daten

    def freigabe_starten(
        self,
        *,
        bank_name: str,
        land: str,
        redirect_url: str,
        state: str,
        gueltig_bis: datetime.datetime,
    ) -> str:
        daten = self._anfrage(
            "POST",
            "/auth",
            art="Freigabe starten",
            json={
                "access": {"valid_until": gueltig_bis.isoformat()},
                "aspsp": {"name": bank_name, "country": land},
                "state": state,
                "redirect_url": redirect_url,
                "psu_type": "business",
            },
        )
        url = daten.get("url")
        if not isinstance(url, str):
            raise EnableBankingFehler("Freigabe starten: keine URL in der Antwort")
        return url

    def sitzung_anlegen(self, code: str) -> Sitzung:
        daten = self._anfrage("POST", "/sessions", art="Sitzung anlegen", json={"code": code})
        try:
            gueltig_bis = datetime.datetime.fromisoformat(daten["access"]["valid_until"]).date()
            konten = [
                SitzungsKonto(
                    uid=str(k["uid"]),
                    iban=str((k.get("account_id") or {}).get("iban") or ""),
                )
                for k in daten.get("accounts") or []
            ]
            return Sitzung(
                sitzung_id=str(daten["session_id"]), gueltig_bis=gueltig_bis, konten=konten
            )
        except (KeyError, TypeError, ValueError) as fehler:
            raise EnableBankingFehler("Sitzung anlegen: unerwartete Antwort") from fehler

    def umsaetze(
        self, konto_uid: str, von: datetime.date, bis: datetime.date
    ) -> Iterator[dict[str, Any]]:
        parameter: dict[str, str] = {"date_from": von.isoformat(), "date_to": bis.isoformat()}
        for _ in range(MAX_SEITEN):
            daten = self._anfrage(
                "GET",
                f"/accounts/{konto_uid}/transactions",
                art="Umsätze abrufen",
                params=parameter,
            )
            for umsatz in daten.get("transactions") or []:
                if isinstance(umsatz, dict):
                    yield umsatz
            weiter = daten.get("continuation_key")
            if not weiter:
                return
            parameter = {**parameter, "continuation_key": str(weiter)}
        raise EnableBankingFehler(f"Umsätze abrufen: mehr als {MAX_SEITEN} Seiten")

    def salden(self, konto_uid: str) -> list[Saldo]:
        daten = self._anfrage("GET", f"/accounts/{konto_uid}/balances", art="Salden abrufen")
        return [s for s in (_saldo(b) for b in daten.get("balances") or []) if s is not None]


def _saldo(daten: Any) -> Saldo | None:
    try:
        betrag = Decimal(str(daten["balance_amount"]["amount"]))
        datum_text = daten.get("reference_date")
        datum = datetime.date.fromisoformat(datum_text) if datum_text else None
        return Saldo(betrag=betrag, art=str(daten.get("balance_type") or ""), datum=datum)
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return None


def bester_saldo(salden: list[Saldo]) -> Saldo | None:
    """Der Saldo der gebuchten Umsätze (siehe ``SALDO_ARTEN``)."""
    for art in SALDO_ARTEN:
        for saldo in salden:
            if saldo.art == art:
                return saldo
    return None


# --- Umsätze in das Format des Hof-Systems -------------------------------------------


class UmsatzUngueltig(ValueError):
    """Ein Umsatz der Bank lässt sich nicht lesen. Die Meldung nennt nur das Feld."""


def ist_gebucht(umsatz: dict[str, Any]) -> bool:
    return str(umsatz.get("status") or "BOOK").upper() == "BOOK"


def umsatz_aus_enablebanking(umsatz: dict[str, Any]) -> UmsatzDaten:
    """Wandelt einen Umsatz der API in ``UmsatzDaten`` (Betrag positiv, Richtung extra)."""
    try:
        betrag = Decimal(str(umsatz["transaction_amount"]["amount"]))
    except (KeyError, TypeError, InvalidOperation) as fehler:
        raise UmsatzUngueltig("transaction_amount") from fehler
    kennzeichen = str(umsatz.get("credit_debit_indicator") or "").upper()
    if kennzeichen == "CRDT":
        richtung = Bankumsatz.Richtung.EINGANG
    elif kennzeichen == "DBIT":
        richtung = Bankumsatz.Richtung.AUSGANG
    else:
        richtung = Bankumsatz.Richtung.AUSGANG if betrag < 0 else Bankumsatz.Richtung.EINGANG
    gegen = "debtor" if richtung == Bankumsatz.Richtung.EINGANG else "creditor"
    datum_text = umsatz.get("booking_date") or umsatz.get("value_date")
    try:
        buchungsdatum = datetime.date.fromisoformat(str(datum_text))
        valuta_text = umsatz.get("value_date")
        valuta = datetime.date.fromisoformat(str(valuta_text)) if valuta_text else None
    except ValueError as fehler:
        raise UmsatzUngueltig("booking_date") from fehler
    zweck = umsatz.get("remittance_information") or []
    if isinstance(zweck, str):
        zweck = [zweck]
    return UmsatzDaten(
        buchungsdatum=buchungsdatum,
        betrag=abs(betrag).quantize(Decimal("0.01")),
        richtung=richtung,
        gegenpartei=str((umsatz.get(gegen) or {}).get("name") or ""),
        gegen_iban=str((umsatz.get(f"{gegen}_account") or {}).get("iban") or ""),
        verwendungszweck=" ".join(str(z) for z in zweck),
        valuta=valuta,
        bank_referenz=str(umsatz.get("entry_reference") or ""),
        waehrung=str((umsatz.get("transaction_amount") or {}).get("currency") or "EUR"),
        rohdaten=umsatz,
    )


# --- Fake für Entwicklung und Tests --------------------------------------------------


@dataclass
class FakeEnableBanking:
    """Fake ohne Netz: Umsätze und Salden je Konto-UID aus dem Speicher.

    ``seitengroesse`` teilt die Umsätze in Seiten mit ``continuation_key`` auf, wie die
    echte API. ``fehler`` lässt jeden Aufruf mit dieser Meldung scheitern.
    """

    umsaetze_je_konto: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    salden_je_konto: dict[str, list[Saldo]] = field(default_factory=dict)
    sitzungen: dict[str, Sitzung] = field(default_factory=dict)
    seitengroesse: int = 50
    fehler: str | None = None
    aufrufe: list[tuple[str, Any]] = field(default_factory=list)

    @classmethod
    def aus_einstellungen(cls) -> FakeEnableBanking:
        return cls()

    def _pruefen(self) -> None:
        if self.fehler:
            raise EnableBankingFehler(self.fehler, status=500)

    def freigabe_starten(
        self,
        *,
        bank_name: str,
        land: str,
        redirect_url: str,
        state: str,
        gueltig_bis: datetime.datetime,
    ) -> str:
        self._pruefen()
        self.aufrufe.append(("freigabe_starten", (bank_name, land, redirect_url, state)))
        return f"{redirect_url}?code=fake-{uuid.uuid4().hex[:8]}&state={state}"

    def sitzung_anlegen(self, code: str) -> Sitzung:
        self._pruefen()
        self.aufrufe.append(("sitzung_anlegen", code))
        if code in self.sitzungen:
            return self.sitzungen[code]
        return Sitzung(
            sitzung_id=f"fake-sitzung-{code}",
            gueltig_bis=datetime.date.today() + datetime.timedelta(days=180),
            konten=[],
        )

    def umsaetze(
        self, konto_uid: str, von: datetime.date, bis: datetime.date
    ) -> Iterator[dict[str, Any]]:
        self._pruefen()
        self.aufrufe.append(("umsaetze", (konto_uid, von, bis)))
        passend = [
            u
            for u in self.umsaetze_je_konto.get(konto_uid, [])
            if von.isoformat() <= str(u.get("booking_date")) <= bis.isoformat()
        ]
        for start in range(0, len(passend), self.seitengroesse):
            self.aufrufe.append(("seite", start // self.seitengroesse))
            yield from passend[start : start + self.seitengroesse]

    def salden(self, konto_uid: str) -> list[Saldo]:
        self._pruefen()
        self.aufrufe.append(("salden", konto_uid))
        return list(self.salden_je_konto.get(konto_uid, []))


def client() -> BankClient:
    """Der eingestellte Client (``HOF_BANK_CLIENT``), für Läufe und Seiten."""
    klasse = import_string(settings.HOF_BANK_CLIENT)
    instanz: BankClient = klasse.aus_einstellungen()
    return instanz
