"""Seiten Arbeitslisten, Protokoll und Läufe (Schritt 0b, Aufgabe 7)."""

from __future__ import annotations

import uuid

import pytest
from django.test import Client
from django.urls import reverse

from apps.kern import services
from apps.kern.models import Arbeitsposten
from apps.kern.vorgaenge import vorgang
from tests.factories import MandantFabrik, ObjektFabrik

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}


def test_arbeitslisten_gruppiert_nach_art(angemeldet: Client) -> None:
    """Schritt 0b, Aufgabe 7: Alle offenen Arbeitsposten, gruppiert nach Art."""
    a1 = services.arbeitsposten_anlegen("Umsätze einordnen")
    a2 = services.arbeitsposten_anlegen("Umsätze einordnen")
    b1 = services.arbeitsposten_anlegen("Frist fällig")
    erledigt = services.arbeitsposten_erledigen(services.arbeitsposten_anlegen("Frist fällig"))

    html = angemeldet.get(reverse("kern:arbeitslisten")).content.decode()

    # Review 2, B2: Die Arten stehen links mit ihrer Zahl, die Posten nach Art sortiert.
    assert "<span>Frist fällig</span>" in html and "<span>Umsätze einordnen</span>" in html
    assert html.index(f'id="posten-{b1.pk}"') < html.index(f'id="posten-{a1.pk}"')
    assert html.index("Frist fällig") < html.index(f'id="posten-{b1.pk}"')
    assert html.index("Umsätze einordnen") < html.index(f'id="posten-{a1.pk}"')
    assert f'id="posten-{a2.pk}"' in html
    assert f'id="posten-{erledigt.pk}"' not in html


def test_aktion_per_htmx_liefert_den_posten_neu(angemeldet: Client) -> None:
    """Schritt 0b, Aufgabe 7: Aktionen per HTMX ersetzen nur den Posten. Review 2, B2: Ein
    erledigter Posten verschwindet aus der Liste (leere Zeile), die Zahlen links kommen neu,
    und eine Meldung sagt, was passiert ist."""
    posten = services.arbeitsposten_anlegen("Selbsttest")
    url = reverse("kern:arbeitsposten_aktion", args=[posten.pk, "erledigen"])

    antwort = angemeldet.post(url, **HTMX)

    assert antwort.status_code == 200
    html = antwort.content.decode()
    assert f'id="posten-{posten.pk}"' not in html
    assert html.lstrip().startswith('<nav class="posteingang-arten"')
    assert "<html" not in html
    assert "jetzt erledigt" in html
    posten.refresh_from_db()
    assert posten.status == Arbeitsposten.Status.ERLEDIGT
    assert posten.erledigt_am is not None


def test_zurueckstellen_und_wieder_oeffnen(angemeldet: Client) -> None:
    """Schritt 0b, Aufgabe 7: Zurückgestellte Posten stehen unten und lassen sich wieder öffnen."""
    posten = services.arbeitsposten_anlegen("Selbsttest")

    angemeldet.post(reverse("kern:arbeitsposten_aktion", args=[posten.pk, "zurueckstellen"]))
    posten.refresh_from_db()
    assert posten.status == Arbeitsposten.Status.ZURUECKGESTELLT
    html = angemeldet.get(reverse("kern:arbeitslisten")).content.decode()
    assert "Zurückgestellt" in html
    html = angemeldet.get(
        reverse("kern:arbeitslisten"), {"status": "zurueckgestellt"}
    ).content.decode()
    assert reverse("kern:arbeitsposten_aktion", args=[posten.pk, "wieder_oeffnen"]) in html

    angemeldet.post(
        reverse("kern:arbeitsposten_aktion", args=[posten.pk, "wieder_oeffnen"]), **HTMX
    )
    posten.refresh_from_db()
    assert posten.status == Arbeitsposten.Status.OFFEN


