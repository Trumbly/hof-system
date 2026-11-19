"""Review 3, Punkt 2: Radio- und Checkbox-Gruppen im Baustein ``{% feld %}``.

Vorher setzte ``_attrs`` die Klasse ``feld-haken`` (20 × 20 px) auch an den ``div``, den Django
um die Optionen legt; Text, Fehlermeldung und nächstes Feld schoben sich übereinander. Jetzt
steht die Gruppe in einem ``fieldset`` mit ``legend``, die Optionen untereinander mit dem
Label neben dem Feld, Fehler und Hilfe unter der Gruppe.
"""

from __future__ import annotations

import datetime
import re

import pytest
from django import forms
from django.template import Context, Template
from django.test import Client
from django.urls import reverse

from apps.kern.models import Objekt
from apps.stammdaten import services as stammdaten
from apps.stammdaten.models import Vertrag
from tests.anbindungen.conftest import aktiver_vertrag, vermarktete_einheit
from tests.factories import ObjektFabrik

D = datetime.date


class GruppenForm(forms.Form):
    weg = forms.ChoiceField(
        label="Wer keine E-Mail-Adresse hat",
        choices=[("brief", "bekommt einen Brief"), ("auslassen", "wird ausgelassen")],
        widget=forms.RadioSelect,
        help_text="Gilt nur, wenn die Serie per Mail geht.",
    )
    empfaenger = forms.MultipleChoiceField(
        label="Empfänger",
        choices=[("1", "FELD"), ("2", "HALLE")],
        widget=forms.CheckboxSelectMultiple,
        error_messages={"required": "Bitte mindestens einen Empfänger auswählen."},
    )
    notiz = forms.CharField(label="Notiz", required=False)


def _feld(form: forms.Form, name: str) -> str:
    vorlage = Template("{% load bausteine %}{% feld form." + name + " %}")
    return vorlage.render(Context({"form": form}))


def _fieldset(html: str) -> str:
    treffer = re.search(r"<fieldset\b.*?</fieldset>", html, re.S)
    assert treffer, html
    return treffer.group(0)


@pytest.mark.parametrize("name", ["weg", "empfaenger"])
def test_gruppe_mit_fieldset_und_legend(name: str) -> None:
    """Review 3, Punkt 2: Die Gruppe steht in einem fieldset mit legend; jede Option hat ihr
    Label neben dem Feld (Feld im Label), der Rahmen der Optionen trägt keine Klasse des
    Eingabefelds mehr."""
    html = _feld(GruppenForm(), name)
    gruppe = _fieldset(html)
    assert gruppe.startswith('<fieldset class="feld feld-gruppe-optionen')
    legende = re.search(r'<legend class="feld-label">(.*?)</legend>', gruppe, re.S)
    assert legende and GruppenForm.base_fields[name].label in legende.group(1)
    assert 'for=""' not in gruppe
    # Rahmen der Optionen: nur .feld-optionen, nie .feld-haken (die 20-px-Box von vorher)
    assert f'<div class="feld-optionen" id="id_{name}">' in gruppe
    assert not re.search(r'<div[^>]*class="[^"]*feld-haken', gruppe)
    optionen = re.findall(r'<label class="feld-option" for="(id_\w+)"><input ([^>]*)>', gruppe)
    assert len(optionen) == 2
    for kennung, attribute in optionen:
        assert f'id="{kennung}"' in attribute and 'class="feld-haken"' in attribute


def test_fehler_unter_der_gruppe() -> None:
    """Review 3, Punkt 2: Die Fehlermeldung steht unter der Gruppe (nach der letzten Option),
    der Hilfetext danach; das fieldset verweist auf beide, die Optionen sind als ungültig
    markiert."""
    form = GruppenForm({"notiz": "x"})
    assert not form.is_valid()
    for name in ("weg", "empfaenger"):
        gruppe = _fieldset(_feld(form, name))
        letzte_option = gruppe.rindex("</label>")
        fehler = gruppe.index(f'<p class="feld-fehler" id="id_{name}-fehler">')
        assert letzte_option < fehler
        assert 'aria-invalid="true"' in gruppe
        assert "feld-fehlerhaft" in gruppe.split(">", 1)[0]
    gruppe = _fieldset(_feld(form, "weg"))
    assert gruppe.index("feld-fehler") < gruppe.index('class="feld-hilfe"')
    assert 'aria-describedby="id_weg-fehler id_weg-hilfe"' in gruppe
    assert "Bitte mindestens einen Empfänger auswählen." in _feld(form, "empfaenger")


