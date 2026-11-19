"""Review 2, B10: Fristen als kompakte Tabelle in Gruppen, Eichfristen gebündelt,
„Erledigt“ und „verschieben“ direkt in der Zeile, abgelöste Bankfreigabe schließt ihre Frist."""

from __future__ import annotations

import datetime
import re

import pytest
from django.test import Client
from django.urls import reverse
from django.utils.html import strip_tags

from apps.fristen import services, views
from apps.fristen.models import Frist
from apps.kern import zeit
from testhof.aufbau import Testhof
from tests.factories import ObjektFabrik

pytestmark = pytest.mark.django_db

HEUTE = zeit.heute()
LISTE = reverse("fristen:liste")


def tage(n: int) -> datetime.date:
    return HEUTE + datetime.timedelta(days=n)


def _gruppe(html: str, schluessel: str) -> str:
    treffer = re.search(rf'<section class="karte" id="{schluessel}">(.*?)</section>', html, re.S)
    assert treffer is not None, schluessel
    return treffer.group(1)


def test_gruppen_ueberfaellig_naechste_14_tage_spaeter(angemeldet: Client) -> None:
    """Review 2, B10: gruppiert in „überfällig“, „nächste 14 Tage“ und „später“."""
    a, b, c = (ObjektFabrik() for _ in range(3))
    alt = services.frist_anlegen(Frist.Art.KUENDIGUNG, tage(-3), Frist.Quelle.VERTRAG, a)
    bald = services.frist_anlegen(Frist.Art.SONSTIGE, tage(14), Frist.Quelle.HAND, b)
    spaet = services.frist_anlegen(Frist.Art.SONSTIGE, tage(15), Frist.Quelle.HAND, c)

    html = angemeldet.get(LISTE).content.decode()

    assert f'id="frist-{alt.pk}"' in _gruppe(html, "ueberfaellig")
    assert f'id="frist-{bald.pk}"' in _gruppe(html, "bald")
    assert f'id="frist-{spaet.pk}"' in _gruppe(html, "spaeter")
    assert "Nächste 14 Tage" in html and "Später" in html and "Überfällig" in html
    assert "1 überfällig" in html


def test_eichfristen_in_einer_zeile_gebuendelt(angemeldet: Client) -> None:
    """Review 2, B10: Eichfristen stehen zu einer Zeile gebündelt („3 Eichfristen
    2027–2033“), aufklappbar; jede einzelne ist darin weiter bedienbar."""
    jahre = (2027, 2030, 2033)
    eich = [
        services.frist_anlegen(
            Frist.Art.EICHFRIST, datetime.date(j, 12, 31), Frist.Quelle.ZAEHLER, ObjektFabrik()
        )
        for j in jahre
    ]
    andere = services.frist_anlegen(
        Frist.Art.SONSTIGE, tage(200), Frist.Quelle.HAND, ObjektFabrik()
    )

    html = angemeldet.get(LISTE).content.decode()

    spaeter = _gruppe(html, "spaeter")
    assert "3 Eichfristen 2027–2033" in spaeter
    assert spaeter.count('<details class="aufklapper"') == 1
    for frist in eich:
        assert f'id="frist-{frist.pk}"' in spaeter
    assert f'id="frist-{andere.pk}"' in spaeter

    einzeln = views.gruppieren(
        views._vorbereiten(Frist.objects.filter(pk=eich[0].pk)), HEUTE, "offen"
    )
    assert isinstance(einzeln[0].zeilen[0], Frist)  # eine einzelne bleibt eine Zeile


