"""Anmeldung mit django-allauth: E-Mail, Passwort und zweiter Faktor (TOTP) als Pflicht."""

from __future__ import annotations

from allauth.account.adapter import DefaultAccountAdapter
from allauth.account.internal.flows.login import (
    get_authentication_records,
    record_authentication,
)
from allauth.mfa.models import Authenticator
from allauth.mfa.utils import is_mfa_enabled
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest

ZWEITER_FAKTOR = [Authenticator.Type.TOTP]


class KontoAdapter(DefaultAccountAdapter):
    """Keine Registrierung über die Oberfläche; Benutzer entstehen per ``benutzer_anlegen``."""

    def is_open_for_signup(self, request: HttpRequest) -> bool:
        return False


def hat_zweiten_faktor(benutzer: AbstractBaseUser | AnonymousUser) -> bool:
    """Hat der Benutzer TOTP eingerichtet?"""
    return bool(benutzer.is_authenticated and is_mfa_enabled(benutzer, ZWEITER_FAKTOR))


def mit_zweitem_faktor_angemeldet(request: HttpRequest) -> bool:
    """Hat diese Sitzung den zweiten Faktor bestanden?

    allauth hält in der Sitzung fest, welche Verfahren der Benutzer bei der Anmeldung
    durchlaufen hat. Eine Anmeldung ohne diesen Eintrag (etwa über eine andere
    Anmeldeseite) zählt nicht.
    """
    benutzer = request.user
    if not isinstance(benutzer, AbstractBaseUser):
        return False
    return any(r.get("method") == "mfa" for r in get_authentication_records(request, benutzer))


def zweiten_faktor_bestanden(request: HttpRequest) -> None:
    """Hält fest, dass die Sitzung den zweiten Faktor bestanden hat.

    Nur für den Moment der Einrichtung: Wer TOTP gerade mit einem gültigen Code
    eingerichtet hat, hat ihn damit bestanden.
    """
    benutzer = request.user
    if not isinstance(benutzer, AbstractBaseUser):
        return
    totp = Authenticator.objects.filter(user_id=benutzer.pk, type=Authenticator.Type.TOTP).first()
    if totp is not None:
        record_authentication(request, benutzer, "mfa", id=totp.pk, type=totp.type)
