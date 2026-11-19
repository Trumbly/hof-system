"""Klärfall mit Karten, Suche und einem Klick (Review 2, B4) in Arbeitsliste und Seite."""

from __future__ import annotations

import datetime

import pytest
from django.test import Client
from django.urls import reverse

from apps.abgleich import posteingang, services
from apps.abgleich.models import Klaerfall, Zahlerregel
from apps.kern.models import Arbeitsposten
from apps.stammdaten.models import Vertrag
from tests.abgleich.conftest import Abgleichhof, klaerfaelle, neue_iban

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}
D = datetime.date


def _zweck_klaerfall(hof: Abgleichhof) -> tuple[Klaerfall, Vertrag, Vertrag, Zahlerregel]:
    """Wie im Testhof: Die Zahlerregel nennt Vertrag A, der Zweck die Einheit von Vertrag B."""
    a = hof.vertrag()
    b = hof.vertrag("40.00")
    iban = neue_iban()
    regel = hof.regel(iban, vertrag=a)
    nw = b.einheiten.first().nw_id  # type: ignore[union-attr]
    buchung = hof.zahlung(D(2026, 7, 2), "40.00", iban=iban, zweck=f"Miete {nw}")
    services.abgleichen()
    (fall,) = klaerfaelle(buchung)
    return fall, a, b, Zahlerregel.objects.get(regel=regel)


def test_karten_mit_vorschlag_alternative_namen_und_offenen_posten(
    hof: Abgleichhof, angemeldet: Client
) -> None:
    """Review 2, B4: Rechts der Vorschlag und bis zu zwei Alternativen als Karten mit Vertrag,
    Einheit, Mieter-Name und offenen Posten (aus soll.offen). Der Haken „Zahlerregel ändern“
    ist vorangehakt; die Karte, die von der Regel abweicht, sagt es."""
    fall, a, b, _regel = _zweck_klaerfall(hof)
    assert fall.art == Klaerfall.Art.ZWECK

    karten = posteingang.karten(fall)
    assert [k.vertrag for k in karten] == [a, b]
    assert karten[0].vorschlag and not karten[1].vorschlag
    assert karten[1].aendert_regel and not karten[0].aendert_regel
    assert karten[0].mieter == hof.mieter.anzeigename
    assert karten[1].offen and karten[1].summe_offen > 0

    seite = angemeldet.get(reverse("abgleich:klaerfall", args=[fall.pk])).content.decode()
    assert seite.count('<article class="wahl-karte') == 2
    assert hof.mieter.anzeigename in seite
    assert b.einheiten.first().nw_id in seite  # type: ignore[union-attr]
    assert 'name="zahlerregel_aendern" value="1" checked' in seite
    assert "<select" not in seite  # keine lange Auswahlliste
    assert "Anderen Vertrag suchen" in seite


def test_ein_klick_auf_die_alternative_in_der_arbeitsliste(
    hof: Abgleichhof, angemeldet: Client
) -> None:
    """Review 2, B4 (Ziel 1 Klick): In der Arbeitsliste stehen die Karten in der Zeile. Ein
    Klick auf „Zuordnen“ der Alternative ordnet zu und ändert die abweichende Zahlerregel."""
    fall, _a, b, regel = _zweck_klaerfall(hof)
    arbeit = services.klaerfall_posten(fall)
    assert arbeit is not None
    p = f"p{arbeit.pk}"

    liste = angemeldet.get(reverse("kern:arbeitslisten")).content.decode()
    assert f'data-vertrag="{b.nummer}"' in liste and f'"{p}-vertrag": "{b.pk}"' in liste

    antwort = angemeldet.post(
        reverse("kern:posten_uebernehmen", args=[arbeit.pk]),
        {f"{p}-da": "1", f"{p}-vertrag": b.pk, f"{p}-zahlerregel": "1"},
        **HTMX,
    )

    html = antwort.content.decode()
    assert f"Klärfall #{fall.pk}: Zahlung Vertrag {b.nummer} zugeordnet." in html
    assert f"Zahlerregel #{regel.pk} zeigt jetzt auf {b.nummer}." in html
    assert f'id="posten-{arbeit.pk}"' not in html
    fall.refresh_from_db()
    regel.refresh_from_db()
    assert fall.ergebnis == Klaerfall.Ergebnis.ZUGEORDNET
    assert regel.vertrag == b


def test_enter_uebernimmt_den_vorschlag(hof: Abgleichhof, angemeldet: Client) -> None:
    """Review 2, B2: Ohne Werte aus der Zeile (Enter, Mehrfachauswahl) gilt der Vorschlag;
    die Zahlerregel zeigt schon dorthin und bleibt."""
    fall, a, _b, regel = _zweck_klaerfall(hof)
    arbeit = services.klaerfall_posten(fall)
    assert arbeit is not None

    angemeldet.post(reverse("kern:posten_uebernehmen", args=[arbeit.pk]), **HTMX)

    arbeit.refresh_from_db()
    regel.refresh_from_db()
    assert arbeit.status == Arbeitsposten.Status.ERLEDIGT
    assert regel.vertrag == a


def test_anderen_vertrag_suchen_mit_namen(hof: Abgleichhof, angemeldet: Client) -> None:
    """Review 2, B4: „anderen Vertrag suchen“ findet Verträge über den Namen (HTMX) und zeigt
    sie als Karten zum Zuordnen; auf der Seite ohne Arbeitsliste als Knopf des Formulars."""
    fall, _a, _b, _regel = _zweck_klaerfall(hof)
    dritter = hof.vertrag("55.00")
    arbeit = services.klaerfall_posten(fall)
    assert arbeit is not None
    url = reverse("abgleich:vertrag_suche", args=[fall.pk])

    treffer = angemeldet.get(
        url, {"posten": arbeit.pk, f"p{arbeit.pk}-suche": hof.mieter.nachname}
    ).content.decode()
    assert dritter.nummer in treffer and hof.mieter.anzeigename in treffer
    assert reverse("kern:posten_uebernehmen", args=[arbeit.pk]) in treffer

    seite = angemeldet.get(url, {"seite": "1", "q": dritter.nummer}).content.decode()
    assert f'name="vertrag" value="{dritter.pk}"' in seite
    leer = angemeldet.get(url, {"seite": "1", "q": "gibtesnicht"}).content.decode()
    assert "Kein Vertrag zu „gibtesnicht“." in leer


def test_seite_ordnet_mit_einem_klick_zu(hof: Abgleichhof, angemeldet: Client) -> None:
    """Review 2, B4: Auf der Seite des Klärfalls ordnet ein Klick auf eine Karte zu und führt
    zurück zur Arbeitsliste, mit Meldung."""
    fall, _a, b, regel = _zweck_klaerfall(hof)

    antwort = angemeldet.post(
        reverse("abgleich:zuordnen", args=[fall.pk]),
        {"vertrag": b.pk, "zahlerregel_aendern": "1", "rest_als_guthaben": "on"},
    )

    assert antwort.status_code == 302
    regel.refresh_from_db()
    assert regel.vertrag == b
