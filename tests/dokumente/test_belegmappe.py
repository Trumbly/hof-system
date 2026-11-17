"""Belegmappe je Objekt und Jahr (Schritt 3a, Aufgabe 5; H6.9)."""

from __future__ import annotations

import datetime
import io
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse
from pypdf import PdfReader

from apps.dokumente import belegmappe, services
from apps.dokumente.models import Beleg
from apps.kassenbuch import auswertung
from apps.kassenbuch import services as kassenbuch_services
from apps.kassenbuch.models import Buchung, Kategorie
from testhof.aufbau import Testhof
from tests.dokumente.conftest import (
    Belegwelt,
    braucht_weasyprint,
    gueltige_buchung,
    rechnung_pdf,
)

pytestmark = pytest.mark.django_db


def _kassenbuch_kosten(belegwelt: Belegwelt, jahr: int) -> Decimal:
    """Kosten des Objekts HOF im Leistungsjahr, direkt aus dem Kassenbuch."""
    summe = Buchung.objects.filter(
        objekt=belegwelt.hof, leistungsjahr=jahr, kategorie__art=Kategorie.Art.AUSGABE
    ).aggregate(s=auswertung.vorzeichen_summe())["s"]
    return -(summe or Decimal("0"))


def beleg_zur_ausgabe(
    belegwelt: Belegwelt, datum: datetime.date, betrag: str, kategorie: Kategorie, nummer: str
) -> Beleg:
    """Eine Ausgabe im Kassenbuch und ihr Beleg, aneinander gehängt."""
    buchung = gueltige_buchung(
        belegwelt.ausgabe(datum, betrag, kategorie=kategorie, zweck=f"Rechnung {nummer}")
    )
    beleg = services.beleg_hochladen(
        rechnung_pdf(nummer, betrag),
        datum=datum - datetime.timedelta(days=5),
        lieferant=belegwelt.lieferant,
        betrag=Decimal(betrag),
        objekt=belegwelt.hof,
        kategorie=kategorie,
        leistungsjahr=datum.year,
    ).beleg
    assert beleg is not None
    return services.beleg_an_buchung(beleg, buchung)


@pytest.fixture
def jahr_2026(belegwelt: Belegwelt) -> list[Beleg]:
    return [
        beleg_zur_ausgabe(belegwelt, datetime.date(2026, 2, 10), "119.00", belegwelt.strom, "S-1"),
        beleg_zur_ausgabe(belegwelt, datetime.date(2026, 5, 10), "121.50", belegwelt.strom, "S-2"),
        beleg_zur_ausgabe(
            belegwelt, datetime.date(2026, 3, 1), "480.00", belegwelt.versicherung, "V-1"
        ),
    ]


def test_summen_der_belegmappe_passen_zum_kassenbuch(
    belegwelt: Belegwelt, jahr_2026: list[Beleg]
) -> None:
    """Abnahme Schritt 3 (H6.9): Die Belegmappe für ein Objekt und ein Jahr; ihre Summen
    stimmen mit dem Kassenbuch, je Kostenart und insgesamt."""
    # Einnahmen und andere Objekte zählen nicht mit.
    einnahme = belegwelt.ausgabe(datetime.date(2026, 2, 1), "50.00", buchen=False, zweck="x")
    kassenbuch_services.umsatz_zuordnen(einnahme, kategorie=belegwelt.strom, objekt=belegwelt.halle)

    mappe = belegmappe.daten(belegwelt.hof, 2026)

    assert mappe.stimmt, mappe.hinweise()
    assert mappe.hinweise() == []
    assert mappe.summe_belege == mappe.summe_kassenbuch == _kassenbuch_kosten(belegwelt, 2026)
    assert mappe.summe_belege == Decimal("720.50")
    je_art = {k.name: (k.belege, k.kassenbuch, k.umlagefaehig) for k in mappe.kostenarten}
    assert je_art == {
        "Strom": (Decimal("240.50"), Decimal("240.50"), Decimal("120.25")),
        "Versicherungen": (Decimal("480.00"), Decimal("480.00"), Decimal("480.00")),
    }
    assert [z.beleg for z in mappe.zeilen] == [jahr_2026[0], jahr_2026[1], jahr_2026[2]]


