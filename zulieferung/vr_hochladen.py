#!/usr/bin/env python3
"""Zulieferung des VR-Kontos vom hof-mac an das Hof-System (H3.1).

Das VR-Konto läuft über FinTS und bleibt bis zum Ende des Kontos (1. Quartal 2027) auf
dem hof-mac; die PIN kommt nie auf den Server. Dieses Skript holt die Umsätze über den
bestehenden FinTS-Abruf von hof-fints und schickt sie per HTTPS über Tailscale an
``/api/bank/zulieferung``. Es braucht nur die Standardbibliothek von Python 3.10+.

Ablauf
------
1. Die FinTS-Quelle (ein Adapter, siehe unten) liefert die Umsätze der letzten
   ``--tage`` Tage (Standard 14) bis heute, dazu den aktuellen Saldo.
2. Das Skript schickt sie als eine Lieferung. Der Server nimmt jeden Umsatz nur einmal
   an (Fingerabdruck); ein erneuter Lauf über denselben Zeitraum ist also harmlos.
3. Ausgabe: nur Zähler. Namen, IBANs, Beträge oder Zwecke erscheinen nie, auch nicht in
   Fehlermeldungen. ``--probelauf`` zählt nur und schickt nichts.

Einrichtung auf dem hof-mac
---------------------------
1. Token auf dem Server erzeugen (gilt nur für das VR-Konto, erscheint genau einmal)::

       compose.sh exec app python manage.py bank_zulieferung_token --konto <id>

   Ihn auf dem hof-mac in eine Datei schreiben, nur für Max lesbar::

       mkdir -p ~/.config/hof && chmod 700 ~/.config/hof
       pbpaste > ~/.config/hof/zulieferung-token && chmod 600 ~/.config/hof/zulieferung-token

2. Den FinTS-Abruf von hof-fints anschließen: eine kleine Datei, z. B.
   ``~/hof-fints/hof_adapter.py``, mit einer Funktion ``umsaetze_abrufen(von, bis)``.
   Sie ruft den vorhandenen Abruf auf und gibt die Umsätze in der Form unten zurück.
   Nutzt hof-fints die Bibliothek ``python-fints`` (Umsätze als MT940-Transaktionen,
   Saldo über ``get_balance``), genügen die Helfer dieses Skripts::

       # ~/hof-fints/hof_adapter.py
       import datetime, importlib.util, os

       # vr_hochladen.py als Modul laden, um die Helfer zu nutzen
       _pfad = os.path.expanduser("~/hof-system/zulieferung/vr_hochladen.py")
       _spec = importlib.util.spec_from_file_location("vr_hochladen", _pfad)
       vr = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(vr)

       from hof_fints import verbindung   # ← der bestehende FinTS-Abruf (Name anpassen)

       def umsaetze_abrufen(von: datetime.date, bis: datetime.date) -> dict:
           client, konto = verbindung.vr_konto()          # PIN wie bisher aus dem Schlüsselbund
           with client:
               transaktionen = client.get_transactions(konto, von, bis)
               saldo = client.get_balance(konto)
           return {
               "umsaetze": vr.aus_mt940(transaktionen),
               "saldo": vr.saldo_aus_mt940(saldo, stand_am=datetime.date.today()),
           }

   Liefert hof-fints die Umsätze schon als Liste von Wörterbüchern, reicht es, sie in
   die Form unten umzubauen.

3. Quelle, Adresse und Token-Datei setzen (z. B. in ``~/.config/hof/zulieferung.env``
   und im launchd-Job)::

       HOF_FINTS_QUELLE=~/hof-fints/hof_adapter.py:umsaetze_abrufen
       HOF_ZULIEFERUNG_URL=https://hof-system.<tailnet>.ts.net/api/bank/zulieferung
       HOF_ZULIEFERUNG_TOKEN_DATEI=~/.config/hof/zulieferung-token

   ``HOF_FINTS_QUELLE`` ist ``<datei.py>:<funktion>`` oder ``<modul.pfad>:<funktion>``.

4. Erst prüfen, dann senden::

       python3 vr_hochladen.py --probelauf
       python3 vr_hochladen.py

5. Täglich vor 6 Uhr starten (launchd, z. B. 5:30), damit der Lauf ``bank_abruf`` um
   6 Uhr auf dem Server den Saldo beider Konten prüft.

Form der Quelle
---------------
``umsaetze_abrufen(von, bis)`` gibt ein Wörterbuch zurück (oder nur die Liste der
Umsätze, dann ohne Saldo)::

    {
      "umsaetze": [
        {"buchungsdatum": date | "JJJJ-MM-TT", "betrag": Decimal | "-12.34",  # Ausgang negativ
         "valuta": …, "gegenpartei": "…", "gegen_iban": "…", "verwendungszweck": "…",
         "bank_referenz": "…", "rohdaten": {…}}
      ],
      "saldo": {"betrag": Decimal | "1234.56", "stand_am": date}   # Saldo jetzt, am Tag stand_am
    }

Alle Umsätze im Zeitraum, ganze Tage, nur gebuchte (keine vorgemerkten).

Exit-Code: 0 bei Erfolg, 1 bei einem Fehler, 2 bei falschem Aufruf.
"""

