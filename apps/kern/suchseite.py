"""Suche in der Kopfzeile (Review 2, B1) und Kontoseite (Review 2, B15).

- ``/suche/?q=…``: per HTMX die Trefferliste unter dem Suchfeld (höchstens fünf je Gruppe),
  ohne JavaScript (Enter im Feld) die ganze Seite mit mehr Treffern je Gruppe.
- ``/konto/``: E-Mail, Status des zweiten Faktors (TOTP an/aus) und der
  Wiederherstellungscodes, mit Links zu den Seiten von allauth.
"""

from __future__ import annotations

from typing import Any

from allauth.mfa import app_settings as mfa_einstellungen
from allauth.mfa.models import Authenticator
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.kern import htmx, suche

#: Treffer je Gruppe auf der Seite „Suche“ (ohne JavaScript oder über „alle Treffer“).
JE_GRUPPE_SEITE = 25


@login_required
@require_GET
def suchen(request: HttpRequest) -> HttpResponse:
    a = suche.anfrage(request.GET.get("q", ""))
    kontext = {"q": a.text, "kurz": len(a.text) < suche.MINDESTLAENGE and not a.ist_betrag}
    if htmx.ist_htmx(request):
        return render(
            request, "kern/_suche_treffer.html", {**kontext, "gruppen": suche.suchen(a.text)}
        )
    gruppen = suche.suchen(a.text, je_gruppe=JE_GRUPPE_SEITE)
    return render(request, "kern/suche.html", {**kontext, "gruppen": gruppen, "suchtext": a.text})


def zweiter_faktor(benutzer: Any) -> dict[str, Any]:
    """Status der Anmeldung mit zweitem Faktor: TOTP an/aus, Wiederherstellungscodes."""
    arten = {a.type: a for a in Authenticator.objects.filter(user=benutzer)}
    codes = arten.get(Authenticator.Type.RECOVERY_CODES)
    unbenutzt = len(codes.wrap().get_unused_codes()) if codes is not None else 0
    return {
        "totp": Authenticator.Type.TOTP in arten,
        "totp_seit": arten[Authenticator.Type.TOTP].created_at
        if Authenticator.Type.TOTP in arten
        else None,
        "codes": codes is not None,
        "codes_unbenutzt": unbenutzt,
        "codes_gesamt": mfa_einstellungen.RECOVERY_CODE_COUNT,
    }


@login_required
@require_GET
def konto(request: HttpRequest) -> HttpResponse:
    return render(request, "kern/konto.html", {"faktor": zweiter_faktor(request.user)})
