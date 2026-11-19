"""Gestaltung, Schritt 1 (Review 2, Teil C; PR R2-4): Zahlenformat, Stichtag-Band,
Stilseite, Bausteine und Navigation."""

from __future__ import annotations

import datetime
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from django.template import Context, Template
from django.test import Client
from django.urls import reverse

from apps.dokumente import platzhalter
from apps.kern import zahlen
from apps.kern.templatetags.bausteine import BADGES

WURZEL = Path(__file__).resolve().parents[2]
D = datetime.date


# --- Zahlen und Beträge (Review 1, Punkt 11) -----------------------------------------


@pytest.mark.parametrize(
    ("wert", "erwartet"),
    [
        (Decimal("209533.70"), "209.533,70\u00a0€"),
        (Decimal("209533.7"), "209.533,70\u00a0€"),
        (Decimal("0"), "0,00\u00a0€"),
        (Decimal("-14.6"), "-14,60\u00a0€"),
        (Decimal("-0.001"), "0,00\u00a0€"),
        (1234567, "1.234.567,00\u00a0€"),
        (12.345, "12,35\u00a0€"),
        ("85.00", "85,00\u00a0€"),
        (Decimal("999.995"), "1.000,00\u00a0€"),
        (None, ""),
        ("", ""),
        ("‹betrag.offen›", "‹betrag.offen›"),
    ],
)
def test_euro(wert: object, erwartet: str) -> None:
    """Review 1, Punkt 11: Beträge mit Tausenderpunkt und Komma (209.533,70 €)."""
    assert zahlen.euro(wert) == erwartet


@pytest.mark.parametrize(
    ("wert", "stellen", "erwartet"),
    [
        (Decimal("9602.7"), None, "9.602,7"),
        (Decimal("12.50"), None, "12,50"),
        (Decimal("1000"), None, "1.000"),
        (1234567, 2, "1.234.567,00"),
        (Decimal("-1234.5"), 1, "-1.234,5"),
        (Decimal("18"), 1, "18,0"),
        (0, None, "0"),
        (None, None, ""),
        ("abc", None, "abc"),
    ],
)
def test_zahl(wert: object, stellen: int | None, erwartet: str) -> None:
    """Review 1, Punkt 11: Zahlen mit Tausenderpunkt und Komma (9.602,7)."""
    assert zahlen.zahl(wert, stellen) == erwartet


def test_filter_in_jeder_vorlage_ohne_load() -> None:
    """Review 1, Punkt 11: |euro und |zahl stehen in jeder Vorlage bereit (builtins)."""
    html = Template("{{ a|euro }} · {{ b|zahl }} · {{ c|zahl:2 }}").render(
        Context({"a": Decimal("209533.7"), "b": Decimal("9602.7"), "c": 3})
    )
    assert html == "209.533,70\u00a0€ · 9.602,7 · 3,00"


def test_eine_stelle_fuer_das_format() -> None:
    """Review 1, Punkt 11: Die Schreiben an Mieter nutzen dieselbe Funktion wie die Seiten."""
    for wert in (Decimal("209533.70"), Decimal("-14.6"), "‹x›", None):
        assert platzhalter.euro(wert) == zahlen.euro(wert)
    assert platzhalter.rendern("{{ b|euro }}", {"b": Decimal("1234.5")}) == "1.234,50\u00a0€"


def test_tabellenkoepfe_mit_abstand_in_der_alten_gestaltung() -> None:
    """Review 1, Punkt 11: „.zahl“ in Tabellenzellen ist kein Zähler-Kreis mehr (dieser
    machte „BetragoffenStatus“ ohne Abstand), sondern eine rechtsbündige Zelle."""
    css = (WURZEL / "static" / "css" / "hof.css").read_text()
    regel = css.split("th.zahl,\ntd.zahl {", 1)[1].split("}", 1)[0]
    assert "display: table-cell" in regel
    assert "text-align: right" in regel
    assert "padding: 0.5rem" in regel
    assert "background: none" in regel


# --- Stichtag-Band (Review 1, Punkt 14) ----------------------------------------------


