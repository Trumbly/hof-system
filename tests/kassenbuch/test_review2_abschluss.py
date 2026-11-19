"""Review 2, A11: Knopf „Monat jetzt abschließen“, Name des Mandanten, IDs als Links;
Monatsfeld mit deutschen Namen statt ``type=month``."""

from __future__ import annotations

import datetime
import re

import pytest
from django.test import Client
from django.urls import reverse

from apps.kassenbuch.forms import BerichtForm
from apps.kassenbuch.models import Monatsabschluss
from tests.kassenbuch.conftest import ganz
from tests.kassenbuch.test_abschluss import JAN, Januar, januar  # noqa: F401

SEITE = reverse("kassenbuch:abschluesse")


def _knopf(mandant_pk: int, monat: datetime.date) -> str:
    return reverse("kassenbuch:abschluss_jetzt", args=[mandant_pk, monat.year, monat.month])


@pytest.fixture(autouse=True)
def _heute_im_februar(monkeypatch: pytest.MonkeyPatch) -> None:
    """Der Januar 2026 ist vorbei."""
    from django.utils import timezone

    monkeypatch.setattr(timezone, "localdate", lambda *a, **k: datetime.date(2026, 2, 10))


def test_bereiter_monat_hat_knopf_und_schliesst_ab(januar: Januar, angemeldet: Client) -> None:  # noqa: F811
    """Review 2, A11: Ein Monat im Zustand „bereit“ hat den Knopf „Monat jetzt abschließen“
    (mit Rückfrage). Ein Klick schließt ihn ab; die Meldung nennt Monat und Mandant."""
    ganz(januar, januar.miete_jan)
    ganz(januar, januar.strom_jan, januar.strom)

    seite = angemeldet.get(SEITE).content.decode()
    assert "Monat 01/2026 jetzt abschließen" in seite
    assert _knopf(januar.gbr.pk, JAN) in seite
    # R2-9: Rückfrage als Dialog (statt confirm() des Browsers)
    assert f'hx-get="{_knopf(januar.gbr.pk, JAN)}"' in seite
    dialog = angemeldet.get(_knopf(januar.gbr.pk, JAN)).content.decode()
    assert "Monat 01/2026 für" in dialog and "Jetzt abschließen" in dialog
    assert not Monatsabschluss.objects.filter(abgeschlossen_am__isnull=False).exists()
    assert januar.gbr.bezeichnung in seite

    antwort = angemeldet.post(_knopf(januar.gbr.pk, JAN), follow=True)
    text = antwort.content.decode()
    assert "Monat 01/2026 für" in text and "abgeschlossen" in text
    assert Monatsabschluss.objects.get(mandant=januar.gbr, monat=JAN).abgeschlossen


def test_nicht_bereiter_monat_nennt_luecken_als_links(januar: Januar, angemeldet: Client) -> None:  # noqa: F811
    """Review 2, A11: Statt „Mandant #18“ steht der Name; die Umsätze sind Links. Kein Knopf,
    und ein POST schließt nichts ab."""
    ganz(januar, januar.miete_jan)

    seite = angemeldet.get(SEITE).content.decode()
    assert f"Mandant #{januar.gbr.pk}" not in seite
    assert januar.gbr.bezeichnung in seite
    assert f'href="{reverse("kassenbuch:umsatz", args=[januar.strom_jan.pk])}"' in seite
    assert "jetzt abschließen" not in seite

    angemeldet.post(_knopf(januar.gbr.pk, JAN))
    assert not Monatsabschluss.objects.filter(
        mandant=januar.gbr, monat=JAN, abgeschlossen_am__isnull=False
    ).exists()


def test_nur_der_naechste_monat(januar: Januar, angemeldet: Client) -> None:  # noqa: F811
    """Review 2, A11: Abgeschlossen wird der Reihe nach; ein späterer Monat geht nicht."""
    ganz(januar, januar.miete_jan)
    ganz(januar, januar.strom_jan, januar.strom)
    antwort = angemeldet.post(_knopf(januar.gbr.pk, datetime.date(2026, 3, 1)), follow=True)
    assert "der Reihe nach" in antwort.content.decode()
    assert not Monatsabschluss.objects.filter(abgeschlossen_am__isnull=False).exists()


def test_monatsfeld_mit_deutschen_namen() -> None:
    """Review 2, A11: Der Monatsbericht wählt Monat und Jahr aus zwei Listen mit deutschen
    Namen; ``type=month`` zeigt der Browser englisch („October 2026“). Links mit
    ``?monat=2026-10`` gehen weiter."""
    form = BerichtForm(initial={"monat": "2026-10"})
    html = str(form["monat"])
    assert 'type="month"' not in html
    assert re.search(r'<option value="10" selected>Oktober</option>', html)
    assert re.search(r'<option value="2026" selected>2026</option>', html)
    assert "März" in html

    neu = BerichtForm({"monat_0": "03", "monat_1": "2026"})
    neu.is_valid()
    assert neu.cleaned_data["monat"] == datetime.date(2026, 3, 1)
    alt = BerichtForm({"monat": "2026-10"})
    alt.is_valid()
    assert alt.cleaned_data["monat"] == datetime.date(2026, 10, 1)


@pytest.mark.parametrize("name", ["kern:startseite", "kassenbuch:bericht", "fristen:liste"])
def test_seiten_sind_deutsch(name: str, angemeldet: Client) -> None:
    """Review 2, A11: Jede Seite trägt ``<html lang="de">``; dann zeigen Datums- und
    Auswahlfelder deutsche Texte."""
    assert '<html lang="de">' in angemeldet.get(reverse(name)).content.decode()


def test_anmeldung_ist_deutsch(client: Client, db: None) -> None:
    """Review 2, A11: auch die Anmeldung (ohne Sitzung)."""
    assert '<html lang="de">' in client.get(reverse("account_login")).content.decode()