from __future__ import annotations

import argparse
import datetime
import importlib
import importlib.util
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

STANDARD_TAGE = 14
TIMEOUT_SEKUNDEN = 60


class ZulieferungFehler(Exception):
    """Ein Fehler, dessen Meldung keine Daten aus Umsätzen enthält."""


# --- Helfer für python-fints / MT940 ------------------------------------------------


def _json_sicher(wert: Any) -> Any:
    if isinstance(wert, Mapping):
        return {str(k): _json_sicher(v) for k, v in wert.items()}
    if isinstance(wert, list | tuple):
        return [_json_sicher(v) for v in wert]
    if wert is None or isinstance(wert, bool | int | float | str):
        return wert
    return str(wert)


def aus_mt940(transaktionen: Iterable[Any]) -> list[dict[str, Any]]:
    """Wandelt MT940-Transaktionen (``python-fints``/``mt940``) in die Form der Quelle.

    Erwartet je Transaktion ``.data`` mit ``date``, ``amount`` (``.amount`` mit
    Vorzeichen), ``applicant_name``, ``applicant_iban``, ``purpose``, optional
    ``entry_date``, ``transaction_reference`` bzw. ``customer_reference``.
    """
    ergebnis = []
    for transaktion in transaktionen:
        daten = getattr(transaktion, "data", transaktion)
        betrag = daten.get("amount")
        betrag = getattr(betrag, "amount", betrag)
        zweck = daten.get("purpose") or ""
        if isinstance(zweck, list | tuple):
            zweck = " ".join(str(z) for z in zweck)
        ergebnis.append(
            {
                "buchungsdatum": daten.get("date"),
                "valuta": daten.get("entry_date") or daten.get("date"),
                "betrag": betrag,
                "gegenpartei": daten.get("applicant_name") or "",
                "gegen_iban": daten.get("applicant_iban") or "",
                "verwendungszweck": str(zweck),
                "bank_referenz": str(
                    daten.get("transaction_reference") or daten.get("customer_reference") or ""
                ),
                "rohdaten": _json_sicher(dict(daten)),
            }
        )
    return ergebnis


def saldo_aus_mt940(saldo: Any, *, stand_am: datetime.date) -> dict[str, Any] | None:
    """Saldo aus ``get_balance`` (``mt940.models.Balance``): ``.amount.amount``."""
    if saldo is None:
        return None
    betrag = getattr(saldo, "amount", saldo)
    betrag = getattr(betrag, "amount", betrag)
    return {"betrag": betrag, "stand_am": stand_am}


# --- Quelle laden -------------------------------------------------------------------


def quelle_laden(angabe: str) -> Callable[[datetime.date, datetime.date], Any]:
    """``<datei.py>:<funktion>`` oder ``<modul.pfad>:<funktion>``."""
    ziel, trenner, funktion = angabe.rpartition(":")
    if not trenner or not ziel or not funktion:
        raise ZulieferungFehler(
            "HOF_FINTS_QUELLE hat nicht die Form <datei.py>:<funktion> oder <modul>:<funktion>."
        )
    pfad = Path(os.path.expanduser(ziel))
    if ziel.endswith(".py"):
        if not pfad.is_file():
            raise ZulieferungFehler(f"Die Quelle {pfad} gibt es nicht.")
        spec = importlib.util.spec_from_file_location("hof_fints_quelle", pfad)
        if spec is None or spec.loader is None:
            raise ZulieferungFehler(f"Die Quelle {pfad} lässt sich nicht laden.")
        modul = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(pfad.parent))
        spec.loader.exec_module(modul)
    else:
        modul = importlib.import_module(ziel)
    aufruf = getattr(modul, funktion, None)
    if not callable(aufruf):
        raise ZulieferungFehler(f"In der Quelle fehlt die Funktion {funktion}.")
    return aufruf  # type: ignore[no-any-return]


