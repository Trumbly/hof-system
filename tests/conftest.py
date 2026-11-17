"""Gemeinsame Fixtures: Benutzer mit TOTP, eine echte Anmeldung mit zweitem Faktor und
der Testhof."""

from __future__ import annotations

import sys
import time
from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from allauth.mfa.totp.internal import auth as totp_auth
from allauth.mfa.totp.internal.auth import TOTP, format_hotp_value, generate_totp_secret, hotp_value
from django.core.cache import cache
from django.db import transaction
from django.test import Client
from django.urls import reverse
from pytest_django import DjangoDbBlocker

from apps.kern.models import Benutzer
from testhof.aufbau import Testhof, aufbauen
from tests.factories import TESTPASSWORT, BenutzerFabrik

#: Seed des Testhofs für alle Tests.
TESTHOF_SEED = 42


def totp_code(geheimnis: str, versatz: int = 0) -> str:
    """Der aktuelle TOTP-Code, wie ihn die Authenticator-App zeigt.

    Die Uhr ist dieselbe, mit der allauth prüft (in Tests angehalten, siehe
    ``_totp_uhr_anhalten``).
    """
    zaehler = int(totp_auth.time.time()) // 30 + versatz
    return format_hotp_value(hotp_value(geheimnis, zaehler))


@pytest.fixture(autouse=True)
def _totp_uhr_anhalten(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hält die Uhr der TOTP-Prüfung von allauth für die Dauer eines Tests an.

    Ohne das scheitert eine Anmeldung zufällig, wenn zwischen Erzeugen und Prüfen des Codes
    die 30-Sekunden-Grenze liegt (allauth erlaubt keine Toleranz). Nur das Modul von allauth
    sieht die angehaltene Uhr; ``time.time`` bleibt für alles andere echt.
    """
    jetzt = float(int(time.time()))
    monkeypatch.setattr(totp_auth, "time", SimpleNamespace(time=lambda: jetzt))


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


# --- Testhof -------------------------------------------------------------------------


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Tests mit dem Testhof laufen zuletzt.

    Der Testhof steht ab dem ersten Test, der ihn braucht, bis zum Ende des Testlaufs in
    der Datenbank (eine offene Transaktion um alle folgenden Tests). Tests ohne Testhof
    laufen deshalb vorher und sehen eine leere Datenbank wie bisher.
    """
    items.sort(key=lambda item: "testhof" in getattr(item, "fixturenames", ()))


@pytest.fixture(scope="session")
def _testhof_sitzung(
    django_db_setup: None, django_db_blocker: DjangoDbBlocker
) -> Iterator[Testhof]:
    """Baut den Testhof einmal pro Testlauf in einer Transaktion auf, am Ende zurückgerollt.

    Jeder Test läuft darin in einem eigenen Sicherungspunkt (pytest-django), seine
    Änderungen verschwinden nach dem Test; der Testhof bleibt.
    """
    with django_db_blocker.unblock():
        atomar = transaction.atomic()
        atomar.__enter__()
        try:
            hof = aufbauen(seed=TESTHOF_SEED)
        except BaseException:
            atomar.__exit__(*sys.exc_info())
            raise
    try:
        yield hof
    finally:
        with django_db_blocker.unblock():
            transaction.set_rollback(True)
            atomar.__exit__(None, None, None)


@pytest.fixture
def testhof(_testhof_sitzung: Testhof, db: None) -> Testhof:
    """Der Testhof aus Seed 42 (``testhof.kern``: Mandanten, Objekte, Testbenutzer).

    Nicht mit ``transaction=True`` kombinieren: Solche Tests leeren die Datenbank.
    Wer Objekte des Testhofs im Test ändert, liest sie danach mit ``refresh_from_db()``.
    """
    return _testhof_sitzung
