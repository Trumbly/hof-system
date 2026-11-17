"""Endpunkt für die Zulieferung des VR-Kontos vom hof-mac: ``POST /api/bank/zulieferung``.

- Erreichbar nur mit eigenem Token (``Authorization: Bearer hofz_…``), der für genau ein
  Konto gilt. Ohne gültigen Token kommt 401, ohne Hinweis, ob es das Konto gibt.
- Keine Sitzung, kein zweiter Faktor (siehe ``apps.kern.middleware``), kein CSRF: Der
  Token ersetzt die Anmeldung. Erreichbar ist der Server nur über Tailscale.
- Idempotent: Der Fingerabdruck lässt jeden Umsatz nur einmal ins System. Dieselbe
  Lieferung zweimal ergibt beim zweiten Mal nur „doppelt“.
- Fehlermeldungen nennen Position und Feldname, nie Werte aus der Lieferung.

Aufbau der Lieferung (JSON)::

    {
      "zeitraum": {"von": "2026-11-03", "bis": "2026-11-17"},
      "umsaetze": [
        {"buchungsdatum": "2026-11-16", "valuta": "2026-11-16", "betrag": "-12.34",
         "gegenpartei": "…", "gegen_iban": "DE…", "verwendungszweck": "…",
         "bank_referenz": "…", "rohdaten": {…}}
      ],
      "saldo": {"betrag": "1234.56", "stand_am": "2026-11-17"}
    }

``betrag`` mit Vorzeichen (Ausgang negativ) oder positiv mit ``"richtung": "eingang"``
bzw. ``"ausgang"``. ``saldo`` ist der Kontostand zum Zeitpunkt des Abrufs am Tag
``stand_am``; die Lieferung muss dann die Umsätze bis ``stand_am`` enthalten.
"""

from __future__ import annotations

import datetime
import json
import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from django.http import HttpRequest, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.bank import services
from apps.bank.models import Bankabruf, Bankumsatz

logger = logging.getLogger(__name__)

MAX_UMSAETZE = 5000
TEXTFELDER = ("gegenpartei", "gegen_iban", "verwendungszweck", "bank_referenz", "waehrung")


class Ungueltig(ValueError):
    """Die Lieferung passt nicht. Die Meldung nennt nur Position und Feld."""


def _datum(wert: Any, wo: str) -> datetime.date:
    if not isinstance(wert, str):
        raise Ungueltig(f"{wo}: Datum fehlt oder ist kein Text")
    try:
        return datetime.date.fromisoformat(wert)
    except ValueError as fehler:
        raise Ungueltig(f"{wo}: Datum ungültig (JJJJ-MM-TT)") from fehler


def _betrag(wert: Any, wo: str) -> Decimal:
    if isinstance(wert, bool) or not isinstance(wert, str | int | float):
        raise Ungueltig(f"{wo}: Betrag fehlt")
    try:
        betrag = Decimal(str(wert))
    except InvalidOperation as fehler:
        raise Ungueltig(f"{wo}: Betrag ungültig") from fehler
    if not betrag.is_finite() or betrag != betrag.quantize(services.CENT):
        raise Ungueltig(f"{wo}: Betrag ungültig (höchstens zwei Nachkommastellen)")
    return betrag


def umsatz_lesen(eintrag: Any, nummer: int) -> services.UmsatzDaten:
    wo = f"umsaetze[{nummer}]"
    if not isinstance(eintrag, dict):
        raise Ungueltig(f"{wo}: kein Objekt")
    betrag = _betrag(eintrag.get("betrag"), f"{wo}.betrag")
    richtung = eintrag.get("richtung")
    if richtung is None:
        richtung = Bankumsatz.Richtung.AUSGANG if betrag < 0 else Bankumsatz.Richtung.EINGANG
    elif richtung not in Bankumsatz.Richtung.values or betrag < 0:
        raise Ungueltig(f"{wo}.richtung: ungültig (eingang/ausgang mit positivem Betrag)")
    texte = {}
    for feld in TEXTFELDER:
        wert = eintrag.get(feld, "")
        if wert is None:
            wert = ""
        if not isinstance(wert, str):
            raise Ungueltig(f"{wo}.{feld}: kein Text")
        texte[feld] = wert
    rohdaten = eintrag.get("rohdaten") or {}
    if not isinstance(rohdaten, dict):
        raise Ungueltig(f"{wo}.rohdaten: kein Objekt")
    valuta = eintrag.get("valuta")
    return services.UmsatzDaten(
        buchungsdatum=_datum(eintrag.get("buchungsdatum"), f"{wo}.buchungsdatum"),
        betrag=abs(betrag),
        richtung=richtung,
        valuta=_datum(valuta, f"{wo}.valuta") if valuta else None,
        gegenpartei=texte["gegenpartei"],
        gegen_iban=texte["gegen_iban"],
        verwendungszweck=texte["verwendungszweck"],
        bank_referenz=texte["bank_referenz"],
        waehrung=texte["waehrung"] or "EUR",
        rohdaten=rohdaten,
    )


