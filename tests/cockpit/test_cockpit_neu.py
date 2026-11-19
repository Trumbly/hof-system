"""Cockpit in der neuen Gestaltung (Review 2, C; Review 1, Punkte 6 und 7)."""

from __future__ import annotations

import datetime

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.cockpit import eigene_kacheln, kacheln
from apps.fristen import services as fristen
from apps.kern import services as kern
from apps.kern.models import Arbeitsposten
from apps.stammdaten import schreiben
from tests.factories import MandantFabrik, ObjektFabrik

pytestmark = pytest.mark.django_db
T = datetime.timedelta


def _kachel(html: str, name: str) -> str:
    rest = html.split(f'id="kachel-{name}"', 1)[1]
    return rest.split('<li id="kachel-', 1)[0]


def _start(client: Client) -> str:
    antwort = client.get(reverse("kern:startseite"))
    assert antwort.status_code == 200
    return antwort.content.decode()


def test_fristen_als_zwei_listen(angemeldet: Client) -> None:
    """Review 1, Punkt 6: Fristen „überfällig“ und „in den nächsten 14 Tagen“ stehen im
    Cockpit als zwei getrennte Listen."""
    heute = timezone.localdate()
    objekt = ObjektFabrik(kuerzel="HOF", mandant=MandantFabrik())
    fristen.frist_anlegen("kuendigung", heute - T(days=3), "hand", objekt)
    fristen.frist_anlegen("sonstige", heute - T(days=1), "hand", objekt)
    fristen.frist_anlegen("vertragsende", heute + T(days=5), "hand", objekt)
    fristen.frist_anlegen("sonstige", heute + T(days=40), "hand", objekt)

    kachel = _kachel(_start(angemeldet), "fristen")
    ueberfaellig = kachel.split('id="fristen-ueberfaellig"', 1)[1].split('id="fristen-bald"')[0]
    bald = kachel.split('id="fristen-bald"', 1)[1]
    assert 'Überfällig <span class="zaehler">2</span>' in ueberfaellig
    assert (heute - T(days=3)).strftime("%d.%m.%Y") in ueberfaellig
    assert (heute + T(days=5)).strftime("%d.%m.%Y") not in ueberfaellig
    assert 'Nächste 14 Tage <span class="zaehler">1</span>' in bald
    assert (heute + T(days=5)).strftime("%d.%m.%Y") in bald
    assert (heute - T(days=3)).strftime("%d.%m.%Y") not in bald
    assert (heute + T(days=40)).strftime("%d.%m.%Y") not in kachel


def test_belege_zuordnen_nicht_doppelt(angemeldet: Client) -> None:
    """Review 1, Punkt 7: „Belege zuordnen“ hat eine eigene Kachel und steht nicht noch
    einmal unter den offenen Freigaben."""
    assert "Belege zuordnen" in kacheln.eigene_arten()
    kern.arbeitsposten_anlegen("Belege zuordnen")
    kern.arbeitsposten_anlegen("Umsätze einordnen")
    html = _start(angemeldet)
    freigaben = _kachel(html, "freigaben")
    assert "Belege zuordnen" not in freigaben and "Umsätze einordnen" in freigaben
    assert html.count("Belege zuordnen") == 1  # nur die eigene Kachel


def test_freigaben_nach_dringlichkeit_fuenf_arten_rest_hinter_link(angemeldet: Client) -> None:
    """Review 1, Punkt 7: Offene Freigaben nach Dringlichkeit (überfällige zuerst, dann
    früheste Fälligkeit, dann Anzahl), die ersten fünf Arten, der Rest hinter einem Link."""
    heute = timezone.localdate()
    for _ in range(4):
        kern.arbeitsposten_anlegen("Viele ohne Datum")
    kern.arbeitsposten_anlegen("Morgen fällig", faellig_am=heute + T(days=1))
    kern.arbeitsposten_anlegen("Überfällig", faellig_am=heute - T(days=2))
    kern.arbeitsposten_anlegen("Nächste Woche", faellig_am=heute + T(days=7))
    kern.arbeitsposten_anlegen("Einer ohne Datum")
    kern.arbeitsposten_anlegen("Zwei ohne Datum")
    kern.arbeitsposten_anlegen("Zwei ohne Datum")

    kontext = eigene_kacheln.freigaben()
    assert [a["art"] for a in kontext["arten"]] == [
        "Überfällig",
        "Morgen fällig",
        "Nächste Woche",
        "Viele ohne Datum",
        "Zwei ohne Datum",
    ]
    assert [a["art"] for a in kontext["weitere"]] == ["Einer ohne Datum"]
    assert kontext["summe"] == 10

    freigaben = _kachel(_start(angemeldet), "freigaben")
    assert "Einer ohne Datum" not in freigaben
    assert "weitere 1 Art ansehen" in freigaben
    assert "1 überfällig" in freigaben
    assert freigaben.index("Überfällig") < freigaben.index("Morgen fällig")


def test_selbsttest_nicht_im_cockpit(angemeldet: Client) -> None:
    """Review 1, Punkt 7: Der Selbsttest erscheint nicht als offene Freigabe im Cockpit."""
    kern.arbeitsposten_anlegen("Selbsttest")
    kontext = eigene_kacheln.freigaben()
    assert kontext["summe"] == 0 and not kontext["arten"]
    assert "Selbsttest" not in _kachel(_start(angemeldet), "freigaben")


def test_ohne_schreiben_kein_offener_arbeitsposten(db: None) -> None:
    """Review 1, Punkt 7: „Testhof legt keine Schreiben an“ wartet auf niemanden. Der Posten
    hält es nur fest und ist sofort erledigt (Protokoll), nie offen."""
    objekt = ObjektFabrik(kuerzel="HOF", mandant=MandantFabrik())
    with schreiben.ohne_schreiben("Testhof legt keine Schreiben an"):
        posten = schreiben._erzeugen("kuendigungsbestaetigung", objekt, {})
    assert isinstance(posten, Arbeitsposten)
    posten.refresh_from_db()
    assert posten.status == Arbeitsposten.Status.ERLEDIGT
    assert not Arbeitsposten.objects.filter(status=Arbeitsposten.Status.OFFEN).exists()


def test_kennzahlen_aus_den_kacheln(angemeldet: Client) -> None:
    """Review 2, C3: Kennzahl-Kacheln oben im Cockpit, aus den Kontexten der Kacheln."""
    kern.arbeitsposten_anlegen("Umsätze einordnen")
    html = _start(angemeldet)
    reihe = html.split('class="kennzahlen-reihe"', 1)[1].split('class="cockpit-raster', 1)[0]
    for titel in ("Offene Freigaben", "Rückstand", "Fristen überfällig", "Leerstand"):
        assert titel in reihe
    assert f'href="{reverse("kern:arbeitslisten")}"' in reihe
    assert "base_neu" not in html and 'class="leiste"' in html  # neue Gestaltung