def test_aktion_ohne_htmx_leitet_zurueck(angemeldet: Client) -> None:
    """Schritt 0b, Aufgabe 7: Ohne JavaScript funktionieren die Knöpfe als Formular."""
    posten = services.arbeitsposten_anlegen("Selbsttest")

    antwort = angemeldet.post(reverse("kern:arbeitsposten_aktion", args=[posten.pk, "erledigen"]))

    assert antwort.status_code == 302
    assert antwort["Location"] == reverse("kern:arbeitslisten")


def test_nicht_angebotene_aktion_wird_abgelehnt(angemeldet: Client) -> None:
    """Schritt 0b, Aufgabe 7: Nur Aktionen, die der Posten anbietet, sind möglich."""
    posten = services.arbeitsposten_anlegen("Selbsttest", aktionen=["erledigen"])

    for aktion in ("zurueckstellen", "gibt_es_nicht"):
        antwort = angemeldet.post(reverse("kern:arbeitsposten_aktion", args=[posten.pk, aktion]))
        assert antwort.status_code == 400
    assert (
        angemeldet.get(
            reverse("kern:arbeitsposten_aktion", args=[posten.pk, "erledigen"])
        ).status_code
        == 405
    )
    posten.refresh_from_db()
    assert posten.status == Arbeitsposten.Status.OFFEN


def test_protokoll_filtert_nach_modell(angemeldet: Client) -> None:
    """Schritt 0b, Aufgabe 7: Das Protokoll zeigt die letzten Änderungen, filterbar nach Modell.
    Review 2, A4: als Vorgänge, deren Einzelheiten die Änderungen nennen."""
    with vorgang("Mandant angelegt"):
        mandant = MandantFabrik()
    with vorgang("Objekt angelegt"):
        objekt = ObjektFabrik(mandant=mandant)
    with vorgang("Halle umbenannt"):
        services.objekt_aendern(objekt, bezeichnung="Halle Ost")

    alle = angemeldet.get(reverse("kern:protokoll")).content.decode()
    assert "Mandant angelegt" in alle and "Halle umbenannt" in alle
    assert f"Objekt {objekt.kuerzel} geändert" in alle
    assert "Bezeichnung:" in alle and "Halle Ost" in alle

    nur_objekte = angemeldet.get(reverse("kern:protokoll"), {"modell": "kern.Objekt"})
    html = nur_objekte.content.decode()
    assert "Halle umbenannt" in html and "Objekt angelegt" in html
    assert "Mandant angelegt" not in html
    assert '<option value="kern.Objekt" selected>' in html


def test_ruecknahme_mit_fehler_zeigt_meldung(angemeldet: Client) -> None:
    """Schritt 0b, Aufgabe 7: Scheitert die Rücknahme, erklärt eine Meldung den Grund."""
    unbekannt = uuid.uuid4()
    antwort = angemeldet.post(reverse("kern:vorgang_zuruecknehmen", args=[unbekannt]), follow=True)

    assert "Nicht zurückgenommen: Diesen Vorgang gibt es nicht." in antwort.content.decode()


def test_laeufe_seite_ohne_laeufe(angemeldet: Client) -> None:
    """Schritt 0b, Aufgabe 7: Die Seite „Läufe“ zeigt auch ohne Läufe etwas Sinnvolles."""
    assert "Noch keine Läufe" in angemeldet.get(reverse("kern:laeufe")).content.decode()


@pytest.mark.parametrize(
    "seite", ["kern:startseite", "kern:arbeitslisten", "kern:protokoll", "kern:laeufe"]
)
def test_seiten_in_navigation_und_am_handy(angemeldet: Client, seite: str) -> None:
    """Schritt 0b, Aufgabe 7: Jede Seite steht in der Navigation und ist am Handy bedienbar
    (Viewport, Navigation zum Aufklappen)."""
    html = angemeldet.get(reverse(seite)).content.decode()

    assert 'name="viewport"' in html
    # Alte Seiten: Navigation zum Aufklappen; neue (base_neu): Leiste unten mit „Mehr“.
    assert 'class="nav-schalter"' in html or 'data-dialog-oeffnen="mehr"' in html
    for ziel in ("kern:arbeitslisten", "kern:protokoll", "kern:laeufe"):
        assert f'href="{reverse(ziel)}"' in html
    assert 'aria-current="page"' in html
