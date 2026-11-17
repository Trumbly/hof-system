"""Gemeinsame Fixtures: Benutzer mit TOTP und eine echte Anmeldung mit zweitem Faktor."""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest
from allauth.mfa.totp.internal.auth import TOTP, format_hotp_value, generate_totp_secret, hotp_value
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.kern.models import Benutzer
from tests.factories import TESTPASSWORT, BenutzerFabrik


def totp_code(geheimnis: str, versatz: int = 0) -> str:
    """Der aktuelle TOTP-Code, wie ihn die Authenticator-App zeigt."""
    zaehler = int(time.time()) // 30 + versatz
    return format_hotp_value(hotp_value(geheimnis, zaehler))


@pytest.fixture(autouse=True)
def _cache_leeren() -> Iterator[None]:
    """allauth merkt sich benutzte Codes und Fehlversuche im Cache."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def benutzer(db: None) -> Benutzer:
    """Ein Benutzer ohne zweiten Faktor."""
    return BenutzerFabrik()  # type: ignore[no-any-return]


@pytest.fixture
def totp_geheimnis() -> str:
    return generate_totp_secret()


@pytest.fixture
def benutzer_mit_totp(benutzer: Benutzer, totp_geheimnis: str) -> Benutzer:
    TOTP.activate(benutzer, totp_geheimnis)
    return benutzer


def anmelden(client: Client, benutzer: Benutzer, geheimnis: str) -> None:
    """Meldet über die echten Seiten an: E-Mail und Passwort, dann TOTP-Code."""
    antwort = client.post(
        reverse("account_login"), {"login": benutzer.email, "password": TESTPASSWORT}
    )
    assert antwort.status_code == 302, antwort.content.decode()[:500]
    assert antwort["Location"] == reverse("mfa_authenticate")
    antwort = client.post(reverse("mfa_authenticate"), {"code": totp_code(geheimnis)})
    assert antwort.status_code == 302, antwort.content.decode()[:500]


@pytest.fixture
def angemeldet(client: Client, benutzer_mit_totp: Benutzer, totp_geheimnis: str) -> Client:
    """Ein Client, der mit Passwort und zweitem Faktor angemeldet ist."""
    anmelden(client, benutzer_mit_totp, totp_geheimnis)
    return client