def test_verschieben_in_der_zeile(angemeldet: Client) -> None:
    """Review 2, B10: „verschieben“ um +1 Woche, +1 Monat oder auf ein Datum direkt in der
    Zeile (HTMX): Die Zeile kommt mit dem neuen Datum zurück, eine Meldung sagt es."""
    frist = services.frist_anlegen(Frist.Art.SONSTIGE, tage(3), Frist.Quelle.HAND, ObjektFabrik())
    url = reverse("fristen:verschieben", args=[frist.pk])
    assert url in angemeldet.get(LISTE).content.decode()

    antwort = angemeldet.post(url, {"um": "woche"}, HTTP_HX_REQUEST="true")
    assert antwort.status_code == 200
    frist.refresh_from_db()
    assert frist.datum == tage(10)
    teil = antwort.content.decode()
    assert f'id="frist-{frist.pk}"' in teil and f"verschoben auf {tage(10):%d.%m.%Y}" in teil
    assert "1× verschoben" in teil

    angemeldet.post(url, {"um": "monat"}, HTTP_HX_REQUEST="true")
    frist.refresh_from_db()
    assert frist.datum == views.monat_spaeter(tage(10))

    angemeldet.post(url, {"datum": "2031-05-04"}, HTTP_HX_REQUEST="true")
    frist.refresh_from_db()
    assert frist.datum == datetime.date(2031, 5, 4)
    assert frist.verschiebungen.count() == 3

    falsch = angemeldet.post(url, {"datum": "kein Datum"}, HTTP_HX_REQUEST="true")
    assert "Bitte ein gültiges Datum wählen." in falsch.content.decode()

    ohne_htmx = angemeldet.post(url, {"um": "woche", "zurueck": f"{LISTE}?zeitraum=alle"})
    assert ohne_htmx["Location"] == f"{LISTE}?zeitraum=alle"


def test_ueberfaellige_frist_verschiebt_ab_heute() -> None:
    """Review 2, B10: Eine überfällige Frist rückt um eine Woche ab heute, nicht ab ihrem
    alten Datum (sonst bliebe sie überfällig)."""
    frist = Frist(datum=tage(-20))
    assert views.neues_datum(frist, "woche", "", HEUTE) == tage(7)
    assert views.monat_spaeter(datetime.date(2027, 1, 31)) == datetime.date(2027, 2, 28)
    assert views.monat_spaeter(datetime.date(2026, 12, 15)) == datetime.date(2027, 1, 15)


def test_erledigt_in_der_zeile_mit_meldung(angemeldet: Client) -> None:
    """Review 2, B10: „Erledigt“ ersetzt die Zeile und meldet, was erledigt ist."""
    frist = services.frist_anlegen(Frist.Art.SONSTIGE, tage(-1), Frist.Quelle.HAND, ObjektFabrik())
    antwort = angemeldet.post(reverse("fristen:erledigt", args=[frist.pk]), HTTP_HX_REQUEST="true")
    teil = antwort.content.decode()
    assert teil.lstrip().startswith("<tr")
    assert f"Frist #{frist.pk} erledigt" in strip_tags(teil) and 'hx-swap-oob="beforeend"' in teil


def test_abgeloeste_bankfreigabe_schliesst_ihre_frist(testhof: Testhof, angemeldet: Client) -> None:
    """Review 2, B10 (Frist #41 zu Bankfreigabe #4): Eine neue Freigabe erledigt die Frist
    der abgelösten; die Liste zeigt danach nur noch die Frist der neuen Freigabe. Gilt seit
    Schritt 9a (``bank.services._freigabe_frist``)."""
    from apps.bank import services as bank_services
    from apps.bank.models import Bankkonto

    konto = Bankkonto.objects.filter(freigaben__isnull=False).order_by("pk").first()
    assert konto is not None
    alte = bank_services.aktuelle_freigabe(konto)
    assert alte is not None
    (alte_frist,) = services.fristen_zu(alte, nur_offen=True)

    neue = bank_services.bankfreigabe_anlegen(
        konto, gueltig_bis=alte.gueltig_bis + datetime.timedelta(days=180)
    )

    alte_frist.refresh_from_db()
    assert alte_frist.erledigt_am is not None
    html = angemeldet.get(LISTE, {"art": Frist.Art.BANKFREIGABE}).content.decode()
    assert f'id="frist-{alte_frist.pk}"' not in html
    (neue_frist,) = services.fristen_zu(neue, nur_offen=True)
    assert f'id="frist-{neue_frist.pk}"' in html
    assert f"Bankfreigabe {konto.bezeichnung}" in html
