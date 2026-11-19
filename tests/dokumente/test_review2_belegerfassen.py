"""Review 2, B9: Beleg erfassen mit „prüfen und ein Klick“.

Datum, Betrag, Rechnungsnummer und Lieferant kommen aus der Textschicht des PDFs (pypdf,
keine KI), Kostenart und Objekt vom letzten Beleg des Lieferanten, die passende Abbuchung
steht daneben, und nach dem Bestätigen öffnet sich der nächste offene Beleg. Alles mit
erfundenen Daten (Testhof, ``testhof.pdfs``).
"""

from __future__ import annotations

import datetime
import re
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils.html import strip_tags

from apps.bank import fingerabdruck
from apps.bank.models import Bankumsatz
from apps.dokumente import belegseite, services, textschicht
from apps.dokumente.models import Beleg, Lieferant
from apps.kassenbuch import services as kassenbuch_services
from apps.kassenbuch.models import Buchung
from apps.kern import zahlen
from testhof.aufbau import Testhof
from testhof.pdfs import einfaches_pdf, rechnung_pdf


def _wert(html: str, name: str) -> str | None:
    feld = re.search(rf'<input[^>]*name="{name}"[^>]*>', html)
    assert feld is not None, name
    wert = re.search(r'value="([^"]*)"', feld.group(0))
    return wert.group(1) if wert else None


def _gewaehlt(html: str, name: str) -> str | None:
    auswahl = re.search(rf'<select name="{name}".*?</select>', html, re.S)
    assert auswahl is not None, name
    treffer = re.search(r'<option value="(\d*)" selected', auswahl.group(0))
    return treffer.group(1) if treffer else None


# --- Textschicht --------------------------------------------------------------------


def test_textschicht_liest_die_rechnung_des_testhofs() -> None:
    """Review 2, B9: Die erfundenen Rechnungen des Testhofs tragen eine Textschicht mit
    Rechnungs-Nr., Rechnungsdatum, Gesamtbetrag (mit Komma) und IBAN; die Erkennung liest
    alles und findet den Lieferanten über die IBAN."""
    lieferant = Lieferant(pk=7, name="Beispiel Dienste (erfunden)", iban="DE02120300000000202051")
    andere = Lieferant(pk=8, name="Anderer Lieferant (erfunden)", iban="DE89370400440532013000")
    pdf = rechnung_pdf(
        lieferant=lieferant.name,
        nummer="4779",
        datum=datetime.date(2026, 10, 21),
        betrag=Decimal("1627.34"),
        leistung="Winterdienst",
        iban=lieferant.iban,
    )
    angaben = textschicht.angaben_aus_pdf(pdf, [andere, lieferant])
    assert angaben.datum == datetime.date(2026, 10, 21)
    assert angaben.betrag == Decimal("1627.34")
    assert angaben.rechnungsnummer == "4779"
    assert angaben.lieferant is lieferant
    assert angaben.gefunden == ["Lieferant", "Datum", "Betrag", "Rechnungsnummer"]


def test_muster_deutscher_rechnungen() -> None:
    """Review 2, B9: „Rechnungsnummer“, „Rechnungs-Nr.“, „Datum“, „Gesamtbetrag“, „Summe“,
    Beträge mit Komma, Datum TT.MM.JJJJ. Der Gesamtbetrag geht vor Zwischensummen."""
    text = "\n".join(
        [
            "Muster Handwerk (erfunden)",
            "Rechnungsnummer: RE-2026/117",
            "Datum: 05.11.2026",
            "Lieferdatum 01.11.2026",
            "Summe netto 100,00 €",
            "Gesamtbetrag (brutto): 1.190,00 EUR",
        ]
    )
    angaben = textschicht.angaben_aus_text(text)
    assert angaben.rechnungsnummer == "RE-2026/117"
    assert angaben.datum == datetime.date(2026, 11, 5)
    assert angaben.betrag == Decimal("1190.00")
    assert angaben.lieferant is None
    assert angaben.lieferant_name == "Muster Handwerk (erfunden)"

    ohne_titel = textschicht.angaben_aus_text("Rechnung Nr. 88\n12.03.2026\nSumme 45,50")
    assert (ohne_titel.rechnungsnummer, ohne_titel.datum, ohne_titel.betrag) == (
        "88",
        datetime.date(2026, 3, 12),
        Decimal("45.50"),
    )
    assert textschicht.angaben_aus_text("Re.-Nr. A-1 Betrag: 7.50").betrag == Decimal("7.50")


