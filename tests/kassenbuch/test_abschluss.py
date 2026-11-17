"""Monatsabschluss mit Sperre (H6.6) und Monatsbericht je Objekt (H10.2)."""

from __future__ import annotations

import datetime
import io
from dataclasses import dataclass
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.db import DatabaseError, transaction
from pghistory.models import Events

from apps.bank import services as bank_services
from apps.bank.models import Bankumsatz, Kontostand
from apps.kassenbuch import auswertung, pdf, services
from apps.kassenbuch.models import Buchung, Monatsabschluss, Monatsbericht
from apps.kern import services as kern_services
from apps.kern.models import Arbeitsposten, Lauf
from tests.kassenbuch.conftest import KleinerHof, ganz, umsatz

JAN = datetime.date(2026, 1, 1)
FEB = datetime.date(2026, 2, 1)
DEZ_ENDE = datetime.date(2025, 12, 31)
JAN_ENDE = datetime.date(2026, 1, 31)


@dataclass
class Januar(KleinerHof):
    miete_jan: Bankumsatz
    strom_jan: Bankumsatz


@pytest.fixture
def januar(klein: KleinerHof) -> Januar:
    """Januar 2026: Miete +250, Strom −80; Kontostand 31.12. 1000, 31.01. 1170."""
    miete = umsatz(klein.konto, datetime.date(2026, 1, 5), "250.00")
    strom = umsatz(klein.konto, datetime.date(2026, 1, 20), "80.00", eingang=False, zweck="Strom")
    bank_services.kontostand_erfassen(klein.konto, DEZ_ENDE, Decimal("1000.00"))
    bank_services.kontostand_erfassen(klein.konto, JAN_ENDE, Decimal("1170.00"))
    return Januar(**vars(klein), miete_jan=miete, strom_jan=strom)


