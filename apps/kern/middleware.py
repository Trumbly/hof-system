"""Middleware des Kerns."""

from __future__ import annotations

from collections.abc import Callable

from django.conf import settings
from django.contrib.auth import logout
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import resolve_url
from django.urls import Resolver404, resolve, reverse

from apps.kern.anmeldung import (
    hat_zweiten_faktor,
    mit_zweitem_faktor_angemeldet,
    zweiten_faktor_bestanden,
)

# Seiten, die ein angemeldeter Benutzer ohne eingerichteten zweiten Faktor erreicht:
# genau die Einrichtung von TOTP (mit erneuter Passwortabfrage) und das Abmelden.
OHNE_ZWEITEN_FAKTOR_ERLAUBT = frozenset(
    {
        "mfa_activate_totp",
        "account_reauthenticate",
        "account_logout",
    }
)


class ZweiterFaktorPflichtMiddleware:
    """Ohne zweiten Faktor kommt niemand über die Anmeldung hinaus.

    - Benutzer ohne TOTP: nur die Einrichtung von TOTP und das Abmelden sind erreichbar.
    - Benutzer mit TOTP, deren Sitzung den zweiten Faktor nicht bestanden hat
      (etwa über eine andere Anmeldeseite): Sie werden abgemeldet und müssen sich
      neu anmelden.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        # Der Gesundheits-Endpunkt gilt für jeden, angemeldet oder nicht (er zeigt nur
        # „ok“ und die Version). Geprüft vor request.user, damit er keine Sitzung lädt.
        if request.path_info == reverse("kern:gesund"):
            return self.get_response(request)
        benutzer = request.user
        if not benutzer.is_authenticated or request.path_info.startswith(
            "/" + settings.STATIC_URL.lstrip("/")
        ):
            return self.get_response(request)

        if not hat_zweiten_faktor(benutzer):
            seite = self._seitenname(request)
            if seite not in OHNE_ZWEITEN_FAKTOR_ERLAUBT:
                return HttpResponseRedirect(resolve_url("mfa_activate_totp"))
            antwort = self.get_response(request)
            if seite == "mfa_activate_totp" and hat_zweiten_faktor(benutzer):
                # Gerade mit gültigem Code eingerichtet: Der zweite Faktor ist bestanden.
                zweiten_faktor_bestanden(request)
            return antwort

        if not mit_zweitem_faktor_angemeldet(request):
            logout(request)
            return HttpResponseRedirect(resolve_url(settings.LOGIN_URL))

        return self.get_response(request)

    @staticmethod
    def _seitenname(request: HttpRequest) -> str | None:
        try:
            return resolve(request.path_info).url_name
        except Resolver404:
            return None
