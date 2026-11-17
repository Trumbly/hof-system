"""Testhof-Modul der Belege (Schritt 3a): Inhalt, erfundene PDFs, Prüfsumme gleich."""

from __future__ import annotations

import datetime
import io
import re

import pytest

from apps.dokumente import services
from apps.dokumente.models import Beleg, Dokument, Lieferant
from apps.kern.models import Arbeitsposten
from testhof import aufbau, pruefsumme
from testhof.aufbau import Testhof
from testhof.pdfs import einfaches_pdf


def test_testhof_hat_belege_zu_ausgaben(testhof: Testhof) -> None:
    """Schritt 3a: Der Testhof hat Belege zu Ausgaben mit allen Feldern, eine aufgeteilte
    Sammelrechnung an ihren Buchungen, einen offenen Beleg aus dem Postfach und eine Mahnung."""
    daten = testhof.belege
    assert len(daten.belege) >= 5
    for beleg in daten.belege:
        assert beleg.status == Beleg.Status.BESTAETIGT
        assert beleg.objekt == testhof.kern.objekte["HOF"]
        assert (beleg.dokument.datum.year, beleg.leistungsjahr) == (2026, 2026)
        assert beleg.umlagefaehiger_anteil is not None
    assert {b.buchung_id is not None for b in daten.sammelrechnung} == {True}
    assert len({b.dokument_id for b in daten.sammelrechnung}) == 1

    assert daten.offen.status == Beleg.Status.OFFEN
    assert daten.offen.lieferant is not None and daten.offen.objekt is not None
    assert Arbeitsposten.objects.filter(
        art=services.ART_BELEGE_ZUORDNEN, bezug_id=daten.offen.pk, status="offen"
    ).exists()
    assert daten.mahnung.art == "mahnung_lieferant"
    assert not daten.mahnung.belege.exists()


def test_testhof_hat_eine_offene_eingangsrechnung(testhof: Testhof) -> None:
    """Schritt 3b: Eine offene Eingangsrechnung mit Posten „Rechnung zahlen“ und Frist; der
    Lauf kassenbuch_einordnen findet keine passende Abbuchung (Betrag über allen Ausgaben)."""
    from django.core.management import call_command

    from apps.dokumente.models import Eingangsrechnung
    from apps.fristen.models import Frist

    rechnung = testhof.belege.rechnung
    assert rechnung.status == Eingangsrechnung.Status.OFFEN
    assert rechnung.faellig_am == aufbau.STICHTAG + datetime.timedelta(days=14)
    assert Arbeitsposten.objects.filter(
        art=services.ART_RECHNUNG_ZAHLEN, bezug_id=rechnung.pk, status="offen"
    ).exists()
    frist = Frist.objects.get(quelle=Frist.Quelle.EINGANGSRECHNUNG)
    assert (frist.datum, frist.vorlauf_tage) == (rechnung.faellig_am, 3)

    call_command("kassenbuch_einordnen", stdout=io.StringIO())
    rechnung.refresh_from_db()
    assert rechnung.status == Eingangsrechnung.Status.OFFEN
    assert not Arbeitsposten.objects.filter(art=services.ART_RECHNUNG_ZUORDNEN).exists()


def test_lieferanten_und_pdfs_sind_erfunden(testhof: Testhof) -> None:
    """KI-Grenze: nur erfundene Kontaktdaten (.invalid), PDFs ohne echte Inhalte."""
    for lieferant in Lieferant.objects.all():
        assert lieferant.email.endswith(".invalid")
    for dokument in Dokument.objects.all():
        with dokument.datei.open("rb") as datei:
            inhalt = datei.read()
        assert inhalt.startswith(b"%PDF-1.4")
        assert b"erfunden" in inhalt
        assert re.fullmatch(
            rf"ablage/\d{{4}}/{dokument.art}_\d{{4}}-\d\d-\d\d_\d+\.pdf", dokument.datei.name
        )


def test_einfaches_pdf_ist_deterministisch_und_lesbar() -> None:
    from io import BytesIO

    from pypdf import PdfReader

    eins = einfaches_pdf(["Rechnung (Größe)", "Betrag 12,00 EUR"])
    assert eins == einfaches_pdf(["Rechnung (Größe)", "Betrag 12,00 EUR"])
    text = PdfReader(BytesIO(eins)).pages[0].extract_text()
    assert "Rechnung (Größe)" in text


@pytest.mark.django_db
def test_pruefsumme_bleibt_mit_belegen_gleich() -> None:
    """Schritt 3a: Die Prüfsumme des Testhofs bleibt bei zwei Läufen gleich, auch mit
    Dateinamen aus Art, Datum und ID."""
    aufbau.aufbauen(seed=42)
    erste = pruefsumme.pruefsumme()
    assert Dokument.objects.exists()
    aufbau.leeren()
    assert not Dokument.objects.exists()
    aufbau.aufbauen(seed=42)
    assert pruefsumme.pruefsumme() == erste
