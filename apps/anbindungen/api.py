"""Schnittstelle für die Website (W1, W10). Beschrieben in ``docs/website-anbindung.md``.

- ``GET /api/website/katalog``: buchbare Einheiten mit Typ, Fläche, Preis, frei ab und
  Bildern; keine Parteien. Mit ``ETag``: Fragt die Website mit ``If-None-Match`` und hat
  sich nichts geändert, kommt ``304`` ohne Inhalt.
- ``GET /api/website/bilder/<id>``: ein Vermarktungsbild (unveränderlich, lange cachebar).
- ``POST /api/website/ereignis``: Buchung oder Kündigung; idempotent über ``ereignis_id``.

Zugang:
- Token im Kopf ``Authorization: Bearer hofw_…`` (``manage.py website_token``). Ohne gültigen
  Token kommt 401.
- Optional eine IP-Freigabe (``HOF_WEBSITE_IP_FREIGABE``, Adressen oder Netze). Hinter
  Caddy zählt die letzte Adresse in ``X-Forwarded-For`` (die setzt Caddy selbst).
- Keine Sitzung, kein zweiter Faktor (``apps.kern.middleware``), kein CSRF: Der Token
  ersetzt die Anmeldung.

Fehlermeldungen nennen Feldnamen, nie Werte aus der Meldung.
"""

from __future__ import annotations

import datetime
import hashlib
import ipaddress
import json
import logging
import mimetypes
import re
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import FileResponse, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.anbindungen import services
from apps.anbindungen.models import Buchungsanfrage, Einheitbild
from apps.kern import zeit

logger = logging.getLogger(__name__)

#: Höchstlängen der Felder einer Meldung (wie im Modell).
LAENGEN = {"name": 200, "email": 200, "telefon": 50, "nachricht": 5000, "vertragsnummer": 20}
EREIGNIS_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,99}")


class Ungueltig(ValueError):
    """Die Meldung passt nicht. Die Meldung nennt nur das Feld."""


def _fehler(text: str, status: int) -> JsonResponse:
    antwort = JsonResponse({"fehler": text}, status=status)
    if status == 401:
        antwort["WWW-Authenticate"] = "Bearer"
    return antwort


def _token(request: HttpRequest) -> str:
    kopf = request.headers.get("Authorization", "")
    art, _, token = kopf.partition(" ")
    return token.strip() if art.lower() == "bearer" else ""


def _absender(request: HttpRequest) -> str:
    """Adresse des Absenders. Hinter Caddy die letzte in X-Forwarded-For (setzt Caddy)."""
    weitergeleitet = request.headers.get("X-Forwarded-For", "")
    if weitergeleitet:
        return weitergeleitet.split(",")[-1].strip()
    return str(request.META.get("REMOTE_ADDR", ""))


def ip_erlaubt(adresse: str, freigabe: list[str]) -> bool:
    """Leere Freigabe: jede Adresse (Zugang dann nur über Tailscale)."""
    if not freigabe:
        return True
    try:
        ip = ipaddress.ip_address(adresse)
    except ValueError:
        return False
    return any(ip in ipaddress.ip_network(netz.strip(), strict=False) for netz in freigabe)


def _zugang_pruefen(request: HttpRequest) -> JsonResponse | None:
    if not ip_erlaubt(_absender(request), settings.HOF_WEBSITE_IP_FREIGABE):
        logger.warning("Website-Schnittstelle: Anfrage von nicht freigegebener Adresse")
        return _fehler("Zugriff von dieser Adresse nicht freigegeben.", 403)
    if services.zugang_zum_token(_token(request)) is None:
        return _fehler("Token fehlt oder ist ungültig.", 401)
    return None


def _basis(request: HttpRequest) -> str:
    return settings.HOF_BASIS_URL.rstrip("/") or request.build_absolute_uri("/").rstrip("/")


def katalog_daten(request: HttpRequest, stichtag: datetime.date) -> dict[str, Any]:
    basis = _basis(request)
    eintraege = services.katalog(stichtag)
    return {
        "stand": stichtag.isoformat(),
        "anzahl": len(eintraege),
        "einheiten": [
            {
                "nw_id": e.nw_id,
                "typ": e.typ,
                "typ_titel": e.typ_titel,
                "objekt": e.objekt,
                "bezeichnung": e.bezeichnung,
                "flaeche_qm": str(e.flaeche_qm) if e.flaeche_qm is not None else None,
                "preis_monat": str(e.preis_monat) if e.preis_monat is not None else None,
                "waehrung": "EUR",
                "frei_ab": e.frei_ab.isoformat(),
                "slug": e.slug,
                "bilder": [
                    {
                        "url": basis + reverse("anbindungen:api_bild", args=[b.bild_id]),
                        "beschreibung": b.beschreibung,
                        "reihenfolge": b.reihenfolge,
                    }
                    for b in e.bilder
                ],
            }
            for e in eintraege
        ],
    }


def _etag(inhalt: bytes) -> str:
    return '"' + hashlib.sha256(inhalt).hexdigest()[:32] + '"'


def _passt(request: HttpRequest, etag: str) -> bool:
    gefragt = request.headers.get("If-None-Match", "")
    return any(t.strip().removeprefix("W/") == etag for t in gefragt.split(",")) or (
        gefragt.strip() == "*"
    )


