"""Review 3, Punkt 3: Regel doppelt angelegt.

Ablauf aus dem Durchgang von Max: „Übernehmen“ mit „Als Regel merken“ legt eine Regel an,
danach stand die Gruppe weiter als „… ohne Regel“ da, und „Für alle anwenden“ wollte eine
zweite, gleiche Regel anlegen. Die Meldung nannte Interna (Priorität, Überschneidung).
"""

from __future__ import annotations

import datetime
import json

import pytest
from django.test import Client
from django.urls import reverse
from django.utils.html import strip_tags

from apps.kassenbuch import services
from apps.kassenbuch.models import Buchung, Kategorie, Zuordnungsregel
from apps.kern.models import Arbeitsposten
from tests.kassenbuch.conftest import KleinerHof, neue_iban, umsatz

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}
ART = services.ART_EINORDNEN


@pytest.fixture
def material(klein: KleinerHof) -> Kategorie:
    return services.kategorie_anlegen("Material", Kategorie.Art.AUSGABE)


def _baumarkt(klein: KleinerHof, monat: int, iban: str):  # type: ignore[no-untyped-def]
    return umsatz(
        klein.konto,
        datetime.date(2026, monat, 5),
        "48.90",
        eingang=False,
        iban=iban,
        name="Baumarkt Beispiel KG",
        zweck=f"Material {monat:02d}/2026",
    )


def _offene_posten() -> list[Arbeitsposten]:
    return list(Arbeitsposten.objects.filter(art=ART, status="offen").order_by("pk"))


def _uebernehmen_mit_regel(
    angemeldet: Client, posten: Arbeitsposten, kategorie: Kategorie, objekt: object, **extra: str
):  # type: ignore[no-untyped-def]
    p = f"p{posten.pk}"
    daten = {
        f"{p}-da": "1",
        f"{p}-kategorie": kategorie.pk,
        f"{p}-objekt": objekt.pk,  # type: ignore[attr-defined]
        f"{p}-regel": "1",
        **extra,
    }
    return angemeldet.post(reverse("kern:posten_uebernehmen", args=[posten.pk]), daten, **HTMX)


# --- Gleiche Regel wird verwendet ------------------------------------------------------


def test_zweite_gleiche_regel_wird_wiederverwendet(klein: KleinerHof) -> None:
    """Review 3, Punkt 3: Gibt es schon eine Regel mit gleichen Bedingungen und gleichem
    Ergebnis, wird sie verwendet statt neu angelegt. Das gilt im Service, also für jeden Weg;
    Groß- und Kleinschreibung der Texte zählt nicht."""
    iban = neue_iban()
    erste, neu = services.regel_anlegen_oder_finden(
        "Baumarkt", kategorie=klein.strom, objekt=klein.hof, gegen_iban=iban, richtung="ausgang"
    )
    zweite, zweite_neu = services.regel_anlegen_oder_finden(
        "Baumarkt nochmal",
        kategorie=klein.strom,
        objekt=klein.hof,
        gegen_iban=iban.lower(),
        richtung="ausgang",
    )
    assert neu and not zweite_neu
    assert zweite == erste
    assert (
        services.regel_anlegen(
            "Dritte", kategorie=klein.strom, objekt=klein.hof, gegen_iban=iban, richtung="ausgang"
        )
        == erste
    )
    name = services.regel_anlegen(
        "Name", kategorie=klein.strom, objekt=klein.hof, gegenpartei="Abc"
    )
    assert (
        services.regel_anlegen("Name 2", kategorie=klein.strom, objekt=klein.hof, gegenpartei="ABC")
        == name
    )
    assert Zuordnungsregel.objects.count() == 2
    # Anderes Ergebnis ist keine gleiche Regel: Das bleibt eine Überschneidung.
    with pytest.raises(services.RegelUeberschneidung) as fehler:
        services.regel_anlegen(
            "Anders", kategorie=klein.strom, objekt=klein.halle, gegen_iban=iban, richtung="ausgang"
        )
    assert fehler.value.andere == erste


def test_gleiche_regel_ueber_die_regelseite(klein: KleinerHof, angemeldet: Client) -> None:
    """Review 3, Punkt 3: Auch die Seite „Neue Regel“ legt keine zweite gleiche Regel an; sie
    führt zur vorhandenen und sagt das in einem Satz."""
    iban = neue_iban()
    regel = services.regel_anlegen(
        "Strom", kategorie=klein.strom, objekt=klein.hof, gegen_iban=iban
    )
    antwort = angemeldet.post(
        reverse("kassenbuch:regel_neu"),
        {
            "bezeichnung": "Doppelt",
            "gegen_iban": iban,
            "kategorie": klein.strom.pk,
            "objekt": klein.hof.pk,
            "mandant": klein.hof.mandant_id,
            "prioritaet": 10,
            "aktiv": "on",
        },
        follow=True,
    )
    html = strip_tags(antwort.content.decode())
    assert f"Diese Regel gibt es schon: Regel #{regel.pk}" in html
    assert Zuordnungsregel.objects.count() == 1