def test_stichtag_band_auf_jeder_seite(angemeldet: Client, settings: object) -> None:
    """Review 1, Punkt 14: Ist ein fester Stichtag gesetzt, zeigt jede Seite oben das Band
    „Testhof · Stichtag …“, alte und neue Gestaltung."""
    settings.HOF_STICHTAG = D(2026, 11, 1)  # type: ignore[attr-defined]
    for name in ("kern:startseite", "kern:laeufe", "kern:stil", "stammdaten:uebersicht"):
        html = angemeldet.get(reverse(name)).content.decode()
        assert "Testhof · Stichtag 01.11.2026" in html, name


def test_ohne_stichtag_kein_band(angemeldet: Client, settings: object) -> None:
    settings.HOF_STICHTAG = None  # type: ignore[attr-defined]
    for name in ("kern:startseite", "kern:laeufe"):
        assert "stichtag-band" not in angemeldet.get(reverse(name)).content.decode()


def test_stichtag_in_dev_und_test_ist_der_des_testhofs() -> None:
    """Review 1, Punkt 14: dev und test setzen den Stichtag des Testhofs."""
    from django.conf import settings

    from testhof.aufbau import STICHTAG

    assert settings.HOF_STICHTAG == STICHTAG
    assert (
        'setdefault("HOF_STICHTAG", "2026-11-01")'
        in (WURZEL / "hof" / "settings" / "dev.py").read_text()
    )


def test_prod_hat_keinen_festen_stichtag() -> None:
    """Review 1, Punkt 14: In prod gibt es keinen festen Stichtag, auch wenn die Umgebung
    einen setzt."""
    umgebung = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "hof.settings.prod",
        "DJANGO_SECRET_KEY": "nur-fuer-den-test",
        "DATABASE_URL": "postgres://hof:hof@localhost:5432/hof_nicht_benutzt",
        "EMAIL_URL": "smtp://localhost:1025",
        "HOF_STICHTAG": "2026-11-01",
    }
    ergebnis = subprocess.run(
        [
            sys.executable,
            "-c",
            "import django; django.setup(); from django.conf import settings; "
            "print(repr(settings.HOF_STICHTAG))",
        ],
        cwd=WURZEL,
        env=umgebung,
        capture_output=True,
        text=True,
        check=True,
    )
    assert ergebnis.stdout.strip() == "None"


# --- Stilseite (Review 2, C3) ----------------------------------------------------------


def test_stilseite(angemeldet: Client, client: Client) -> None:
    """Review 2, C3: /stil/ zeigt alle Bausteine mit Beispielen (Status 200, nur angemeldet)."""
    antwort = angemeldet.get(reverse("kern:stil"))
    assert antwort.status_code == 200
    html = antwort.content.decode()
    for baustein in (
        'class="seitenkopf"',
        'class="kennzahl',
        'class="tab tab-karten"',
        'class="leer"',
        'class="badge badge-ok"',
        'class="reiter"',
        'class="aufklapper"',
        'class="feld',
        'class="knopf knopf-primaer"',
        'class="knopf knopf-gefahr"',
        'class="karte"',
        'class="toast toast-ok"',
        'id="dialog"',
        "Foto aufnehmen",
        "Datei wählen",
        'capture="environment"',
        'inputmode="decimal"',
        "209.533,70\u00a0€",
        "9.602,7",
    ):
        assert baustein in html, baustein
    assert 'lang="de"' in html
    # Fehler stehen als Satz direkt am Feld
    assert 'class="feld-fehler"' in html and 'aria-invalid="true"' in html

    angemeldet.logout()
    assert client.get(reverse("kern:stil")).status_code == 302


