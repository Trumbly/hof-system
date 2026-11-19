"""Seite „Fristen“ (Schritt 9, Aufgabe 1): Filter, Quelle, Aktion „Erledigt“."""

from __future__ import annotations

import datetime
import re

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.fristen import services
from apps.fristen.models import Frist
from apps.kern.models import Objekt
from tests.factories import ObjektFabrik

pytestmark = pytest.mark.django_db

HEUTE = timezone.localdate()
LISTE = reverse("fristen:liste")


def tage(n: int) -> datetime.date:
    return HEUTE + datetime.timedelta(days=n)


@pytest.fixture
def objekt() -> Objekt:
    return ObjektFabrik()  # type: ignore[no-any-return]


def _ids(html: str) -> set[int]:
    return {int(n) for n in re.findall(r'id="frist-(\d+)"', html)}


def _seite(client: Client, **filter: str) -> str:
    antwort = client.get(LISTE, filter)
    assert antwort.status_code == 200
    return antwort.content.decode()


def test_liste_nennt_quelle_und_zeigt_offene_auch_ueberfaellige(
    angemeldet: Client, objekt: Objekt
) -> None:
    """Abnahme Schritt 9: Jede Frist nennt in der Liste ihre Quelle; eine überfällige Frist
    steht weiter da, eine erledigte nicht mehr (Standard: alle offenen)."""
    ueberfaellig = services.frist_anlegen(
        Frist.Art.KUENDIGUNG, tage(-40), Frist.Quelle.VERTRAG, objekt
    )
    spaeter = services.frist_anlegen(Frist.Art.EICHFRIST, tage(200), Frist.Quelle.ZAEHLER, objekt)
    erledigt = services.frist_erledigen(
        services.frist_anlegen(Frist.Art.SONSTIGE, tage(-1), Frist.Quelle.HAND, objekt)
    )

    html = _seite(angemeldet)

    assert _ids(html) == {ueberfaellig.pk, spaeter.pk}
    assert "überfällig" in html
    for quelle in ("Vertrag", "Zähler"):
        assert f'<strong class="frist-quelle">{quelle}</strong>' in html
    assert _ids(_seite(angemeldet, zeitraum="erledigt")) == {erledigt.pk}


def test_filter_nach_zeitraum_art_und_quelle(angemeldet: Client, objekt: Objekt) -> None:
    """Schritt 9a: Die Fristenliste filtert nach Zeitraum, Art und Quelle."""
    a = services.frist_anlegen(Frist.Art.KUENDIGUNG, tage(-2), Frist.Quelle.VERTRAG, objekt)
    b = services.frist_anlegen(Frist.Art.KAUTIONSABRECHNUNG, tage(10), Frist.Quelle.KAUTION, objekt)
    c = services.frist_anlegen(Frist.Art.BANKFREIGABE, tage(60), Frist.Quelle.BANKFREIGABE, objekt)

    assert _ids(_seite(angemeldet, zeitraum="ueberfaellig")) == {a.pk}
    assert _ids(_seite(angemeldet, zeitraum="14")) == {a.pk, b.pk}
    assert _ids(_seite(angemeldet, zeitraum="90")) == {a.pk, b.pk, c.pk}
    assert _ids(_seite(angemeldet, art=Frist.Art.KAUTIONSABRECHNUNG)) == {b.pk}
    assert _ids(_seite(angemeldet, quelle=Frist.Quelle.BANKFREIGABE)) == {c.pk}
    assert _ids(_seite(angemeldet, zeitraum="14", quelle=Frist.Quelle.VERTRAG)) == {a.pk}
    # Unbekannte Werte fallen auf den Standard zurück.
    assert _ids(_seite(angemeldet, zeitraum="x", art="x", quelle="x")) == {a.pk, b.pk, c.pk}


def test_aktion_erledigt_per_htmx(angemeldet: Client, objekt: Objekt) -> None:
    """Schritt 9a: Aktion „Erledigt“ in der Liste setzt das Erledigt-Datum auf heute."""
    frist = services.frist_anlegen(Frist.Art.SONSTIGE, tage(-1), Frist.Quelle.HAND, objekt)
    url = reverse("fristen:erledigt", args=[frist.pk])
    assert url in _seite(angemeldet)

    antwort = angemeldet.post(url, HTTP_HX_REQUEST="true")

    assert antwort.status_code == 200
    assert f"Erledigt am {HEUTE:%d.%m.%Y}" in antwort.content.decode()
    frist.refresh_from_db()
    assert frist.erledigt_am == HEUTE
    assert frist.pk not in _ids(_seite(angemeldet))
    assert angemeldet.post(url).status_code == 400  # schon erledigt