@require_GET
def katalog(request: HttpRequest) -> HttpResponse:
    fehler = _zugang_pruefen(request)
    if fehler is not None:
        return fehler
    daten = katalog_daten(request, zeit.heute())
    inhalt = json.dumps(daten, ensure_ascii=False, sort_keys=True).encode("utf-8")
    etag = _etag(inhalt)
    if _passt(request, etag):
        antwort = HttpResponse(status=304)
    else:
        antwort = HttpResponse(inhalt, content_type="application/json; charset=utf-8")
    antwort["ETag"] = etag
    # Die Website darf zwischenspeichern, fragt aber jedes Mal mit If-None-Match nach.
    antwort["Cache-Control"] = "private, no-cache"
    return antwort


@require_GET
def bild(request: HttpRequest, pk: int) -> HttpResponse:
    fehler = _zugang_pruefen(request)
    if fehler is not None:
        return fehler
    eintrag = get_object_or_404(Einheitbild, pk=pk, aktiv=True)
    art = mimetypes.guess_type(eintrag.bild.name)[0] or "application/octet-stream"
    antwort = FileResponse(eintrag.bild.open("rb"), content_type=art)
    # Ein Bild ändert sich nie: Ein neues Bild bekommt eine neue ID.
    antwort["Cache-Control"] = "private, max-age=31536000, immutable"
    antwort["ETag"] = f'"bild-{eintrag.pk}"'
    return antwort


# --- Ereignisse ----------------------------------------------------------------------


def _text(daten: dict[str, Any], feld: str, wo: str) -> str:
    wert = daten.get(feld, "")
    if wert is None:
        return ""
    if not isinstance(wert, str):
        raise Ungueltig(f"{wo}: kein Text")
    if len(wert) > LAENGEN.get(feld, 200):
        raise Ungueltig(f"{wo}: zu lang (höchstens {LAENGEN.get(feld, 200)} Zeichen)")
    return wert


def ereignis_lesen(daten: Any) -> services.WebsiteEreignis:
    if not isinstance(daten, dict):
        raise Ungueltig("Meldung: kein Objekt")
    ereignis_id = daten.get("ereignis_id")
    if not isinstance(ereignis_id, str) or not EREIGNIS_ID.fullmatch(ereignis_id):
        raise Ungueltig("ereignis_id: fehlt oder ungültig (Buchstaben, Ziffern, . _ : -)")
    art = daten.get("art")
    if art not in Buchungsanfrage.Art.values:
        raise Ungueltig("art: fehlt oder ungültig (buchung oder kuendigung)")
    nw_id = daten.get("nw_id")
    if not isinstance(nw_id, str) or not nw_id.strip() or len(nw_id) > 30:
        raise Ungueltig("nw_id: fehlt oder ungültig")
    zeitpunkt = daten.get("zeitpunkt")
    try:
        gemeldet_am = datetime.datetime.fromisoformat(str(zeitpunkt))
    except ValueError as fehler:
        raise Ungueltig("zeitpunkt: fehlt oder ungültig (ISO 8601)") from fehler
    if timezone.is_naive(gemeldet_am):
        gemeldet_am = timezone.make_aware(gemeldet_am)
    wunsch_ab = None
    if daten.get("wunsch_ab"):
        try:
            wunsch_ab = datetime.date.fromisoformat(str(daten["wunsch_ab"]))
        except ValueError as fehler:
            raise Ungueltig("wunsch_ab: ungültig (JJJJ-MM-TT)") from fehler
    kontakt = daten.get("kontakt") or {}
    if not isinstance(kontakt, dict):
        raise Ungueltig("kontakt: kein Objekt")
    email = _text(kontakt, "email", "kontakt.email").strip()
    if email:
        try:
            validate_email(email)
        except ValidationError as fehler:
            raise Ungueltig("kontakt.email: ungültig") from fehler
    return services.WebsiteEreignis(
        ereignis_id=ereignis_id,
        art=art,
        nw_id=nw_id.strip(),
        gemeldet_am=gemeldet_am,
        wunsch_ab=wunsch_ab,
        vertragsnummer=_text(daten, "vertragsnummer", "vertragsnummer"),
        kontakt_name=_text(kontakt, "name", "kontakt.name"),
        kontakt_email=email,
        kontakt_telefon=_text(kontakt, "telefon", "kontakt.telefon"),
        nachricht=_text(daten, "nachricht", "nachricht"),
    )


@csrf_exempt
@never_cache
@require_POST
def ereignis(request: HttpRequest) -> JsonResponse:
    fehler = _zugang_pruefen(request)
    if fehler is not None:
        return fehler
    try:
        daten = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        return _fehler("Die Meldung ist kein gültiges JSON.", 400)
    try:
        meldung = ereignis_lesen(daten)
    except Ungueltig as ungueltig:
        return _fehler(f"Meldung ungültig: {ungueltig}", 400)
    try:
        anfrage, neu = services.ereignis_annehmen(meldung)
    except services.EreignisKonflikt:
        return _fehler("ereignis_id: schon mit anderem Inhalt empfangen.", 409)
    except services.EreignisUngueltig as ungueltig:
        return _fehler(f"Meldung nicht verarbeitbar: {ungueltig}", 422)
    return JsonResponse(
        {
            "ereignis_id": anfrage.ereignis_id,
            "anfrage": anfrage.pk,
            "neu": neu,
            "status": anfrage.status,
        },
        status=201 if neu else 200,
    )
