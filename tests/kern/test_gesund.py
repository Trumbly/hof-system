"""Gesundheits-Endpunkt /gesund (Schritt 0d, Aufgabe 5)."""

from __future__ import annotations

import importlib
import re
import sys
from unittest import mock

import pytest
from django.db import DatabaseError
from django.test import Client, override_settings
from django.urls import reverse

from apps.kern.models import Benutzer

pytestmark = pytest.mark.django_db


def test_adresse_ist_gesund_ohne_schraegstrich() -> None:
    """Schritt 0d, Aufgabe 5: Der Endpunkt heißt genau /gesund."""
    assert reverse("kern:gesund") == "/gesund"


@override_settings(HOF_VERSION="v0.0.0-test")
def test_gesund_ohne_anmeldung_nur_ok_und_version(client: Client) -> None:
    """Abnahme Schritt 0d, Aufgabe 5: /gesund antwortet ohne Anmeldung, ohne Daten, nur mit
    „ok“ und der Version."""
    antwort = client.get("/gesund")

    assert antwort.status_code == 200
    assert antwort.content.decode() == "ok v0.0.0-test\n"
    assert antwort["Content-Type"].startswith("text/plain")
    assert "no-cache" in antwort["Cache-Control"]
    assert "sessionid" not in antwort.cookies


@override_settings(HOF_VERSION="v0.0.0-test")
def test_gesund_auch_fuer_benutzer_ohne_zweiten_faktor(client: Client, benutzer: Benutzer) -> None:
    """Schritt 0d, Aufgabe 5: Die Pflicht zum zweiten Faktor (Middleware aus 0b) lässt /gesund
    durch, auch wenn ein Benutzer ohne TOTP angemeldet ist; andere Seiten bleiben gesperrt."""
    client.force_login(benutzer)

    antwort = client.get("/gesund")
    assert antwort.status_code == 200
    assert antwort.content.decode() == "ok v0.0.0-test\n"

    gesperrt = client.get(reverse("kern:startseite"))
    assert gesperrt.status_code == 302
    assert gesperrt["Location"] == reverse("mfa_activate_totp")


def test_gesund_meldet_benutzer_mit_totp_ohne_bestandenen_faktor_nicht_ab(
    client: Client, benutzer_mit_totp: Benutzer
) -> None:
    """Schritt 0d, Aufgabe 5: /gesund greift nicht in Sitzungen ein; die Middleware meldet
    erst auf einer anderen Seite ab."""
    client.force_login(benutzer_mit_totp)

    assert client.get("/gesund").status_code == 200
    assert "_auth_user_id" in client.session

    client.get(reverse("kern:startseite"))
    assert "_auth_user_id" not in client.session


def test_gesund_version_aus_der_umgebung(monkeypatch: pytest.MonkeyPatch) -> None:
    """Schritt 0d, Aufgabe 5: Die Version kommt aus der Umgebungsvariable HOF_VERSION, die
    deploy.sh setzt; ohne sie steht dort „unbekannt“."""
    monkeypatch.setenv("HOF_VERSION", "v1.2.3")
    monkeypatch.delitem(sys.modules, "hof.settings.base", raising=False)
    basis = importlib.import_module("hof.settings.base")
    assert basis.HOF_VERSION == "v1.2.3"

    monkeypatch.delenv("HOF_VERSION")
    monkeypatch.delitem(sys.modules, "hof.settings.base")
    basis = importlib.import_module("hof.settings.base")
    assert basis.HOF_VERSION == "unbekannt"


@override_settings(HOF_VERSION="v0.0.0-test")
def test_gesund_meldet_fehler_wenn_die_datenbank_nicht_antwortet(client: Client) -> None:
    """Schritt 0d, Aufgabe 5: Ohne Datenbank antwortet /gesund mit 503 und „fehler“, damit
    deploy.sh und der Healthcheck eine kaputte Version erkennen."""
    with mock.patch(
        "apps.kern.views.connection.cursor", side_effect=DatabaseError("keine Verbindung")
    ):
        antwort = client.get("/gesund")

    assert antwort.status_code == 503
    assert antwort.content.decode() == "fehler v0.0.0-test\n"


def test_gesund_nur_lesend(client: Client) -> None:
    """Schritt 0d, Aufgabe 5: /gesund nimmt nur GET an."""
    assert client.post("/gesund").status_code == 405


def test_prod_einstellungen_fuer_den_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """Schritt 0d, Aufgabe 1 und 5: In prod liefert WhiteNoise die statischen Dateien aus dem
    Image, der Healthcheck im Container erreicht /gesund über HTTP ohne Umleitung."""
    monkeypatch.setenv("DJANGO_SECRET_KEY", "nur-fuer-diesen-test")
    monkeypatch.setenv("DATABASE_URL", "postgres://hof:hof@localhost:5432/hof")
    monkeypatch.setenv("EMAIL_URL", "smtp://localhost:1025")
    monkeypatch.setenv("DJANGO_ALLOWED_HOSTS", "hof-system.beispiel.ts.net")
    monkeypatch.setenv("DJANGO_ENV", "prod")
    monkeypatch.delitem(sys.modules, "hof.settings.base", raising=False)
    monkeypatch.delitem(sys.modules, "hof.settings.prod", raising=False)

    prod = importlib.import_module("hof.settings.prod")

    assert prod.MIDDLEWARE[0] == "django.middleware.security.SecurityMiddleware"
    assert prod.MIDDLEWARE[1] == "whitenoise.middleware.WhiteNoiseMiddleware"
    assert "apps.kern.middleware.ZweiterFaktorPflichtMiddleware" in prod.MIDDLEWARE
    assert prod.STORAGES["staticfiles"]["BACKEND"].startswith("whitenoise.")
    assert prod.ALLOWED_HOSTS == ["hof-system.beispiel.ts.net", "127.0.0.1", "localhost"]
    assert prod.SECURE_SSL_REDIRECT is True
    assert any(re.search(muster, "gesund") for muster in prod.SECURE_REDIRECT_EXEMPT)
    assert not any(re.search(muster, "") for muster in prod.SECURE_REDIRECT_EXEMPT)
    assert not any(re.search(muster, "konto/login/") for muster in prod.SECURE_REDIRECT_EXEMPT)