def test_gruppe_behaelt_wahl_und_pflicht() -> None:
    """Review 3, Punkt 2: Die gewählte Option bleibt gewählt; Pflicht-Radios tragen
    ``required``, Checkbox-Gruppen nicht (dort genügt eine von mehreren)."""
    form = GruppenForm({"weg": "auslassen", "empfaenger": ["2"]})
    weg = _feld(form, "weg")
    assert re.search(r'<input type="radio"[^>]*value="auslassen"[^>]*checked', weg)
    assert re.search(r'<input type="radio"[^>]*value="brief"[^>]*required', weg)
    empfaenger = _feld(form, "empfaenger")
    assert re.search(r'value="2"[^>]*checked', empfaenger)
    assert "required" not in empfaenger


def test_einzelfeld_unveraendert() -> None:
    """Review 3, Punkt 2: Andere Felder bleiben, wie sie waren (Label mit for, kein fieldset)."""
    html = _feld(GruppenForm(), "notiz")
    assert "<fieldset" not in html
    assert '<label class="feld-label" for="id_notiz">' in html


@pytest.mark.django_db
def test_stilseite_zeigt_optionen_normal_und_mit_fehler(angemeldet: Client) -> None:
    """Review 3, Punkt 2: /stil/ zeigt die Radio- und Checkbox-Gruppe normal und im
    Fehlerzustand."""
    html = angemeldet.get(reverse("kern:stil")).content.decode()
    anfang = html.index('<section class="stil-abschnitt" id="optionen">')
    text = html[anfang : html.index('<section class="stil-abschnitt" id="dateien">')]
    assert text.count("<fieldset") >= 6
    assert 'type="radio"' in text and 'type="checkbox"' in text
    assert text.count('class="feld-fehler"') >= 2
    assert "liegt vor dem Fristende" in text


@pytest.mark.django_db
def test_serie_gruppen_mit_fehler_unter_der_gruppe(angemeldet: Client) -> None:
    """Review 3, Punkt 2: Serienmitteilung leer abgesendet: „Wer keine E-Mail-Adresse hat“
    und die Empfänger stehen als Gruppen da, der Fehler unter den Empfängern."""
    url = reverse("dokumente:serie_neu")
    html = angemeldet.post(url, {"schritt": "vorschau"}).content.decode()
    gruppen = re.findall(r"<fieldset\b.*?</fieldset>", html, re.S)
    ohne_mail = next(g for g in gruppen if 'name="ohne_mail"' in g)
    assert "Wer keine E-Mail-Adresse hat" in ohne_mail
    auswahl = next((g for g in gruppen if 'name="auswahl"' in g), None)
    if auswahl is not None:  # nur, wenn es Objekte zur Auswahl gibt
        assert auswahl.rindex("</label>") < auswahl.index("Bitte mindestens einen Empfänger")


@pytest.mark.django_db
def test_kuendigung_wunschtermin_als_gruppe(angemeldet: Client) -> None:
    """Review 3, Punkt 2: Im Kündigungsdialog steht „Wunschtermin vor dem Fristende“ als
    Gruppe mit legend, die Fehlermeldung unter den Optionen."""
    objekt: Objekt = ObjektFabrik(kuerzel="HOF")
    vertrag: Vertrag = aktiver_vertrag(vermarktete_einheit(objekt))
    zugang = D(2026, 11, 17)
    wunsch = D(2026, 12, 31)
    assert wunsch < stammdaten.kuendigung_fristende(vertrag, zugang)
    antwort = angemeldet.post(
        reverse("stammdaten:vertrag", args=[vertrag.pk]),
        {
            "aktion": "kuendigung",
            "kuendigung-zugang_am": zugang.isoformat(),
            "kuendigung-von": "mieter",
            "kuendigung-zum_wunsch": wunsch.isoformat(),
        },
        HTTP_HX_REQUEST="true",
    )
    gruppe = _fieldset(antwort.content.decode())
    assert '<legend class="feld-label">Wunschtermin vor dem Fristende' in gruppe
    assert gruppe.count('type="radio" name="kuendigung-wunsch_wahl"') == 2
    assert gruppe.rindex("</label>") < gruppe.index("liegt vor dem Fristende")