def test_lieferant_ueber_mail_und_namen() -> None:
    """Review 2, B9: Ohne IBAN erkennt die E-Mail den Lieferanten, sonst der Name."""
    mit_mail = Lieferant(pk=1, name="Post Lieferant", email="rechnung@lieferant.testhof.invalid")
    mit_name = Lieferant(pk=2, name="Hausmeisterei Beispiel")
    alle = [mit_mail, mit_name]
    assert (
        textschicht.lieferant_finden("Kontakt: rechnung@lieferant.testhof.invalid", alle)
        is mit_mail
    )
    assert textschicht.lieferant_finden("HAUSMEISTEREI BEISPIEL GmbH", alle) is mit_name
    assert textschicht.lieferant_finden("Ganz jemand anderes", alle) is None


def test_ohne_textschicht_keine_angaben() -> None:
    """Review 2, B9: Ein Foto (kein Text) oder eine kaputte Datei geben leere Angaben,
    keinen Fehler."""
    assert textschicht.angaben_aus_pdf(b"%PDF-1.4 kaputt", []).leer
    assert textschicht.angaben_aus_pdf(b"\xff\xd8\xff", []).leer
    assert textschicht.angaben_aus_pdf(einfaches_pdf(["Hallo"]), []).betrag is None


# --- Seite des Belegs ---------------------------------------------------------------


def _offener_beleg(testhof: Testhof) -> Beleg:
    return Beleg.objects.get(pk=testhof.belege.offen.pk)


def test_offener_beleg_ist_aus_dem_pdf_vorausgefuellt(testhof: Testhof, angemeldet: Client) -> None:
    """Review 2, B9: Der offene Beleg aus dem Postfach zeigt Datum, Betrag und
    Rechnungsnummer aus dem PDF, Lieferant, Objekt und Kostenart aus früheren Belegen; das
    PDF steht groß in der geteilten Ansicht."""
    beleg = _offener_beleg(testhof)
    assert beleg.betrag is None and not beleg.rechnungsnummer
    angaben = textschicht.angaben_aus_dokument(beleg.dokument)
    assert angaben.betrag is not None and angaben.rechnungsnummer

    html = angemeldet.get(reverse("dokumente:beleg", args=[beleg.pk])).content.decode()
    assert 'class="geteilt"' in html and 'class="pdf-rahmen"' in html
    assert _wert(html, "betrag") == f"{angaben.betrag:.2f}".replace(".", ",")
    assert _wert(html, "rechnungsnummer") == angaben.rechnungsnummer
    assert _wert(html, "datum") == f"{angaben.datum:%Y-%m-%d}"
    assert _gewaehlt(html, "lieferant") == str(beleg.lieferant_id)
    assert _gewaehlt(html, "kategorie") == str(beleg.kategorie_id)
    assert _gewaehlt(html, "objekt") == str(beleg.objekt_id)
    assert "Aus dem PDF gelesen: Datum, Betrag, Rechnungsnummer" in html


def _abbuchung_ohne_beleg(testhof: Testhof) -> Buchung:
    """Eine Ausgabe ohne Beleg: „Material Baumarkt“ hat im Testhof keinen Beleg und keine
    Regel; hier wird sie von Hand gebucht."""
    umsatz = (
        kassenbuch_services.nicht_gebuchte_umsaetze()
        .filter(richtung=Bankumsatz.Richtung.AUSGANG, verwendungszweck__startswith="Material")
        .order_by("-buchungsdatum", "pk")
        .first()
    )
    assert umsatz is not None
    (buchung,) = kassenbuch_services.umsatz_zuordnen(
        umsatz,
        kategorie=testhof.kassenbuch.kategorien["Material"],
        objekt=testhof.kern.objekte["HOF"],
    )
    return buchung


def _hochladen(angemeldet: Client, pdf: bytes, name: str = "rechnung.pdf") -> Beleg:
    antwort = angemeldet.post(
        reverse("dokumente:beleg_neu"),
        {"datei": SimpleUploadedFile(name, pdf, content_type="application/pdf")},
    )
    assert antwort.status_code == 302, antwort.content.decode()[:500]
    treffer = re.search(r"/belege/(\d+)/$", antwort["Location"])
    assert treffer is not None, antwort["Location"]
    return Beleg.objects.get(pk=int(treffer.group(1)))


