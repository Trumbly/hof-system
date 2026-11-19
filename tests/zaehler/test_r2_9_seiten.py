"""Zähler-Seiten in der neuen Gestaltung (PR R2-9): Aktionen als Dialoge, Ablesung am Handy als
Karten mit Kameraknopf, „geschätzt“ und vorigem Stand, Besuchsliste druckbar."""

from __future__ import annotations

import datetime
import json

import pytest
from django.test import Client
from django.urls import reverse

from apps.kern.models import Objekt
from apps.zaehler import services
from apps.zaehler.models import Zaehler
from tests.factories import ObjektFabrik

pytestmark = pytest.mark.django_db
D = datetime.date
HTMX = {"HTTP_HX_REQUEST": "true"}


def test_ablesung_je_zaehler_eine_karte(
    angemeldet: Client, objekt: Objekt, stromzaehler: Zaehler
) -> None:
    """R2-9: Je Zähler eine Karte mit Stand (inputmode=decimal), Kameraknopf, „geschätzt“
    und dem vorigen Stand als Hilfe."""
    services.stand_erfassen(stromzaehler, D(2024, 12, 31), "1234.5", art="geschaetzt")
    html = angemeldet.get(reverse("zaehler:ablesung", args=[objekt.pk])).content.decode()
    assert f'<section class="karte" id="zaehler-{stromzaehler.pk}">' in html
    assert f'name="stand_{stromzaehler.pk}"' in html and 'inputmode="decimal"' in html
    assert "Foto aufnehmen" in html and 'capture="environment"' in html
    assert f'name="geschaetzt_{stromzaehler.pk}"' in html and "geschätzt (nicht abgelesen)" in html
    assert "Voriger Stand: 1.234,5 am 31.12.2024 (geschätzt)" in html


def test_nach_der_ablesung_geht_es_zum_naechsten_objekt(
    angemeldet: Client, objekt: Objekt, stromzaehler: Zaehler
) -> None:
    """Review 2, B-Leitlinien: Nach einer Aktion kommt der nächste Posten; die Meldung nennt,
    was gespeichert ist, und verlinkt auf die Ablesung des nächsten Objekts."""
    naechstes = ObjektFabrik(kuerzel="ZZZ")
    services.zaehler_anlegen("TZ-W-9", "wasser", objekt=naechstes)
    antwort = angemeldet.post(
        reverse("zaehler:ablesung", args=[objekt.pk]),
        {"datum": "2025-12-31", f"stand_{stromzaehler.pk}": "812,4"},
        follow=True,
    )
    html = antwort.content.decode()
    assert f"1 Stand gespeichert ({objekt.kuerzel})" in html
    assert f'href="{reverse("zaehler:ablesung", args=[naechstes.pk])}">weiter zu ZZZ' in html


def test_stand_erfassen_als_dialog(angemeldet: Client, stromzaehler: Zaehler) -> None:
    """Review 2, B-Leitlinien: „Stand erfassen“ öffnet einen Dialog; Fehler stehen als Satz am
    Feld, das Speichern ersetzt Kopf und Stände und meldet mit Link."""
    url = reverse("zaehler:zaehler", args=[stromzaehler.pk])
    seite = angemeldet.get(url).content.decode()
    assert f'hx-get="{url}?dialog=stand"' in seite

    dialog = angemeldet.get(url, {"dialog": "stand"}, **HTMX).content.decode()
    assert "Stand erfassen · TZ-S-1" in dialog and "Foto aufnehmen" in dialog

    fehler = angemeldet.post(
        url, {"aktion": "stand", "stand-datum": "2024-12-31", "stand-stand": ""}, **HTMX
    )
    assert fehler.status_code == 200 and 'class="feld-fehler"' in fehler.content.decode()

    antwort = angemeldet.post(
        url, {"aktion": "stand", "stand-datum": "2024-12-31", "stand-stand": "700"}, **HTMX
    )
    html = antwort.content.decode()
    assert "Stand 700 vom 31.12.2024 gespeichert" in html
    assert 'id="zaehler-kopf" hx-swap-oob="true"' in html
    assert 'id="staende" hx-swap-oob="true"' in html
    assert "dialogSchliessen" in json.loads(antwort["HX-Trigger"])


def test_neuer_zaehler_im_dialog_der_uebersicht(angemeldet: Client, objekt: Objekt) -> None:
    """R2-9: „Neuer Zähler“ als Dialog der Übersicht; danach ist die Liste ersetzt."""
    dialog = angemeldet.get(reverse("zaehler:neu"), **HTMX).content.decode()
    assert "Neuer Zähler" in dialog and 'class="dialog-formular"' in dialog
    antwort = angemeldet.post(
        reverse("zaehler:neu"),
        {"nummer": "TZ-W-5", "art": "wasser", "objekt": objekt.pk, "bezeichnung": "Keller"},
        **HTMX,
    )
    html = antwort.content.decode()
    zaehler = Zaehler.objects.get(nummer="TZ-W-5")
    assert "Wasser TZ-W-5 angelegt" in html
    assert reverse("zaehler:zaehler", args=[zaehler.pk]) in html
    assert 'id="zaehler-liste" class="stapel" hx-swap-oob="true"' in html


def test_besuchsliste_druckbar(angemeldet: Client, stromzaehler: Zaehler) -> None:
    """R2-9: Die Besuchsliste bleibt druckbar: Knopf „Drucken“, Spalte „neuer Stand“ nur im
    Druck, Leiste und Knöpfe verschwinden im Druck."""
    html = angemeldet.get(reverse("zaehler:besuch")).content.decode()
    assert "window.print()" in html and 'class="nur-druck">neuer Stand' in html
    assert 'class="knopf knopf-primaer nur-bildschirm"' in html