def test_aktion_erledigt_ohne_htmx_leitet_zurueck(angemeldet: Client, objekt: Objekt) -> None:
    frist = services.frist_anlegen(Frist.Art.SONSTIGE, tage(-1), Frist.Quelle.HAND, objekt)
    url = reverse("fristen:erledigt", args=[frist.pk])

    antwort = angemeldet.post(url, {"zurueck": f"{LISTE}?zeitraum=14"})
    assert antwort.status_code == 302
    assert antwort["Location"] == f"{LISTE}?zeitraum=14"

    zweite = services.frist_anlegen(Frist.Art.SPERRFRIST, tage(-1), Frist.Quelle.HAND, objekt)
    antwort = angemeldet.post(
        reverse("fristen:erledigt", args=[zweite.pk]), {"zurueck": "https://boese.invalid/"}
    )
    assert antwort["Location"] == LISTE


def test_liste_am_handy_bedienbar(angemeldet: Client, objekt: Objekt) -> None:
    """Schritt 9a: Die Seite ist am Handy bedienbar: Viewport, Navigation, Filter mit
    Beschriftung, Knöpfe als echte Formulare (auch ohne JavaScript)."""
    services.frist_anlegen(Frist.Art.SONSTIGE, tage(1), Frist.Quelle.HAND, objekt)
    html = _seite(angemeldet)

    assert 'name="viewport"' in html
    assert f'href="{LISTE}" aria-current="page"' in html
    for feld in ("zeitraum", "art", "quelle"):
        assert f'<label for="{feld}">' in html
    assert '<form method="post" action="/fristen/' in html
    assert "<table" not in html  # Karten statt breiter Tabelle


def test_liste_nur_angemeldet(client: Client) -> None:
    antwort = client.get(LISTE)
    assert antwort.status_code == 302
    assert reverse("account_login") in antwort["Location"]


def _reihenfolge(html: str) -> list[int]:
    return [int(n) for n in re.findall(r'id="frist-(\d+)"', html)]


@pytest.mark.parametrize("zeitraum", ["offen", "alle", "90"])
def test_liste_ueberfaellig_zuerst_dann_nach_datum(angemeldet: Client, zeitraum: str) -> None:
    """Review 2, A5: Fristen stehen nach Datum, überfällige zuerst. Vorher kam der
    31.12.2029 vor dem 11.11.2026, weil ``annotate(Count)`` die Sortierung des Modells
    aufhebt. Angelegt in verkehrter Reihenfolge."""
    a, b, c, d = (ObjektFabrik() for _ in range(4))
    weit = services.frist_anlegen(Frist.Art.EICHFRIST, tage(80), Frist.Quelle.ZAEHLER, a)
    bald = services.frist_anlegen(Frist.Art.SONSTIGE, tage(5), Frist.Quelle.HAND, b)
    morgen = services.frist_anlegen(Frist.Art.SONSTIGE, tage(1), Frist.Quelle.HAND, c)
    ueber = services.frist_anlegen(Frist.Art.KUENDIGUNG, tage(-3), Frist.Quelle.VERTRAG, d)

    reihe = _reihenfolge(_seite(angemeldet, zeitraum=zeitraum))

    assert reihe == [ueber.pk, morgen.pk, bald.pk, weit.pk]


def test_alle_erledigte_ueberfaellige_nicht_vorne(angemeldet: Client) -> None:
    """Review 2, A5: Unter „alle“ zählt eine erledigte alte Frist nicht als überfällig; sie
    steht nach den offenen überfälligen."""
    a, b, c = (ObjektFabrik() for _ in range(3))
    erledigt = services.frist_erledigen(
        services.frist_anlegen(Frist.Art.SONSTIGE, tage(-30), Frist.Quelle.HAND, a)
    )
    ueber = services.frist_anlegen(Frist.Art.SONSTIGE, tage(-2), Frist.Quelle.HAND, b)
    spaeter = services.frist_anlegen(Frist.Art.SONSTIGE, tage(10), Frist.Quelle.HAND, c)

    reihe = _reihenfolge(_seite(angemeldet, zeitraum="alle"))

    assert reihe == [ueber.pk, erledigt.pk, spaeter.pk]
