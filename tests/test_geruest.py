"""Tests für Schritt 0a: Gerüst, Einstellungen und Basis-Layout."""

import pytest
from django.conf import settings
from django.db import connection
from django.test import Client
from django.urls import reverse


@pytest.mark.django_db
def test_startseite_leitet_zur_anmeldung_weiter(client: Client) -> None:
    """Schritt 0a: Die Startseite antwortet ohne Anmeldung mit 302 zur Anmeldung."""
    antwort = client.get(reverse("kern:startseite"))

    assert antwort.status_code == 302
    assert antwort["Location"].startswith(reverse(settings.LOGIN_URL))


def test_startseite_zeigt_layout_mit_htmx_fuer_angemeldete(angemeldet: Client) -> None:
    """Schritt 0a: Basis-Layout mit Navigation, HTMX und Handy-Viewport."""
    antwort = angemeldet.get(reverse("kern:startseite"))

    assert antwort.status_code == 200
    html = antwort.content.decode()
    assert 'name="viewport"' in html
    assert "js/htmx.min.js" in html
    assert 'aria-label="Hauptnavigation"' in html


def test_tests_laufen_gegen_postgres() -> None:
    """Schritt 0a: Tests laufen gegen Postgres, nicht gegen SQLite."""
    assert connection.vendor == "postgresql"


def test_in_tests_geht_keine_mail_hinaus() -> None:
    """Schritt 0a: In Tests landen Mails nur im Speicher."""
    assert settings.EMAIL_BACKEND == "django.core.mail.backends.locmem.EmailBackend"