def test_fuer_alle_anwenden_mit_haken_verwendet_die_vorhandene_regel(
    klein: KleinerHof, material: Kategorie, angemeldet: Client
) -> None:
    """Review 3, Punkt 3 (Ablauf von Max): Nach „Übernehmen“ mit „Als Regel merken“ legt
    „Für alle anwenden“ mit gesetztem Haken keine zweite Regel an, auch wenn die Gruppe noch
    aus der alten Ansicht stammt; alle Umsätze tragen die eine Regel."""
    iban = neue_iban()
    umsaetze = [_baumarkt(klein, m, iban) for m in (1, 2, 3, 4)]
    services.umsaetze_einordnen()
    erster, *rest = _offene_posten()

    _uebernehmen_mit_regel(angemeldet, erster, material, klein.hof)
    (regel,) = Zuordnungsregel.objects.all()

    # Die Gruppe von vorher („4 Umsätze von … ohne Regel“) schickt Kategorie, Objekt, Haken.
    antwort = angemeldet.post(
        reverse("kern:posten_gruppe"),
        {
            "gruppe": "g1",
            "g1-da": "1",
            "g1-posten": [p.pk for p in rest],
            "g1-kategorie": material.pk,
            "g1-objekt": klein.hof.pk,
            "g1-regel": "1",
        },
        **HTMX,
    )

    html = antwort.content.decode()
    assert Zuordnungsregel.objects.count() == 1
    assert "überschneidet" not in html and "Priorität" not in html
    assert all(services.ist_gebucht(u) for u in umsaetze)
    assert set(Buchung.objects.filter(umsatz__in=umsaetze).values_list("regel", flat=True)) == {
        regel.pk
    }


# --- Gruppe „passen auf Regel #…“ mit „Regel anwenden“ ---------------------------------


def test_gruppe_passt_auf_regel_mit_regel_anwenden(
    klein: KleinerHof, material: Kategorie, angemeldet: Client
) -> None:
    """Review 3, Punkt 3: Nach dem Anlegen einer Regel heißt die Gruppe „N Umsätze passen auf
    Regel #…“ und hat den Knopf „Regel anwenden“ (dazu die Vorschau). Die Liste lädt nach dem
    Übernehmen neu, damit die Gruppe gleich so dasteht; „Regel anwenden“ bucht alle nach der
    Regel."""
    iban = neue_iban()
    umsaetze = [_baumarkt(klein, m, iban) for m in (1, 2, 3, 4)]
    services.umsaetze_einordnen()
    liste = angemeldet.get(reverse("kern:arbeitslisten")).content.decode()
    assert "4 Umsätze von Baumarkt Beispiel KG ohne Regel" in liste
    erster, *rest = _offene_posten()

    antwort = _uebernehmen_mit_regel(angemeldet, erster, material, klein.hof)

    (regel,) = Zuordnungsregel.objects.all()
    assert "posteingangNeu" in json.loads(antwort["HX-Trigger"])  # Liste lädt neu
    toast = antwort.content.decode()
    assert f"Regel #{regel.pk}</a> angelegt." in toast
    assert "3 weitere offene Umsätze passen darauf." in toast
    assert reverse("kassenbuch:regel_anwenden", args=[regel.pk]) in toast  # „jetzt anwenden“

    liste = angemeldet.get(reverse("kern:arbeitslisten")).content.decode()
    assert "ohne Regel" not in liste
    assert f"3 Umsätze passen auf Regel #{regel.pk}" in liste
    assert ">Regel anwenden</button>" in liste
    assert 'name="g1-regel_anwenden"' in liste
    assert reverse("kassenbuch:regel_anwenden", args=[regel.pk]) in liste  # Vorschau

    antwort = angemeldet.post(
        reverse("kern:posten_gruppe"),
        {
            "gruppe": "g1",
            "g1-da": "1",
            "g1-posten": [p.pk for p in rest],
            "g1-regel_anwenden": regel.pk,
        },
        **HTMX,
    )

    html = antwort.content.decode()
    assert "3 Umsätze nach " in html and "Material · HOF." in html
    assert all(services.ist_gebucht(u) for u in umsaetze)
    assert not _offene_posten()
    assert Zuordnungsregel.objects.count() == 1


