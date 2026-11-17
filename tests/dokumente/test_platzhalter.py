"""Platzhalter in einer abgeschlossenen Engine (Schritt 3c, Aufgabe 1; C2)."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from apps.dokumente import platzhalter, vorlagenarten
from apps.dokumente.platzhalter import PlatzhalterFehler, UnbekannterPlatzhalter
from tests.factories import ObjektFabrik


def test_rendert_platzhalter_mit_filtern_und_maskierung() -> None:
    """Schritt 3c, Aufgabe 1: Platzhalter wie {{ partei.name }} werden ersetzt, Werte für
    HTML maskiert; euro und datum formatieren deutsch."""
    text = (
        "<p>{{ empfaenger.name }}: {{ betrag.offen|euro }} bis {{ frist|datum }}</p>"
        "{% for p in posten %}<i>{{ p }}</i>{% endfor %}"
        "{% if extra %}x{% else %}y{% endif %}"
    )
    ergebnis = platzhalter.rendern(
        text,
        {
            "empfaenger": {"name": "A & <B>"},
            "betrag": {"offen": Decimal("1234.5")},
            "frist": datetime.date(2026, 12, 1),
            "posten": ["eins", "zwei"],
            "extra": False,
        },
    )
    assert ergebnis == (
        "<p>A &amp; &lt;B&gt;: 1.234,50 € bis 01.12.2026</p><i>eins</i><i>zwei</i>y"
    )


def test_unbekannter_platzhalter_ist_ein_fehler_kein_leerer_text() -> None:
    """Schritt 3c, Aufgabe 1: Unbekannte Platzhalter ergeben einen Fehler, nicht still
    einen leeren Text – beim Rendern gegen den Kontext …"""
    with pytest.raises(UnbekannterPlatzhalter, match=r"empfaenger\.nmae"):
        platzhalter.rendern("{{ empfaenger.nmae }}", {"empfaenger": {"name": "x"}})
    with pytest.raises(UnbekannterPlatzhalter, match="vertrag"):
        platzhalter.rendern("{{ vertrag.nummer }}", {})
    with pytest.raises(UnbekannterPlatzhalter, match="frist"):
        platzhalter.rendern('{{ frist|default:"" }}', {})
    with pytest.raises(UnbekannterPlatzhalter, match="geheim"):
        platzhalter.rendern("{% if geheim %}ja{% endif %}", {})


def test_unbekannter_platzhalter_beim_pruefen_gegen_die_vorlagenart() -> None:
    """Schritt 3c, Aufgabe 1: … und beim Prüfen gegen die erlaubten Platzhalter der Art."""
    erlaubt = vorlagenarten.holen("mahnung").erlaubte_platzhalter()
    platzhalter.pruefen(
        "{{ empfaenger.name }} {{ betrag.offen|euro }} "
        "{% for p in forderung.posten %}{{ p.monat }}{{ forloop.counter }}{% endfor %}",
        erlaubt,
    )
    with pytest.raises(PlatzhalterFehler, match=r"partei\.iban"):
        platzhalter.pruefen("{{ partei.iban }}", erlaubt)
    with pytest.raises(PlatzhalterFehler, match="sonstwas"):
        platzhalter.pruefen("{% if sonstwas %}x{% endif %}", erlaubt)
    with pytest.raises(PlatzhalterFehler, match="x"):
        platzhalter.pruefen("{{ empfaenger.name|default:x }}", erlaubt)


@pytest.mark.parametrize(
    "text",
    [
        "{% load static %}",
        "{% include 'base.html' %}",
        "{% extends 'base.html' %}",
        "{% url 'kern:startseite' %}",
        "{% debug %}",
        "{% with a=b %}{% endwith %}",
        "{{ empfaenger.name|safe }}",
        "{{ empfaenger.name|pprint }}",
        "{% if x %}",
    ],
)
def test_nur_if_und_for_und_freigegebene_filter(text: str) -> None:
    """Schritt 3c, Aufgabe 1: Keine Tags wie {% load %}, keine Filter außerhalb der Liste."""
    with pytest.raises(PlatzhalterFehler):
        platzhalter.rendern(text, {"empfaenger": {"name": "x"}, "x": 1, "b": 1})


@pytest.mark.django_db
def test_kontext_nur_mit_einfachen_werten() -> None:
    """Schritt 3c, Aufgabe 1: Nur ein freigegebener Kontext, keine Modell-Objekte (sonst
    könnte eine Vorlage jedes Feld und jede Beziehung lesen)."""
    objekt = ObjektFabrik()
    with pytest.raises(PlatzhalterFehler, match="Objekt"):
        platzhalter.rendern("{{ objekt.anschrift }}", {"objekt": objekt})
    with pytest.raises(PlatzhalterFehler):
        platzhalter.rendern("{{ x }}", {"x": [object()]})


def test_beispielkontext_zeigt_jeden_platzhalter_mit_namen() -> None:
    """Schritt 3c: Die Vorschau einer Vorlage zeigt statt Werten die Namen der Platzhalter."""
    kontext = platzhalter.beispielkontext(["empfaenger.name", "betrag"], "{{ betrag.offen }}")
    assert kontext == {
        "empfaenger": {"name": "‹empfaenger.name›"},
        "betrag": {"offen": "‹betrag.offen›"},
    }
    assert platzhalter.rendern("{{ betrag.offen|euro }}", kontext) == "‹betrag.offen›"
