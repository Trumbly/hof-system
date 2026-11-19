"""Review 2, B11: Belegmappe mit Vorbelegung (Objekt und aktuelles Jahr), Links auf
Buchungen und Belege und „Buchungen ohne Beleg“ als Tabelle mit „Beleg zuordnen“."""

from __future__ import annotations

import datetime
import re
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.dokumente import belegmappe, services
from apps.dokumente.models import Beleg
from tests.dokumente.conftest import Belegwelt, gueltige_buchung, rechnung_pdf
from tests.dokumente.test_belegmappe import beleg_zur_ausgabe

pytestmark = pytest.mark.django_db

SEITE = reverse("dokumente:belegmappe")


def _beleg_ohne_buchung(belegwelt: Belegwelt, datum: datetime.date, betrag: str) -> Beleg:
    beleg = services.beleg_hochladen(
        rechnung_pdf("O-1", betrag),
        datum=datum,
        lieferant=belegwelt.lieferant,
        betrag=Decimal(betrag),
        objekt=belegwelt.hof,
        kategorie=belegwelt.strom,
        leistungsjahr=datum.year,
    ).beleg
    assert beleg is not None
    return beleg


def test_vorbelegt_mit_objekt_und_aktuellem_jahr(angemeldet: Client, belegwelt: Belegwelt) -> None:
    """Review 2, B11: Ohne Angaben zeigt die Seite sofort die Mappe des Objekts mit den
    meisten Belegen im aktuellen Jahr (vorher: leeres Formular und das Vorjahr)."""
    jahr = timezone.localdate().year
    beleg_zur_ausgabe(belegwelt, datetime.date(jahr, 1, 15), "50.00", belegwelt.strom, "S-9")
    assert belegmappe.vorgabe(jahr) == belegwelt.hof

    html = angemeldet.get(SEITE).content.decode()

    assert re.search(rf'<option value="{belegwelt.hof.pk}" selected', html)
    assert f'name="jahr" value="{jahr}"' in html
    assert 'id="belegmappe"' in html


def test_ohne_beleg_als_tabelle_mit_beleg_zuordnen(
    angemeldet: Client, belegwelt: Belegwelt
) -> None:
    """Review 2, B11: „Buchungen ohne Beleg“ ist eine Tabelle (Datum, Gegenpartei, Betrag)
    mit „Beleg zuordnen“ je Zeile; der Dialog bietet Belege ohne Buchung zum Verknüpfen an.
    Buchungs- und Beleg-IDs sind Links."""
    belegt = beleg_zur_ausgabe(
        belegwelt, datetime.date(2026, 2, 10), "119.00", belegwelt.strom, "S-1"
    )
    buchung = gueltige_buchung(belegwelt.ausgabe(datetime.date(2026, 4, 2), "88.00"))
    lose = _beleg_ohne_buchung(belegwelt, datetime.date(2026, 3, 30), "88.00")
    parameter = {"objekt": belegwelt.hof.pk, "jahr": 2026}

    html = angemeldet.get(SEITE, parameter).content.decode()

    zeile = re.search(rf'<tr id="buchung-{buchung.pk}">(.*?)</tr>', html, re.S)
    assert zeile is not None
    assert reverse("kassenbuch:umsatz", args=[buchung.umsatz_id]) in zeile.group(1)
    assert "Erfundene Lieferant GmbH" in zeile.group(1)
    assert "88,00" in zeile.group(1) and "Beleg zuordnen" in zeile.group(1)
    assert "Buchungen ohne Beleg: #" not in html  # keine IDs mehr in einem Satz
    assert reverse("dokumente:beleg", args=[belegt.pk]) in html
    assert reverse("dokumente:beleg", args=[lose.pk]) in html

    dialog = angemeldet.get(
        SEITE, {**parameter, "dialog": "zuordnen", "buchung": buchung.pk}
    ).content.decode()
    assert f"Beleg zu Buchung #{buchung.pk}" in dialog
    ziel = reverse("dokumente:beleg_buchung", args=[lose.pk])
    assert ziel in dialog
    zurueck = f"{SEITE}?objekt={belegwelt.hof.pk}&jahr=2026"
    antwort = angemeldet.post(ziel, {"buchung": buchung.pk, "zurueck": zurueck})
    assert antwort["Location"] == zurueck
    lose.refresh_from_db()
    assert lose.buchung_id == buchung.pk
    assert f'<tr id="buchung-{buchung.pk}">' not in angemeldet.get(zurueck).content.decode()