def _token(request: HttpRequest) -> str:
    kopf = request.headers.get("Authorization", "")
    art, _, token = kopf.partition(" ")
    return token.strip() if art.lower() == "bearer" else ""


def _fehler(text: str, status: int) -> JsonResponse:
    antwort = JsonResponse({"fehler": text}, status=status)
    if status == 401:
        antwort["WWW-Authenticate"] = "Bearer"
    return antwort


@csrf_exempt
@never_cache
@require_POST
def zulieferung(request: HttpRequest) -> JsonResponse:
    konto = services.konto_zum_token(_token(request))
    if konto is None:
        return _fehler("Token fehlt oder ist ungültig.", 401)
    try:
        daten = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        return _fehler("Die Lieferung ist kein gültiges JSON.", 400)
    try:
        if not isinstance(daten, dict) or not isinstance(daten.get("umsaetze"), list):
            raise Ungueltig("umsaetze: Liste fehlt")
        if len(daten["umsaetze"]) > MAX_UMSAETZE:
            raise Ungueltig(f"umsaetze: höchstens {MAX_UMSAETZE} je Lieferung")
        umsaetze = [umsatz_lesen(e, n) for n, e in enumerate(daten["umsaetze"])]
        zeitraum = daten.get("zeitraum") or {}
        if not isinstance(zeitraum, dict):
            raise Ungueltig("zeitraum: kein Objekt")
        von = _datum(zeitraum["von"], "zeitraum.von") if zeitraum.get("von") else None
        bis = _datum(zeitraum["bis"], "zeitraum.bis") if zeitraum.get("bis") else None
        if von and bis and von > bis:
            raise Ungueltig("zeitraum: von liegt nach bis")
        if von and any(u.buchungsdatum < von for u in umsaetze):
            raise Ungueltig("umsaetze: Buchungsdatum vor zeitraum.von")
        if bis and any(u.buchungsdatum > bis for u in umsaetze):
            raise Ungueltig("umsaetze: Buchungsdatum nach zeitraum.bis")
        saldo = daten.get("saldo")
        saldo_betrag: Decimal | None = None
        stand_am: datetime.date | None = None
        if saldo is not None:
            if not isinstance(saldo, dict):
                raise Ungueltig("saldo: kein Objekt")
            saldo_betrag = _betrag(saldo.get("betrag"), "saldo.betrag")
            stand_am = _datum(saldo.get("stand_am"), "saldo.stand_am")
            if bis is None or bis < stand_am:
                raise Ungueltig("saldo: zeitraum.bis muss den Tag stand_am einschließen")
    except Ungueltig as fehler:
        return _fehler(f"Lieferung ungültig: {fehler}", 400)

    abruf = services.bankabruf_starten(konto, Bankabruf.Quelle.ZULIEFERUNG, von=von, bis=bis)
    ergebnis = services.umsaetze_uebernehmen(konto, umsaetze, abruf=abruf)
    kontostand = None
    if saldo_betrag is not None and stand_am is not None:
        kontostand = services.kontostand_aus_saldo(
            konto, saldo_betrag, stand_am=stand_am, abruf=abruf
        )
    posten = services.saldenabweichungen_melden(konto)
    services.bankabruf_beenden(
        abruf,
        meldung=(
            f"Zulieferung: {ergebnis.geliefert} geliefert, {ergebnis.neu} neu, "
            f"{ergebnis.doppelt} schon vorhanden"
        ),
    )
    logger.info(
        "Zulieferung für Konto #%s: Abruf #%s, %s neu, %s doppelt",
        konto.pk,
        abruf.pk,
        ergebnis.neu,
        ergebnis.doppelt,
    )
    return JsonResponse(
        {
            "konto": konto.pk,
            "abruf": abruf.pk,
            "geliefert": ergebnis.geliefert,
            "neu": ergebnis.neu,
            "doppelt": ergebnis.doppelt,
            "kontostand": kontostand.datum.isoformat() if kontostand else None,
            "arbeitsposten": len(posten),
        }
    )