@pytest.fixture
def fake_pdf(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """PDF ohne WeasyPrint (läuft auch unter macOS ohne Pango); merkt sich das HTML."""
    gesetzt: list[str] = []

    def setzen(html: str) -> bytes:
        gesetzt.append(html)
        return b"%PDF-1.7 Testbericht"

    monkeypatch.setattr(pdf, "html_zu_pdf", setzen)
    return gesetzt


def test_luecken_werden_arbeitsposten(januar: Januar) -> None:
    """H6.6: Der Abschluss prüft, dass alle Umsätze eingeordnet sind; sonst Arbeitsposten
    (je Monat und Mandant höchstens einer), der Monat bleibt offen."""
    ganz(januar, januar.miete_jan)

    ergebnis = services.monat_abschliessen(januar.gbr, JAN)

    assert not ergebnis.abschluss.abgeschlossen
    assert ergebnis.pruefung is not None
    assert ergebnis.pruefung.offene_umsaetze == [januar.strom_jan.pk]
    assert ergebnis.posten is not None
    assert ergebnis.posten.art == services.ART_ABSCHLUSS
    assert f"#{januar.strom_jan.pk}" in ergebnis.posten.hinweis
    assert "80.00" not in ergebnis.posten.hinweis
    assert "80,00" not in ergebnis.posten.hinweis
    assert services.monat_abschliessen(januar.gbr, JAN).posten is None
    assert Arbeitsposten.objects.filter(art=services.ART_ABSCHLUSS).count() == 1


def test_saldo_muss_zur_bank_passen(januar: Januar) -> None:
    """H6.6: Der Saldo im Buch muss zum Saldo der Bank (bank.Kontostand) passen."""
    ganz(januar, januar.miete_jan)
    ganz(januar, januar.strom_jan, januar.strom)
    bank_services.kontostand_erfassen(januar.konto, JAN_ENDE, Decimal("1200.00"))

    ergebnis = services.monat_abschliessen(januar.gbr, JAN)

    assert not ergebnis.abschluss.abgeschlossen
    assert ergebnis.abschluss.saldo_bank == Decimal("1200.00")
    assert ergebnis.abschluss.saldo_buch == Decimal("1170.00")
    assert ergebnis.posten is not None and "Saldo" in ergebnis.posten.hinweis


def test_ohne_kontostand_kein_abschluss(klein: KleinerHof) -> None:
    eintrag = umsatz(klein.konto, datetime.date(2026, 1, 5), "250.00")
    ganz(klein, eintrag)
    ergebnis = services.monat_abschliessen(klein.gbr, JAN)
    assert ergebnis.pruefung is not None
    assert ergebnis.pruefung.konten_ohne_saldo == [klein.konto.pk]
    assert not ergebnis.abschluss.abgeschlossen


def test_saldo_der_bank_aus_spaeterem_kontostand(januar: Januar) -> None:
    """Fehlt der Kontostand zum Monatsende, rechnet der Abschluss vom nächsten späteren
    Kontostand mit den Umsätzen dazwischen zurück."""
    umsatz(januar.konto, datetime.date(2026, 2, 3), "30.00", zweck="Februar")
    bank_services.kontostand_erfassen(januar.konto, datetime.date(2026, 2, 5), Decimal("1200.00"))
    Kontostand.objects.filter(konto=januar.konto, datum=JAN_ENDE).delete()

    saldo = services.bank_saldo_am(januar.konto, JAN_ENDE)

    assert saldo is not None
    assert (saldo.saldo, saldo.genau) == (Decimal("1170.00"), False)
    genau = services.bank_saldo_am(januar.konto, DEZ_ENDE)
    assert genau is not None and genau.genau


def test_abgeschlossener_monat_ist_gesperrt_storno_im_offenen_monat(januar: Januar) -> None:
    """Abnahme Schritt 2: Ein abgeschlossener Monat lässt sich nicht mehr ändern; der Weg ist
    die Stornobuchung im offenen Monat."""
    (miete,) = services.umsatz_zuordnen(
        januar.miete_jan,
        kategorie=januar.miete,
        objekt=januar.hof,
    )
    ganz(januar, januar.strom_jan, januar.strom)
    ergebnis = services.monat_abschliessen(januar.gbr, JAN)
    assert ergebnis.neu_abgeschlossen and ergebnis.abschluss.abgeschlossen
    assert (ergebnis.abschluss.saldo_bank, ergebnis.abschluss.saldo_buch) == (
        Decimal("1170.00"),
        Decimal("1170.00"),
    )
    vorher = auswertung.bericht(januar.hof, JAN)

    # Anlegen im gesperrten Monat geht nicht …
    nachzuegler = umsatz(januar.konto, datetime.date(2026, 1, 28), "15.00", zweck="spät")
    with pytest.raises(services.MonatGesperrt, match="abgeschlossen"):
        services.umsatz_zuordnen(
            nachzuegler,
            kategorie=januar.miete,
            objekt=januar.hof,
            datum=datetime.date(2026, 1, 28),
        )
    # … ändern auch nicht (weder am Service vorbei noch über die Rücknahme des Abschlusses).
    miete.kategorie = januar.strom
    with pytest.raises(DatabaseError), transaction.atomic():
        miete.save()
    abschluss_ereignis = Events.objects.filter(
        pgh_obj_model="kassenbuch.Monatsabschluss", pgh_label="update"
    ).latest("pgh_id")
    with pytest.raises(kern_services.RuecknahmeNichtMoeglich, match="bleibt abgeschlossen"):
        kern_services.zuruecknehmen(abschluss_ereignis.pgh_slug)
    abschluss = Monatsabschluss.objects.get(pk=ergebnis.abschluss.pk)
    abschluss.abgeschlossen_am = None
    with pytest.raises(DatabaseError), transaction.atomic():
        abschluss.save()

    # Der Weg: Storno im ersten offenen Monat, dann neu buchen.
    (storno,) = services.umsatz_stornieren(januar.miete_jan)
    assert storno.storno_von == miete
    assert storno.datum == FEB
    (neu,) = services.umsatz_zuordnen(
        januar.miete_jan,
        kategorie=januar.miete,
        objekt=januar.halle,
    )
    assert neu.datum == FEB
    nachtrag = services.umsatz_zuordnen(nachzuegler, kategorie=januar.miete, objekt=januar.hof)
    assert nachtrag[0].datum == FEB

    # Der Januar bleibt, wie er abgeschlossen wurde.
    nachher = auswertung.bericht(januar.hof, JAN)
    assert (nachher.summe_einnahmen, nachher.summe_kosten) == (
        vorher.summe_einnahmen,
        vorher.summe_kosten,
    )
    assert Buchung.objects.filter(datum__lte=JAN_ENDE).count() == 2
    feb = auswertung.bericht(januar.hof, FEB)
    assert feb.summe_einnahmen == Decimal("15.00") - Decimal("250.00")


def test_vormonat_muss_abgeschlossen_sein(januar: Januar) -> None:
    services.monat_abschliessen(januar.gbr, JAN)  # scheitert: nichts gebucht
    ergebnis = services.monat_abschliessen(januar.gbr, FEB)
    assert ergebnis.pruefung is not None and ergebnis.pruefung.vormonat_offen
    assert "Vormonat" in ergebnis.pruefung.hinweis()


def test_lauf_schliesst_ab_und_erzeugt_berichte(januar: Januar, fake_pdf: list[str]) -> None:
    """Abnahme Schritt 2: Der Monatsbericht je Objekt entsteht nach dem Abschluss ohne
    Handarbeit, als PDF mit Objektkürzel und Monat im Namen."""
    ganz(januar, januar.miete_jan)
    ganz(januar, januar.strom_jan, januar.strom)
    ausgabe = io.StringIO()

    call_command("kassenbuch_monatsabschluss", "--bis", "2026-01", stdout=ausgabe)

    lauf = Lauf.objects.get(name="kassenbuch_monatsabschluss")
    # Ab Schritt 3: Die Stromrechnung hat keinen Beleg; das ist ein Hinweis-Posten
    # „Belege fehlen“, der Abschluss geht trotzdem (tests/dokumente/test_belegpflicht.py).
    assert (lauf.status, lauf.gebucht, lauf.arbeitsposten, lauf.fehler) == ("erfolgreich", 1, 1, 0)
    assert Monatsabschluss.objects.get(mandant=januar.gbr, monat=JAN).abgeschlossen
    berichte = {b.objekt.kuerzel: b for b in Monatsbericht.objects.filter(monat=JAN)}
    assert set(berichte) == {"HOF", "HALLE"}
    hof = berichte["HOF"]
    assert (hof.einnahmen, hof.kosten, hof.ergebnis) == (
        Decimal("250.00"),
        Decimal("80.00"),
        Decimal("170.00"),
    )
    assert hof.datei.name.endswith("monatsbericht-HOF-2026-01.pdf")
    assert hof.datei.read() == b"%PDF-1.7 Testbericht"
    assert hof.lauf == lauf
    assert any("Monatsbericht HOF" in html for html in fake_pdf)

    # Idempotent: Ein zweiter Lauf schließt nichts erneut ab.
    call_command("kassenbuch_monatsabschluss", "--bis", "2026-01", stdout=io.StringIO())
    zweiter = Lauf.objects.filter(name="kassenbuch_monatsabschluss").first()
    assert zweiter is not None and zweiter.gebucht == 0


def test_lauf_ohne_weasyprint_legt_bericht_ohne_pdf_an(
    januar: Januar, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fehlt(html: str) -> bytes:
        raise pdf.PdfNichtVerfuegbar("WeasyPrint fehlt")

    monkeypatch.setattr(pdf, "html_zu_pdf", fehlt)
    ganz(januar, januar.miete_jan)
    ganz(januar, januar.strom_jan, januar.strom)

    call_command("kassenbuch_monatsabschluss", "--bis", "2026-01", stdout=io.StringIO())

    lauf = Lauf.objects.get(name="kassenbuch_monatsabschluss")
    assert lauf.status == "mit_fehlern" and lauf.fehler == 2
    assert Monatsabschluss.objects.get(mandant=januar.gbr, monat=JAN).abgeschlossen
    assert Monatsbericht.objects.filter(monat=JAN, datei="").count() == 2


def test_lauf_mit_luecke_bleibt_offen(januar: Januar) -> None:
    call_command("kassenbuch_monatsabschluss", "--bis", "2026-02", stdout=io.StringIO())
    lauf = Lauf.objects.get(name="kassenbuch_monatsabschluss")
    assert (lauf.gebucht, lauf.arbeitsposten) == (0, 1)
    assert not Monatsabschluss.objects.filter(monat=FEB).exists()  # spätere Monate warten
    assert not Monatsbericht.objects.exists()


def test_bericht_mit_vorjahresmonat(klein: KleinerHof) -> None:
    """H10.2: Einnahmen, Kosten, Ergebnis und Vergleich zum Vorjahresmonat."""
    for datum, betrag, eingang, zweck in (
        (datetime.date(2025, 1, 5), "200.00", True, "Miete alt"),
        (datetime.date(2025, 1, 6), "50.00", False, "Strom alt"),
        (datetime.date(2026, 1, 5), "260.00", True, "Miete neu"),
        (datetime.date(2026, 1, 6), "40.00", False, "Strom neu"),
    ):
        eintrag = umsatz(klein.konto, datum, betrag, eingang=eingang, zweck=zweck)
        ganz(klein, eintrag, klein.miete if eingang else klein.strom)

    bericht = auswertung.bericht(klein.hof, JAN)

    assert (bericht.summe_einnahmen, bericht.summe_kosten, bericht.ergebnis) == (
        Decimal("260.00"),
        Decimal("40.00"),
        Decimal("220.00"),
    )
    assert (bericht.vorjahr_einnahmen, bericht.vorjahr_kosten, bericht.vorjahr_ergebnis) == (
        Decimal("200.00"),
        Decimal("50.00"),
        Decimal("150.00"),
    )
    assert bericht.einnahmen[0].differenz == Decimal("60.00")
    assert bericht.dateiname == "monatsbericht-HOF-2026-01.pdf"
    assert not bericht.abgeschlossen