def test_hochladen_genuegt_die_datei_und_die_abbuchung_steht_daneben(
    testhof: Testhof, angemeldet: Client
) -> None:
    """Review 2, B9: „Beleg hochladen“ braucht nur die Datei. Der Beleg entsteht offen, der
    Lieferant ist über die IBAN erkannt, und die passende Abbuchung steht vorgewählt neben
    dem Formular. „Bestätigen und verknüpfen“ bestätigt und hängt den Beleg an die Buchung
    (ein Klick)."""
    buchung = _abbuchung_ohne_beleg(testhof)
    lieferant = Lieferant.objects.filter(
        iban=fingerabdruck.iban_normal(buchung.umsatz.gegen_iban)
    ).first() or services.lieferant_anlegen("Abbuchungstest", iban=buchung.umsatz.gegen_iban)
    pdf = rechnung_pdf(
        lieferant=lieferant.name,
        nummer="AB-1",
        datum=buchung.datum - datetime.timedelta(days=3),
        betrag=buchung.betrag,
        iban=lieferant.iban,
    )
    beleg = _hochladen(angemeldet, pdf)
    assert beleg.status == Beleg.Status.OFFEN
    assert beleg.lieferant == lieferant
    assert beleg.dokument.datum == buchung.datum - datetime.timedelta(days=3)

    url = reverse("dokumente:beleg", args=[beleg.pk])
    html = angemeldet.get(url).content.decode()
    assert re.search(rf'name="buchung" value="{buchung.pk}" checked', html)
    assert "Bestätigen und verknüpfen" in html

    daten = {
        "lieferant": lieferant.pk,
        "datum": _wert(html, "datum"),
        "betrag": _wert(html, "betrag"),
        "objekt": buchung.objekt_id,
        "kategorie": buchung.kategorie_id,
        "leistungsjahr": _wert(html, "leistungsjahr"),
        "rechnungsnummer": _wert(html, "rechnungsnummer"),
        "buchung": buchung.pk,
    }
    antwort = angemeldet.post(url, daten)
    assert antwort.status_code == 302
    beleg.refresh_from_db()
    assert beleg.status == Beleg.Status.BESTAETIGT
    assert beleg.buchung_id == buchung.pk
    assert beleg.rechnungsnummer == "AB-1"


def test_nach_dem_bestaetigen_oeffnet_sich_der_naechste_offene_beleg(
    testhof: Testhof, angemeldet: Client
) -> None:
    """Review 2, B9: Nach dem Bestätigen geht es zum nächsten offenen Beleg; die Meldung
    sagt, was passiert ist, und verlinkt auf den bestätigten Beleg."""
    erster = _offener_beleg(testhof)
    zweiter = _hochladen(
        angemeldet,
        rechnung_pdf(
            lieferant="Neuer Lieferant Beispiel",
            nummer="N-7",
            datum=erster.dokument.datum + datetime.timedelta(days=5),
            betrag=Decimal("86.40"),
        ),
    )
    assert belegseite.naechster_offener(erster) == zweiter

    url = reverse("dokumente:beleg", args=[erster.pk])
    html = angemeldet.get(url).content.decode()
    daten = {
        "lieferant": erster.lieferant_id,
        "datum": _wert(html, "datum"),
        "betrag": _wert(html, "betrag"),
        "objekt": erster.objekt_id,
        "kategorie": erster.kategorie_id,
        "leistungsjahr": _wert(html, "leistungsjahr"),
        "rechnungsnummer": _wert(html, "rechnungsnummer"),
        "buchung": "",
    }
    antwort = angemeldet.post(url, daten, follow=True)
    assert antwort.redirect_chain[-1][0] == reverse("dokumente:beleg", args=[zweiter.pk])
    seite = antwort.content.decode()
    assert f"Beleg #{erster.pk} bestätigt" in strip_tags(seite)
    assert f'href="{url}"' in seite
    # Der neue Lieferant ist als Vorschlag aus der ersten Zeile eingetragen.
    assert _wert(seite, "lieferant_neu") == "Neuer Lieferant Beispiel"
    assert _wert(seite, "betrag") == "86,40"


def test_ablage_zeigt_lieferant_betrag_und_betreff(testhof: Testhof, angemeldet: Client) -> None:
    """Review 2, B11: Die Liste der Dokumente nennt Lieferant, Betrag und Betreff statt nur
    „#26“; „Beleg hochladen“ ist die eine Hauptaktion (sendet sofort nach der Wahl)."""
    beleg = Beleg.objects.filter(status=Beleg.Status.BESTAETIGT, rechnungsnummer__gt="").first()
    if beleg is None:
        beleg = testhof.belege.rechnung.beleg
    html = angemeldet.get(reverse("dokumente:ablage")).content.decode()
    assert beleg.lieferant is not None and beleg.betrag is not None
    assert beleg.lieferant.name in html
    assert f"Rechnung {beleg.rechnungsnummer}" in html
    assert zahlen.euro(beleg.betrag) in html
    assert "data-sofort-senden" in html
    assert html.count(">Beleg hochladen<") <= 1