# --- Lieferung bauen ----------------------------------------------------------------


def _datum(wert: Any, wo: str) -> str:
    if isinstance(wert, datetime.datetime):
        return wert.date().isoformat()
    if isinstance(wert, datetime.date):
        return wert.isoformat()
    if isinstance(wert, str):
        try:
            return datetime.date.fromisoformat(wert).isoformat()
        except ValueError:
            pass
    raise ZulieferungFehler(f"{wo}: Datum fehlt oder ist ungültig.")


def _betrag(wert: Any, wo: str) -> str:
    try:
        betrag = Decimal(str(wert))
    except InvalidOperation as fehler:
        raise ZulieferungFehler(f"{wo}: Betrag ist ungültig.") from fehler
    if not betrag.is_finite():
        raise ZulieferungFehler(f"{wo}: Betrag ist ungültig.")
    return f"{betrag.quantize(Decimal('0.01')):.2f}"


def lieferung_bauen(ergebnis: Any, von: datetime.date, bis: datetime.date) -> dict[str, Any]:
    """Prüft das Ergebnis der Quelle und baut die JSON-Lieferung."""
    if isinstance(ergebnis, Mapping):
        umsaetze_roh = ergebnis.get("umsaetze") or []
        saldo_roh = ergebnis.get("saldo")
    else:
        umsaetze_roh, saldo_roh = ergebnis, None
    if not isinstance(umsaetze_roh, list | tuple):
        raise ZulieferungFehler("Die Quelle hat keine Liste von Umsätzen geliefert.")
    umsaetze = []
    for nummer, roh in enumerate(umsaetze_roh):
        wo = f"Umsatz {nummer}"
        if not isinstance(roh, Mapping):
            raise ZulieferungFehler(f"{wo}: kein Wörterbuch.")
        umsatz: dict[str, Any] = {
            "buchungsdatum": _datum(roh.get("buchungsdatum"), f"{wo}.buchungsdatum"),
            "betrag": _betrag(roh.get("betrag"), f"{wo}.betrag"),
        }
        if roh.get("richtung"):
            umsatz["richtung"] = str(roh["richtung"])
        if roh.get("valuta"):
            umsatz["valuta"] = _datum(roh["valuta"], f"{wo}.valuta")
        for feld in ("gegenpartei", "gegen_iban", "verwendungszweck", "bank_referenz"):
            umsatz[feld] = str(roh.get(feld) or "")
        umsatz["rohdaten"] = _json_sicher(roh.get("rohdaten") or {})
        if not von.isoformat() <= umsatz["buchungsdatum"] <= bis.isoformat():
            raise ZulieferungFehler(f"{wo}: Buchungsdatum außerhalb des Zeitraums.")
        umsaetze.append(umsatz)
    lieferung: dict[str, Any] = {
        "zeitraum": {"von": von.isoformat(), "bis": bis.isoformat()},
        "umsaetze": umsaetze,
    }
    if saldo_roh:
        if not isinstance(saldo_roh, Mapping):
            raise ZulieferungFehler("Saldo: kein Wörterbuch.")
        lieferung["saldo"] = {
            "betrag": _betrag(saldo_roh.get("betrag"), "Saldo.betrag"),
            "stand_am": _datum(saldo_roh.get("stand_am") or bis, "Saldo.stand_am"),
        }
    return lieferung


def zaehlen(lieferung: Mapping[str, Any]) -> str:
    umsaetze = lieferung["umsaetze"]
    ausgaenge = sum(
        1 for u in umsaetze if u.get("richtung") == "ausgang" or Decimal(u["betrag"]) < 0
    )
    zeitraum = lieferung["zeitraum"]
    return (
        f"{len(umsaetze)} Umsätze von {zeitraum['von']} bis {zeitraum['bis']} "
        f"({len(umsaetze) - ausgaenge} Eingänge, {ausgaenge} Ausgänge), "
        f"Saldo {'dabei' if 'saldo' in lieferung else 'fehlt'}"
    )


# --- Senden -------------------------------------------------------------------------


