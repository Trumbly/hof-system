"""Belege im Monatsabschluss (H6.6, Standardablauf 7 Schritt 1): Buchungen ohne Beleg werden
ein Hinweis und ein Arbeitsposten „Belege fehlen“; der Abschluss geht trotzdem."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.conf import settings
from django.test import Client, override_settings
from django.urls import reverse

from apps.bank import services as bank_services
from apps.bank.models import Bankumsatz
from apps.dokumente import services
from apps.kassenbuch import services as kassenbuch_services
from apps.kern.models import Arbeitsposten
from tests.dokumente.conftest import Belegwelt, gueltige_buchung, rechnung_pdf

pytestmark = pytest.mark.django_db

JAN = datetime.date(2026, 1, 1)


@pytest.fixture
def januar(belegwelt: Belegwelt) -> dict[str, Bankumsatz]:
    """Januar 2026: Strom 80, Versicherung 300, Kontoführung 5; Saldo passt zur Bank."""
    umsaetze = {
        "strom": belegwelt.ausgabe(datetime.date(2026, 1, 10), "80.00", zweck="Strom"),
        "versicherung": belegwelt.ausgabe(
            datetime.date(2026, 1, 12),
            "300.00",
            kategorie=belegwelt.versicherung,
            zweck="Versicherung",
        ),
        "konto": belegwelt.ausgabe(
            datetime.date(2026, 1, 31),
            "5.00",
            kategorie=belegwelt.kontofuehrung,
            zweck="Entgelt",
        ),
    }
    bank_services.kontostand_erfassen(belegwelt.konto, datetime.date(2025, 12, 31), Decimal("1000"))
    bank_services.kontostand_erfassen(belegwelt.konto, datetime.date(2026, 1, 31), Decimal("615"))
    return umsaetze


def _beleg_fuer(belegwelt: Belegwelt, umsatz: Bankumsatz) -> None:
    beleg = services.beleg_hochladen(
        rechnung_pdf(str(umsatz.pk)),
        datum=umsatz.buchungsdatum,
        lieferant=belegwelt.lieferant,
        betrag=umsatz.betrag,
        objekt=belegwelt.hof,
        kategorie=gueltige_buchung(umsatz).kategorie,
        leistungsjahr=2026,
    ).beleg
    assert beleg is not None
    services.beleg_an_buchung(beleg, gueltige_buchung(umsatz))


def test_buchungen_ohne_beleg_sind_ein_hinweis_und_blockieren_nicht(
    belegwelt: Belegwelt, januar: dict[str, Bankumsatz]
) -> None:
    """Schritt 3a (H6.6): Der Abschluss prüft die Belege. Fehlt einer, erscheint ein Hinweis und
    ein Arbeitsposten „Belege fehlen“; der Monat wird trotzdem abgeschlossen."""
    _beleg_fuer(belegwelt, januar["versicherung"])

    ergebnis = kassenbuch_services.monat_abschliessen(belegwelt.gbr, JAN)

    assert ergebnis.neu_abgeschlossen
    assert ergebnis.pruefung is not None
    ohne = [gueltige_buchung(januar["strom"]).pk, gueltige_buchung(januar["konto"]).pk]
    assert ergebnis.pruefung.ohne_beleg == ohne
    assert ergebnis.pruefung.ok
    posten = ergebnis.belege_posten
    assert posten is not None and posten.art == kassenbuch_services.ART_BELEGE_FEHLEN
    assert posten.bezug == ergebnis.abschluss
    assert f"2 Buchungen ohne Beleg (#{ohne[0]}, #{ohne[1]})" in posten.hinweis
    assert "der Abschluss geht trotzdem" in posten.hinweis
    # Kein Betrag im Hinweis (80,00 €). IDs ausblenden, auch die des Mandanten: Sonst hängt
    # das Ergebnis davon ab, wie viele Mandanten frühere Tests angelegt haben (#280).
    ohne_ids = posten.hinweis
    for kennung in (*ohne, belegwelt.gbr.pk):
        ohne_ids = ohne_ids.replace(f"#{kennung}", "")
    assert "80" not in ohne_ids
    assert "ohne Beleg" in ergebnis.pruefung.hinweis()


def test_grenze_und_kategorien_aus_der_konfiguration(
    belegwelt: Belegwelt, januar: dict[str, Bankumsatz]
) -> None:
    """Schritt 3a: Ab welchem Betrag und für welche Kategorien ein Beleg nötig ist, steht in
    DOKUMENTE["BELEGPFLICHT"]."""
    regel = {"ARTEN": ["ausgabe"], "AB_BETRAG": "100.00", "OHNE_KATEGORIEN": ["Kontoführung"]}
    with override_settings(DOKUMENTE={**settings.DOKUMENTE, "BELEGPFLICHT": regel}):
        pruefung = kassenbuch_services.abschluss_pruefen(belegwelt.gbr, JAN)
        assert pruefung.ohne_beleg == [gueltige_buchung(januar["versicherung"]).pk]
        _beleg_fuer(belegwelt, januar["versicherung"])
        assert kassenbuch_services.abschluss_pruefen(belegwelt.gbr, JAN).ohne_beleg == []
    regel = {"ARTEN": [], "AB_BETRAG": "0", "OHNE_KATEGORIEN": []}
    with override_settings(DOKUMENTE={**settings.DOKUMENTE, "BELEGPFLICHT": regel}):
        assert kassenbuch_services.abschluss_pruefen(belegwelt.gbr, JAN).ohne_beleg == []


def test_beleg_nach_storno_und_neubuchung_gilt_weiter(
    belegwelt: Belegwelt, januar: dict[str, Bankumsatz]
) -> None:
    """Der Beleg hängt am Umsatz: Wird die Buchung storniert und neu gebucht, fehlt er nicht."""
    _beleg_fuer(belegwelt, januar["strom"])
    kassenbuch_services.umsatz_umbuchen(
        januar["strom"],
        [kassenbuch_services.Teil(Decimal("80.00"), belegwelt.strom, belegwelt.halle)],
    )
    pruefung = kassenbuch_services.abschluss_pruefen(belegwelt.gbr, JAN)
    assert gueltige_buchung(januar["strom"]).pk not in pruefung.ohne_beleg


def test_aktion_belege_pruefen_schliesst_den_posten(
    angemeldet: Client, belegwelt: Belegwelt, januar: dict[str, Bankumsatz]
) -> None:
    """Sind die Belege nachgereicht, schließt „Belege prüfen“ den Posten (auch im
    abgeschlossenen Monat)."""
    posten = kassenbuch_services.monat_abschliessen(belegwelt.gbr, JAN).belege_posten
    assert posten is not None
    for umsatz in januar.values():
        _beleg_fuer(belegwelt, umsatz)

    angemeldet.post(reverse("kern:arbeitsposten_aktion", args=[posten.pk, "belege_pruefen"]))

    posten.refresh_from_db()
    assert posten.status == Arbeitsposten.Status.ERLEDIGT
    assert not Arbeitsposten.objects.filter(
        art=kassenbuch_services.ART_BELEGE_FEHLEN, status="offen"
    ).exists()


def test_dokumentbezug_an_der_buchung_zaehlt_als_beleg(
    belegwelt: Belegwelt, januar: dict[str, Bankumsatz]
) -> None:
    """Ein Dokument, das direkt an der Buchung hängt (etwa ein Kontoauszug), genügt."""
    dokument = services.dokument_ablegen(
        rechnung_pdf("K"), art="sonstiges", datum=JAN, herkunft="upload"
    ).dokument
    services.dokument_bezug_setzen(dokument, gueltige_buchung(januar["konto"]))
    pruefung = kassenbuch_services.abschluss_pruefen(belegwelt.gbr, JAN)
    assert gueltige_buchung(januar["konto"]).pk not in pruefung.ohne_beleg
