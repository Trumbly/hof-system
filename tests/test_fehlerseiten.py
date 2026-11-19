"""Review 1, Punkt 12: prod startet mit DEBUG=False und zeigt eigene deutsche Fehlerseiten."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.db import connection
from django.test import Client, RequestFactory
from django.urls import reverse
from django.views.defaults import server_error

WURZEL = Path(__file__).resolve().parents[1]

#: Texte der eingebauten englischen Fehlerseiten von Django.
ENGLISCH = (
    "Not Found",
    "The requested resource was not found",
    "Server Error",
    "Forbidden",
    "CSRF verification failed",
    "Bad Request",
    "No Bankkonto matches",
)

#: Startet Django mit den Einstellungen für den Server und fragt eine Adresse ab, die es
#: nicht gibt. Gibt DEBUG, Status und Inhalt als JSON aus.
PROD_SKRIPT = """
import json, django
django.setup()
from django.conf import settings
from django.test import Client
antwort = Client().get("/gibt-es-nicht/", secure=True, HTTP_HOST="hof.testhof.invalid")
print(json.dumps({
    "debug": settings.DEBUG,
    "status": antwort.status_code,
    "inhalt": antwort.content.decode(),
}))
"""


def _db_url() -> str:
    """Die Datenbank des Testlaufs; die Seite 404 liest nichts, verbindet sich höchstens."""
    d = connection.settings_dict
    wirt = f"{d['HOST'] or 'localhost'}:{d['PORT'] or 5432}"
    return f"postgres://{d['USER']}:{d['PASSWORD']}@{wirt}/{d['NAME']}"


def _ohne_englisch(inhalt: str) -> None:
    for text in ENGLISCH:
        assert text not in inhalt, text


@pytest.mark.django_db
def test_prod_startet_mit_debug_false_und_eigener_404() -> None:
    """Review 1, Punkt 12: Mit hof.settings.prod ist DEBUG aus, auch wenn DJANGO_DEBUG=True
    in der Umgebung steht, und eine unbekannte Adresse zeigt die eigene deutsche Seite 404."""
    umgebung = {
        k: v for k, v in os.environ.items() if not k.startswith(("DJANGO_", "HOF_", "PYTEST"))
    }
    umgebung.update(
        DJANGO_SETTINGS_MODULE="hof.settings.prod",
        DJANGO_SECRET_KEY="nur-fuer-den-test-der-prod-einstellungen",
        DJANGO_ALLOWED_HOSTS="hof.testhof.invalid",
        DJANGO_DEBUG="True",
        DATABASE_URL=_db_url(),
        EMAIL_URL="smtp://localhost:1025",
    )
    ergebnis = subprocess.run(  # noqa: S603  (fester Aufruf, keine Eingaben von außen)
        [sys.executable, "-W", "ignore", "-c", PROD_SKRIPT],
        cwd=WURZEL,
        env=umgebung,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert ergebnis.returncode == 0, ergebnis.stderr[-2000:]
    daten = json.loads(ergebnis.stdout.strip().splitlines()[-1])

    assert daten["debug"] is False
    assert daten["status"] == 404
    assert "Diese Seite gibt es nicht." in daten["inhalt"]
    assert '<html lang="de">' in daten["inhalt"]
    assert "/gibt-es-nicht/" in daten["inhalt"]
    _ohne_englisch(daten["inhalt"])


@pytest.mark.django_db
def test_eigene_404_auch_fuer_fehlende_eintraege(angemeldet: Client) -> None:
    """Review 1, Punkt 12: Auch ein fehlender Eintrag (get_object_or_404) zeigt die deutsche
    Seite, ohne die englische Meldung der Ausnahme."""
    antwort = angemeldet.get(reverse("bank:freigabe", args=[999999]))

    assert antwort.status_code == 404
    inhalt = antwort.content.decode()
    assert "Diese Seite gibt es nicht." in inhalt
    _ohne_englisch(inhalt)


@pytest.mark.django_db
def test_unbekannte_adresse_ohne_anmeldung(client: Client) -> None:
    """Review 1, Punkt 12: Die Seite 404 erscheint auch ohne Anmeldung, auf Deutsch."""
    antwort = client.get("/gibt-es-nicht/")

    assert antwort.status_code == 404
    _ohne_englisch(antwort.content.decode())


def test_eigene_500_ohne_kontext() -> None:
    """Review 1, Punkt 12: Die Seite 500 ist deutsch und braucht keinen Kontext."""
    antwort = server_error(RequestFactory().get("/"))

    assert antwort.status_code == 500
    inhalt = antwort.content.decode()
    assert "Hier ist etwas schiefgegangen." in inhalt
    _ohne_englisch(inhalt)


@pytest.mark.django_db
def test_abgelaufenes_formular_auf_deutsch() -> None:
    """Review 1, Punkt 12: Auch die Seite bei fehlendem CSRF-Schlüssel ist deutsch."""
    antwort = Client(enforce_csrf_checks=True).post(reverse("account_login"), {})

    assert antwort.status_code == 403
    inhalt = antwort.content.decode()
    assert "Das Formular ist abgelaufen." in inhalt
    _ohne_englisch(inhalt)


def test_fehlerseiten_ohne_static_und_navigation() -> None:
    """Review 1, Punkt 12: Die Fehlerseiten hängen nicht an statischen Dateien (Manifest)
    oder an der Navigation, damit sie in jeder Lage erscheinen."""
    for name in ("400", "403", "403_csrf", "404", "500"):
        text = (WURZEL / "templates" / f"{name}.html").read_text()
        assert text.startswith('{% extends "_fehlerseite.html" %}'), name
    grundgeruest = (WURZEL / "templates" / "_fehlerseite.html").read_text()
    assert "{% static" not in grundgeruest and "{% url" not in grundgeruest