def senden(url: str, token: str, lieferung: Mapping[str, Any]) -> dict[str, Any]:
    """Schickt die Lieferung. Fehler enthalten nur Status und Meldung des Servers."""
    if not url.startswith("https://") and not url.startswith("http://127.0.0.1"):
        raise ZulieferungFehler("Die Adresse muss mit https:// beginnen.")
    anfrage = urllib.request.Request(  # noqa: S310  (Schema oben geprüft)
        url,
        data=json.dumps(lieferung).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "hof-vr-zulieferung/1",
        },
    )
    try:
        with urllib.request.urlopen(anfrage, timeout=TIMEOUT_SEKUNDEN) as antwort:  # noqa: S310
            daten: dict[str, Any] = json.loads(antwort.read().decode("utf-8"))
            return daten
    except urllib.error.HTTPError as fehler:
        try:
            meldung = json.loads(fehler.read().decode("utf-8")).get("fehler", "")
        except (ValueError, AttributeError):
            meldung = ""
        raise ZulieferungFehler(f"Server antwortet HTTP {fehler.code}. {meldung}".strip()) from None
    except (urllib.error.URLError, TimeoutError) as fehler:
        grund = getattr(fehler, "reason", fehler)
        raise ZulieferungFehler(
            f"Server nicht erreichbar ({type(grund).__name__}). Tailscale an?"
        ) from None


def token_lesen(datei: str | None) -> str:
    if datei:
        try:
            token = Path(os.path.expanduser(datei)).read_text(encoding="utf-8").strip()
        except OSError as fehler:
            raise ZulieferungFehler(
                f"Token-Datei nicht lesbar ({type(fehler).__name__})."
            ) from None
    else:
        token = os.environ.get("HOF_ZULIEFERUNG_TOKEN", "").strip()
    if not token:
        raise ZulieferungFehler("Kein Token (HOF_ZULIEFERUNG_TOKEN_DATEI setzen).")
    return token


# --- Aufruf -------------------------------------------------------------------------


def _argumente(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Liefert die Umsätze des VR-Kontos an das Hof-System (Ausgabe: nur Zähler)."
    )
    parser.add_argument("--probelauf", action="store_true", help="nur zählen, nichts senden")
    parser.add_argument("--tage", type=int, default=STANDARD_TAGE, help="Tage bis heute (14)")
    parser.add_argument("--von", type=datetime.date.fromisoformat, help="Beginn JJJJ-MM-TT")
    parser.add_argument("--bis", type=datetime.date.fromisoformat, help="Ende JJJJ-MM-TT")
    parser.add_argument("--quelle", default=os.environ.get("HOF_FINTS_QUELLE", ""))
    parser.add_argument("--url", default=os.environ.get("HOF_ZULIEFERUNG_URL", ""))
    parser.add_argument("--token-datei", default=os.environ.get("HOF_ZULIEFERUNG_TOKEN_DATEI", ""))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    argumente = _argumente(argv)
    bis: datetime.date = argumente.bis or datetime.date.today()
    von: datetime.date = argumente.von or bis - datetime.timedelta(days=argumente.tage - 1)
    if von > bis or argumente.tage < 1:
        print("Fehler: Zeitraum ungültig.", file=sys.stderr)
        return 2
    if not argumente.quelle:
        print("Fehler: HOF_FINTS_QUELLE (oder --quelle) fehlt.", file=sys.stderr)
        return 2
    try:
        quelle = quelle_laden(argumente.quelle)
        try:
            ergebnis = quelle(von, bis)
        except Exception as fehler:
            # Nur die Art des Fehlers: Meldungen der Bank können Daten enthalten.
            raise ZulieferungFehler(f"FinTS-Abruf gescheitert ({type(fehler).__name__}).") from None
        lieferung = lieferung_bauen(ergebnis, von, bis)
        if argumente.probelauf:
            print(f"Probelauf: {zaehlen(lieferung)}. Nichts gesendet.")
            return 0
        if not argumente.url:
            raise ZulieferungFehler("HOF_ZULIEFERUNG_URL (oder --url) fehlt.")
        antwort = senden(argumente.url, token_lesen(argumente.token_datei), lieferung)
    except ZulieferungFehler as fehler:
        print(f"Fehler: {fehler}", file=sys.stderr)
        return 1
    print(
        f"Gesendet: {zaehlen(lieferung)}. Server: Abruf #{antwort.get('abruf')}, "
        f"{antwort.get('neu')} neu, {antwort.get('doppelt')} schon vorhanden, "
        f"{antwort.get('arbeitsposten')} Arbeitsposten."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