def test_regel_anwenden_bucht_nur_wo_die_regel_allein_gewinnt(
    klein: KleinerHof, material: Kategorie
) -> None:
    """Review 3, Punkt 3: „Regel anwenden“ bucht nur offene Umsätze, bei denen die Regel
    eindeutig gewinnt; schon gebuchte bleiben, wie sie sind."""
    iban = neue_iban()
    a, b = _baumarkt(klein, 1, iban), _baumarkt(klein, 2, iban)
    regel = services.regel_anlegen(
        "Baumarkt", kategorie=material, objekt=klein.hof, gegen_iban=iban
    )
    services.umsatz_zuordnen(b, kategorie=klein.strom, objekt=klein.halle)

    assert services.regel_anwenden_auf(regel, [a, b]) == [a]
    assert Buchung.objects.get(umsatz=b).kategorie == klein.strom


# --- Keine Interna in der Meldung -----------------------------------------------------------


def test_keine_interna_in_der_meldung(
    klein: KleinerHof, material: Kategorie, angemeldet: Client
) -> None:
    """Review 3, Punkt 3: Überschneidet sich die neue Regel mit einer vorhandenen, nennt die
    Meldung keine Priorität und keine Überschneidung, sondern sagt „Für diese Gegenpartei
    gibt es schon die Regel #…“ mit Link zur Regel."""
    iban = neue_iban()
    _baumarkt(klein, 1, iban)
    services.umsaetze_einordnen()
    (posten,) = _offene_posten()
    vorhandene = services.regel_anlegen(
        "Baumarkt", kategorie=klein.strom, objekt=klein.halle, gegen_iban=iban, richtung="ausgang"
    )

    antwort = _uebernehmen_mit_regel(angemeldet, posten, material, klein.hof)

    html = antwort.content.decode()
    regel_url = reverse("kassenbuch:regel", args=[vorhandene.pk])
    assert (
        f'Für diese Gegenpartei gibt es schon die <a href="{regel_url}">Regel #{vorhandene.pk}</a>.'
        in html
    )
    assert f'href="{regel_url}">Regel ansehen →</a>' in html
    for intern in ("Priorität", "überschneidet", "Überschneidung", "passt auf beide"):
        assert intern not in html
    assert Zuordnungsregel.objects.count() == 1


# --- Zahlen im Kopf und links -----------------------------------------------------------------


def test_beide_zahlen_nach_einer_aktion_gleich(
    klein: KleinerHof, material: Kategorie, angemeldet: Client
) -> None:
    """Review 3, Punkt 3: Nach einer Aktion kommen die Zahl im Listenkopf und die Zahl der Art
    links zusammen (OOB) und stimmen überein."""
    for m in (1, 2, 3):
        _baumarkt(klein, m, neue_iban())
    services.umsaetze_einordnen()
    posten = _offene_posten()[0]
    url = reverse("kern:posten_uebernehmen", args=[posten.pk])

    p = f"p{posten.pk}"
    daten = {"art": ART, f"{p}-da": "1", f"{p}-kategorie": material.pk, f"{p}-objekt": klein.hof.pk}
    html = angemeldet.post(url, daten, **HTMX).content.decode()

    assert services.ist_gebucht(Buchung.objects.get().umsatz)
    assert '<span class="zaehler" id="posteingang-anzahl" hx-swap-oob="true">2</span>' in html
    assert f'<span>{ART}</span><span class="zaehler">2</span>' in html


def test_dialog_mit_regel_laedt_die_liste_neu(
    klein: KleinerHof, material: Kategorie, angemeldet: Client
) -> None:
    """Review 3, Punkt 3: Auch „Zuordnen“ im Dialog (Taste e) mit „Als Regel merken“ lässt die
    Liste neu laden, damit die Gruppe „passen auf Regel #…“ erscheint; Kopf und Spalte
    bekommen ihre Zahlen zusammen."""
    iban = neue_iban()
    eintrag, _zweiter = _baumarkt(klein, 1, iban), _baumarkt(klein, 2, iban)
    services.umsaetze_einordnen()

    antwort = angemeldet.post(
        reverse("kassenbuch:zuordnen", args=[eintrag.pk]),
        {
            "kategorie": material.pk,
            "objekt": klein.hof.pk,
            "leistungsjahr": 2026,
            "regel_merken": "on",
        },
        HTTP_HX_CURRENT_URL="http://testserver/arbeitslisten/",
        **HTMX,
    )

    ereignisse = json.loads(antwort["HX-Trigger"])
    assert ereignisse == {"dialogSchliessen": True, "posteingangNeu": True}
    html = antwort.content.decode()
    assert '<span class="zaehler" id="posteingang-anzahl" hx-swap-oob="true">1</span>' in html