def test_stilseite_dialog_htmx_muster(angemeldet: Client) -> None:
    """Review 2, B-Leitlinien: Der Dialog lädt sein Formular; ein Fehler kommt am Feld in
    den Dialog zurück; Speichern ersetzt nur den betroffenen Teil, schließt den Dialog und
    meldet mit Link, was passiert ist."""
    url = reverse("kern:stil_dialog")
    htmx = {"HTTP_HX_REQUEST": "true"}
    formular = angemeldet.get(url, **htmx).content.decode()
    assert f'hx-post="{url}"' in formular and 'hx-target="#dialog-inhalt"' in formular

    fehler = angemeldet.post(url, {"miete": "90", "gueltig_ab": "2026-12-15"}, **htmx)
    assert fehler.status_code == 200
    assert "Eine neue Miete beginnt am Ersten eines Monats." in fehler.content.decode()
    assert "HX-Trigger" not in fehler

    gut = angemeldet.post(url, {"miete": "90", "gueltig_ab": "2026-12-01"}, **htmx)
    html = gut.content.decode()
    assert gut["HX-Trigger"] == '{"dialogSchliessen": true}'
    assert 'id="stil-ergebnis" class="text-2" hx-swap-oob="true"' in html
    assert '<div id="toasts" hx-swap-oob="beforeend">' in html
    assert "Neue Miete 90,00\u00a0€ ab 01.12.2026" in html and 'class="toast-link"' in html


def test_badges_fuer_alle_zustaende() -> None:
    """Review 2, C3: Status-Badges für aktiv, gekündigt, Entwurf, offen, überfällig,
    gesperrt, bereit, versandt."""
    for status, text in (
        ("aktiv", "aktiv"),
        ("gekuendigt", "gekündigt"),
        ("entwurf", "Entwurf"),
        ("offen", "offen"),
        ("ueberfaellig", "überfällig"),
        ("gesperrt", "gesperrt"),
        ("bereit", "bereit"),
        ("versandt", "versandt"),
    ):
        assert BADGES[status].text == text
    html = Template('{% load bausteine %}{% badge "ueberfaellig" %}').render(Context())
    assert html.strip() == '<span class="badge badge-fehler">überfällig</span>'


def test_werte_css_mit_hell_und_dunkel() -> None:
    """Review 2, C2: Gestaltungswerte mit Hell- und Dunkelmodus, Schriftgrößen
    13/15/17/20/24/30, Abstände in 4-px-Schritten, Radius 8 px."""
    css = (WURZEL / "static" / "css" / "werte.css").read_text()
    assert "@media (prefers-color-scheme: dark)" in css
    for groesse in ("0.8125rem", "0.9375rem", "1.0625rem", "1.25rem", "1.5rem", "1.875rem"):
        assert groesse in css
    assert "--radius: 8px;" in css
    for schritt in (4, 8, 12, 16, 20, 24, 32, 40, 48):
        assert f": {schritt}px;" in css
    assert "system-ui" in css
    bausteine = (WURZEL / "static" / "css" / "bausteine.css").read_text()
    assert "font-variant-numeric: tabular-nums" in bausteine
    assert "min-height: var(--ziel)" in bausteine  # 44 px am Handy


def test_navigation_in_gruppen(angemeldet: Client) -> None:
    """Review 1, Punkt 13 und Review 2, C1: Seitenleiste mit den Gruppen Heute · Verträge ·
    Geld · Hof · System, am Handy unten Heute · Arbeitslisten · Zähler · Übergabe · Mehr."""
    html = angemeldet.get(reverse("kern:startseite")).content.decode()
    leiste = html.split('class="leiste"', 1)[1].split("</aside>", 1)[0]
    gruppen = [g.split("</p>", 1)[0] for g in leiste.split('class="nav-gruppe-titel">')[1:]]
    assert gruppen == ["Heute", "Verträge", "Geld", "Hof", "System"]
    for titel in ("Stammdaten", "Offene Posten", "Mahnwesen", "Kassenbuch", "Läufe", "Konto"):
        assert f">{titel}</a>" in leiste
    unten = html.split('class="unten"', 1)[1].split("</nav>", 1)[0]
    for titel in ("Heute", "Arbeitslisten", "Zähler", "Übergabe", "Mehr"):
        assert f"<span>{titel}</span>" in unten
    assert f'href="{reverse("kern:startseite")}" aria-current="page"' in leiste
