"""Protokoll in der neuen Gestaltung (Review 2, B14; PR R2-9): Filter in der Oberfläche nach
Vertrag bzw. Einheit (Suchfeld), Person und Zeitraum; Feldnamen deutsch."""

from __future__ import annotations

import datetime
import re

import pytest
from django.test import Client
from django.urls import reverse

from apps.kern import vorgaenge
from apps.kern.models import Benutzer
from apps.kern.protokoll import protokollierte_modelle
from apps.stammdaten import services as stammdaten
from apps.stammdaten.models import Partei
from tests.soll.conftest import VertragBauer
from tests.stammdaten.conftest import mieter_anlegen

pytestmark = pytest.mark.django_db

SEITE = reverse("kern:protokoll")
ZUGANG = datetime.date(2026, 5, 20)


def _titel(vertrag: object) -> str:
    return f"Kündigung {vertrag.nummer} erfasst"  # type: ignore[attr-defined]


def test_suchfeld_findet_vertrag_einheit_und_person(
    neuer_vertrag: VertragBauer, mieter: Partei, angemeldet: Client
) -> None:
    """Review 2, B14: Filter „Vertrag, Einheit oder Person“ als Suchfeld mit Vorschlägen:
    V-Nummer, NW-ID oder Name; die Vorschläge nennen den Mieter mit Namen (Max' Oberfläche)."""
    vertrag = neuer_vertrag()
    anderer = neuer_vertrag()
    stammdaten.kuendigung_erfassen(vertrag, ZUGANG)
    einheit = vertrag.einheiten.get()

    seite = angemeldet.get(SEITE).content.decode()
    assert 'list="protokoll-bezuege"' in seite and '<datalist id="protokoll-bezuege">' in seite
    assert f'<option value="{vertrag.nummer} · {einheit.nw_id} · {mieter.anzeigename}">' in seite

    for suche in (vertrag.nummer, einheit.nw_id, f"{vertrag.nummer} · {einheit.nw_id} · x"):
        html = angemeldet.get(SEITE, {"suche": suche}).content.decode()
        assert _titel(vertrag) in html, suche
        assert _titel(anderer) not in html
    nach_person = angemeldet.get(SEITE, {"suche": mieter.nachname}).content.decode()
    assert _titel(vertrag) in nach_person


def test_mehrdeutige_person_bietet_auswahl(
    neuer_vertrag: VertragBauer, mieter: Partei, angemeldet: Client
) -> None:
    """Review 2, B14: Passt der Name auf mehrere Personen, zeigt die Seite sie zur Auswahl;
    passt nichts, steht ein deutscher Satz am Feld."""
    zweite = mieter_anlegen(mieter.nachname)  # gleicher Nachname
    html = angemeldet.get(SEITE, {"suche": mieter.nachname}).content.decode()
    assert "Mehrere passen" in html
    assert f"Partei #{mieter.pk}" in html and f"Partei #{zweite.pk}" in html
    assert f"bezug=stammdaten.partei%3A{zweite.pk}" in html

    leer = angemeldet.get(SEITE, {"suche": "Gibtsnicht Niemand"}).content.decode()
    assert "Zu „Gibtsnicht Niemand“ passt kein Vertrag, keine Einheit und keine Person." in leer


def test_link_mit_bezug_zeigt_den_filter_im_suchfeld(
    neuer_vertrag: VertragBauer, angemeldet: Client
) -> None:
    """Review 2, B14: Links von anderen Seiten (``?bezug=``) füllen das Suchfeld vor."""
    vertrag = neuer_vertrag()
    html = angemeldet.get(SEITE, {"bezug": f"stammdaten.vertrag:{vertrag.pk}"}).content.decode()
    assert f"Nur Vorgänge zu Vertrag {vertrag.nummer}" in html
    assert re.search(rf'name="suche" value="{vertrag.nummer} · ', html)


def test_filter_nach_benutzer_lauf_und_zeitraum(
    neuer_vertrag: VertragBauer, angemeldet: Client
) -> None:
    """Review 2, B14: Filter „Wer“ (Benutzer oder Läufe) und Zeitraum."""
    benutzer = Benutzer.objects.get()
    vertrag = neuer_vertrag()
    # Kündigung über die Oberfläche: ein Vorgang des angemeldeten Benutzers
    with vorgaenge.vorgang("von Hand", user=benutzer.pk):
        stammdaten.kuendigung_erfassen(vertrag, ZUGANG)

    seite = angemeldet.get(SEITE).content.decode()
    assert f'<option value="benutzer:{benutzer.pk}">{benutzer.email}</option>' in seite
    eigene = angemeldet.get(SEITE, {"wer": f"benutzer:{benutzer.pk}"}).content.decode()
    assert "von Hand" in eigene
    nur_laeufe = angemeldet.get(SEITE, {"wer": "lauf"}).content.decode()
    assert "von Hand" not in nur_laeufe and "Noch keine Vorgänge." in nur_laeufe

    morgen = datetime.date.today() + datetime.timedelta(days=1)
    falsch = angemeldet.get(
        SEITE, {"von": morgen.isoformat(), "bis": datetime.date.today().isoformat()}
    ).content.decode()
    assert "„bis“ liegt vor „von“." in falsch


def test_feldnamen_im_protokoll_deutsch() -> None:
    """Review 2, B14: Feldnamen auf Deutsch („Status“, „gekündigt von“), nie ``status`` oder
    Umschreibungen wie „gekuendigt“; technische Felder stehen nur in den Einzelheiten."""
    for modell in protokollierte_modelle():
        for f in modell._meta.concrete_fields:
            if vorgaenge.ist_technisch(f.attname):
                continue
            titel = vorgaenge.feld_titel(modell, f.attname)
            assert "_" not in titel, (modell, f.attname, titel)
            assert titel[:1] == titel[:1].upper(), (modell, f.attname, titel)
            assert not re.search(
                r"(?<![aeiouq])(ae|oe)(?!u)|kuend|fuer|pruef|ueber", titel.lower()
            ), (
                modell,
                f.attname,
                titel,
            )
