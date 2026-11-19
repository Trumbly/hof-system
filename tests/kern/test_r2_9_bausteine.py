"""Neue Bausteine aus PR R2-9: Unternavigation, Filterleiste, Formular auf der Seite, Druck,
Badges für Läufe und Schätzwerte; alle auf ``/stil/``."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.conf import settings
from django.template import Context, Template
from django.test import Client, RequestFactory
from django.urls import resolve, reverse

from apps.kern.templatetags.bausteine import BADGES

pytestmark = pytest.mark.django_db
CSS = Path(settings.BASE_DIR, "static", "css", "bausteine.css")


def test_unternavigation_als_reiter_mit_aktiver_seite() -> None:
    """Review 2, B11: Die Unternavigation (Bank, Kassenbuch, Mahnwesen, Anbindungen) ist eine
    Zeile Reiter mit der aktiven Seite, statt loser Links mit „·“ im Raster."""
    anfrage = RequestFactory().get(reverse("mahnwesen:stufe", args=[3]))
    anfrage.resolver_match = resolve(anfrage.path)
    html = Template(
        '{% load bausteine %}{% unternavigation "Mahnwesen" "mahnwesen:faelle=Mahnfälle" '
        '"mahnwesen:stufen,mahnwesen:stufe=Stufen" %}'
    ).render(Context({"request": anfrage}))
    assert '<nav class="reiter unternavigation" aria-label="Mahnwesen">' in html
    assert f'<a href="{reverse("mahnwesen:stufen")}" aria-current="page">Stufen</a>' in html
    assert f'<a href="{reverse("mahnwesen:faelle")}">Mahnfälle</a>' in html
    assert "·" not in html


def test_neue_bausteine_auf_der_stilseite(angemeldet: Client) -> None:
    """Review 2, C3: Jeder neue Baustein steht auf /stil/."""
    html = angemeldet.get(reverse("kern:stil")).content.decode()
    for anker in ('id="unternavigation"', 'id="filterleiste"', 'id="formular-seite"'):
        assert anker in html
    assert 'class="reiter unternavigation"' in html and 'class="filterleiste"' in html
    assert '<span class="badge badge-neutral">übersprungen</span>' in html
    assert '<span class="badge badge-warnung">geschätzt</span>' in html


def test_badges_fuer_laeufe_und_schaetzwerte() -> None:
    """Review 2, B16: Status-Badges für alle Zustände eines Laufs, auch „übersprungen“."""
    for status, text, art in (
        ("laeuft", "läuft", "hinweis"),
        ("erfolgreich", "erfolgreich", "ok"),
        ("mit_fehlern", "mit Fehlern", "warnung"),
        ("abgebrochen", "abgebrochen", "fehler"),
        ("uebersprungen", "übersprungen", "neutral"),
        ("gescheitert", "gescheitert", "fehler"),
        ("geschaetzt", "geschätzt", "warnung"),
    ):
        assert (BADGES[status].text, BADGES[status].art) == (text, art)


def test_css_fuer_filterleiste_druck_und_karten_tabellen() -> None:
    """R2-9: Die neuen Regeln stehen zentral in bausteine.css: Filterleiste, Druck ohne
    Leiste und Knöpfe, Karten-Tabellen am Handy ohne waagrechtes Scrollen."""
    css = CSS.read_text()
    assert ".filterleiste {" in css and ".filter-aktiv {" in css
    assert "@media print" in css and ".neu .nur-druck" in css
    assert ".tab-karten td > * {" in css and "overflow-wrap: anywhere" in css