def test_storno_hebt_im_kassenbuch_auf_und_die_mappe_zeigt_die_abweichung(
    belegwelt: Belegwelt, jahr_2026: list[Beleg]
) -> None:
    """Schritt 3a, Aufgabe 5: Abweichungen erscheinen in der Mappe als Hinweis: Buchung ohne
    Beleg, Beleg ohne Buchung, andere Summe je Kostenart."""
    ohne_beleg = gueltige_buchung(
        belegwelt.ausgabe(datetime.date(2026, 7, 1), "33.00", zweck="Rechnung O-1")
    )
    ohne_buchung = services.beleg_hochladen(
        rechnung_pdf("O-2"),
        datum=datetime.date(2026, 8, 1),
        lieferant=belegwelt.lieferant,
        betrag=Decimal("99.00"),
        objekt=belegwelt.hof,
        kategorie=belegwelt.versicherung,
        leistungsjahr=2026,
    ).beleg
    assert ohne_buchung is not None
    kassenbuch_services.umsatz_stornieren(jahr_2026[2].buchung.umsatz, neu_einordnen=False)  # type: ignore[union-attr]

    mappe = belegmappe.daten(belegwelt.hof, 2026)

    assert not mappe.stimmt
    assert mappe.buchungen_ohne_beleg == [ohne_beleg.pk]
    assert mappe.belege_ohne_buchung == [ohne_buchung.pk]
    hinweise = mappe.hinweise()
    assert "Kostenart „Strom“: Belege 240,50 €, Kassenbuch 273,50 €, Differenz -33,00 €." in (
        hinweise
    )
    assert any(h.startswith("Kostenart „Versicherungen“") for h in hinweise)
    assert f"Buchungen ohne Beleg: #{ohne_beleg.pk}." in hinweise
    assert f"Belege ohne Buchung im Kassenbuch: #{ohne_buchung.pk}." in hinweise
    assert mappe.summe_kassenbuch == _kassenbuch_kosten(belegwelt, 2026)


@braucht_weasyprint
def test_belegmappe_als_pdf_mit_inhaltsverzeichnis_und_belegen(
    angemeldet: Client, belegwelt: Belegwelt, jahr_2026: list[Beleg]
) -> None:
    """Abnahme Schritt 3: Die Belegmappe entsteht auf Knopfdruck als PDF mit
    Inhaltsverzeichnis; dahinter hängen die Belege, die Seitenzahlen stimmen."""
    seite = angemeldet.get(
        reverse("dokumente:belegmappe"), {"objekt": belegwelt.hof.pk, "jahr": 2026}
    ).content.decode()
    assert "Die Summen stimmen mit dem Kassenbuch." in seite
    knopf = f"{reverse('dokumente:belegmappe_pdf')}?objekt={belegwelt.hof.pk}&amp;jahr=2026"
    assert knopf in seite

    antwort = angemeldet.get(
        reverse("dokumente:belegmappe_pdf"), {"objekt": belegwelt.hof.pk, "jahr": 2026}
    )

    assert antwort.status_code == 200
    assert antwort["Content-Type"] == "application/pdf"
    assert 'filename="belegmappe-HOF-2026.pdf"' in antwort["Content-Disposition"]
    leser = PdfReader(io.BytesIO(antwort.content))
    vorn = leser.pages[0].extract_text()
    assert "Belegmappe HOF" in vorn and "Inhaltsverzeichnis" in vorn
    assert "Die Summen je Kostenart stimmen mit dem Kassenbuch" in vorn
    assert "720,50" in vorn
    assert len(leser.pages) == 1 + 3
    for nummer, seitenzahl in ((1, 2), (2, 3), (3, 4)):
        assert (
            f"Rechnung {['S-1', 'S-2', 'V-1'][nummer - 1]}"
            in leser.pages[seitenzahl - 1].extract_text()
        )
    titel = [o.title for o in leser.outline if not isinstance(o, list)]
    assert titel == ["Inhaltsverzeichnis", "Beleg 1", "Beleg 2", "Beleg 3"]


@braucht_weasyprint
def test_abweichung_steht_vorn_in_der_mappe(belegwelt: Belegwelt, jahr_2026: list[Beleg]) -> None:
    ohne = gueltige_buchung(belegwelt.ausgabe(datetime.date(2026, 7, 1), "33.00", zweck="O"))
    mappe = belegmappe.daten(belegwelt.hof, 2026)
    vorn = PdfReader(io.BytesIO(belegmappe.erzeugen(mappe))).pages[0].extract_text()
    assert "Abweichungen zum Kassenbuch" in vorn
    assert f"#{ohne.pk}" in vorn


def test_sammelrechnung_im_testhof_stimmt_fuer_beide_objekte(testhof: Testhof) -> None:
    """Testhof (Sonderfall Sammelrechnung für zwei Objekte): HALLE hat 2026 nur ihren Teil der
    Versicherung; Beleg und Buchung passen. Dasselbe Dokument liegt auch in der Mappe HOF."""
    mappe = belegmappe.daten(testhof.kern.objekte["HALLE"], 2026)
    assert mappe.stimmt, mappe.hinweise()
    halle, hof = testhof.belege.sammelrechnung[1], testhof.belege.sammelrechnung[0]
    assert [z.beleg for z in mappe.zeilen] == [halle]
    assert halle.dokument_id == hof.dokument_id
    assert mappe.summe_kassenbuch == halle.betrag
