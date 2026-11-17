"""Standardablauf 6: Eingangsrechnung und Beleg (Prozessliste), durchgehend mit dem Testhof.

Auslöser: Eine Rechnung trifft ein, als Mail. Ergebnis: eine bezahlte Buchung mit Beleg.

1. Postfach: Die Rechnung liegt am selben Tag in der Ablage (Lauf belege_postfach).
2. System: prüft auf Dubletten und schlägt Objekt, Kostenart und Leistungsjahr aus früheren
   Belegen des Lieferanten vor.
3. Du: bestätigst die Felder und nennst den umlagefähigen Anteil (Sammelrechnungen:
   tests/dokumente/test_eingangsrechnungen.py).
4. System: legt die Rechnung mit Betrag und Fälligkeit als offenen Posten an und meldet sie
   als Aufgabe (``kern.aufgaben.melden``). Drei Tage vor Fälligkeit erinnert es dich.
5. Du: gibst die Zahlung in Vivid frei; der Link in der Aufgabe führt zum Beleg.
6. System: erkennt die Abbuchung beim nächsten Bankabruf, schließt den offenen Posten und die
   Aufgabe und hängt den Beleg an die Buchung.
7. System: führt den Wert im Kassenbuch und, wenn umlagefähig, in der Belegmappe (H6.9).
"""

from __future__ import annotations

import datetime
import io
from decimal import Decimal
from email.utils import format_datetime

import pytest
from django.core import mail
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.bank import services as bank_services
from apps.dokumente import belegmappe, services
from apps.dokumente.models import Beleg, Dokument, Eingangsrechnung
from apps.dokumente.postfach import FakePostfach
from apps.fristen import services as fristen_services
from apps.fristen.models import Frist
from apps.kassenbuch import services as kassenbuch_services
from apps.kern import aufgaben
from apps.kern.models import Arbeitsposten
from testhof.aufbau import Testhof
from testhof.pdfs import einfaches_pdf
from tests.dokumente.conftest import rechnungsmail


@pytest.fixture(autouse=True)
def _postfach_leeren() -> None:
    FakePostfach.zuruecksetzen()


def _lauf(name: str, *argumente: str) -> None:
    call_command(name, *argumente, stdout=io.StringIO())


def test_standardablauf_6_eingangsrechnung_und_beleg(
    testhof: Testhof, angemeldet: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Standardablauf 6 und Abnahme Schritt 3: Eine Rechnung aus dem Rechnungspostfach liegt
    am selben Tag in der Ablage und hängt nach der Zahlung an ihrer Buchung. Die
    Eingangsrechnung erzeugt Arbeitsposten und Erinnerung; nach der Abbuchung schließt sie
    sich."""
    gemeldet: list[tuple[str, str]] = []
    monkeypatch.setattr(aufgaben, "melden", lambda p: gemeldet.append((p.art, p.status)))
    heute = timezone.localdate()
    faellig = heute + datetime.timedelta(days=14)
    strom = testhof.kassenbuch.kategorien["Strom"]
    frueher = next(b for b in testhof.belege.belege if b.kategorie == strom)
    lieferant = frueher.lieferant
    assert lieferant is not None
    pdf = einfaches_pdf([lieferant.name, "Rechnung (erfunden)", "Nr. SW-2026-11", "Betrag 187,40"])

    # 1. Die Rechnung kommt ins Postfach und liegt am selben Tag in der Ablage.
    FakePostfach.einwerfen(
        rechnungsmail(pdf, absender=lieferant.email, datum=format_datetime(timezone.now()))
    )
    _lauf("belege_postfach")
    dokument = Dokument.objects.get(
        herkunft=Dokument.Herkunft.POSTFACH, eingangskennung__startswith="<r1@"
    )
    assert timezone.localdate(dokument.eingegangen_am) == heute == dokument.datum
    assert (
        reverse("dokumente:dokument", args=[dokument.pk])
        in angemeldet.get(reverse("dokumente:ablage")).content.decode()
    )

    # 2. Dubletten: Dieselbe Rechnung noch einmal weitergeleitet legt nichts Neues an.
    FakePostfach.einwerfen(rechnungsmail(pdf, absender=lieferant.email, message_id="<d@x.invalid>"))
    _lauf("belege_postfach")
    assert Dokument.objects.filter(sha256=dokument.sha256).count() == 1
    beleg = Beleg.objects.get(dokument=dokument)
    assert beleg.lieferant == lieferant
    assert (beleg.objekt, beleg.kategorie, beleg.leistungsjahr) == (
        frueher.objekt,
        strom,
        heute.year,
    )

    # 3. Du bestätigst die Felder über den Posten „Belege zuordnen“, mit Fälligkeit.
    posten = Arbeitsposten.objects.get(
        art=services.ART_BELEGE_ZUORDNEN, bezug_id=beleg.pk, status="offen"
    )
    seite = angemeldet.post(
        reverse("kern:arbeitsposten_aktion", args=[posten.pk, "beleg_zuordnen"])
    )["Location"]
    antwort = angemeldet.post(
        seite,
        {
            "lieferant": lieferant.pk,
            "datum": heute.isoformat(),
            "betrag": "187,40",
            "objekt": beleg.objekt.pk,  # type: ignore[union-attr]
            "kategorie": strom.pk,
            "leistungsjahr": str(heute.year),
            "umlagefaehiger_anteil": "50",
            "faellig_am": faellig.isoformat(),
            "rechnungsnummer": "SW-2026-11",
        },
    )
    assert antwort.status_code == 302, antwort.content.decode()[:800]
    beleg.refresh_from_db()
    assert beleg.status == Beleg.Status.BESTAETIGT

    # 4. Offener Posten mit Aufgabe im Hof-Backlog, Erinnerung drei Tage vorher.
    rechnung = Eingangsrechnung.objects.get(beleg=beleg)
    assert (rechnung.betrag, rechnung.faellig_am, rechnung.status) == (
        Decimal("187.40"),
        faellig,
        "offen",
    )
    zahlen = Arbeitsposten.objects.get(art=services.ART_RECHNUNG_ZAHLEN, bezug_id=rechnung.pk)
    assert (services.ART_RECHNUNG_ZAHLEN, "offen") in gemeldet
    _lauf("fristen", "--stichtag", (faellig - datetime.timedelta(days=4)).isoformat())
    frist = Frist.objects.get(quelle=Frist.Quelle.EINGANGSRECHNUNG, bezug_id=rechnung.pk)
    assert not Arbeitsposten.objects.filter(
        art=fristen_services.ART_FRIST_FAELLIG, bezug_id=frist.pk
    ).exists()
    _lauf("fristen", "--stichtag", (faellig - datetime.timedelta(days=3)).isoformat())
    erinnerung = Arbeitsposten.objects.get(
        art=fristen_services.ART_FRIST_FAELLIG, bezug_id=frist.pk, status="offen"
    )
    _lauf("benachrichtigen")
    assert mail.outbox, "keine Benachrichtigung"
    text = str(mail.outbox[-1].body)
    assert "Frist fällig" in text and "Rechnung zahlen" in text
    assert lieferant.name not in text and "187" not in text

    # 5. Der Link in der Aufgabe führt zum Beleg.
    liste = angemeldet.get(reverse("kern:arbeitslisten")).content.decode()
    assert f'href="{reverse("dokumente:beleg", args=[beleg.pk])}">Zum Beleg</a>' in liste

    # 6. Beim nächsten Bankabruf kommt die Abbuchung; das Kassenbuch erkennt sie.
    vivid = testhof.bank.konten["vivid"]
    bank_services.umsaetze_uebernehmen(
        vivid,
        [
            bank_services.UmsatzDaten(
                buchungsdatum=faellig - datetime.timedelta(days=1),
                betrag=Decimal("187.40"),
                richtung="ausgang",
                gegenpartei=lieferant.name,
                gegen_iban=lieferant.iban,
                verwendungszweck="Stromabschlag Rechnung SW-2026-11",
            )
        ],
    )
    _lauf("kassenbuch_einordnen")

    rechnung.refresh_from_db()
    beleg.refresh_from_db()
    assert rechnung.status == Eingangsrechnung.Status.BEZAHLT
    buchung = rechnung.bezahlt_durch
    assert buchung is not None and beleg.buchung == buchung
    assert (buchung.kategorie, buchung.betrag) == (strom, Decimal("187.40"))
    for eintrag in (zahlen, erinnerung):
        eintrag.refresh_from_db()
        assert eintrag.status == Arbeitsposten.Status.ERLEDIGT
    frist.refresh_from_db()
    assert frist.erledigt_am is not None
    assert (services.ART_RECHNUNG_ZAHLEN, "erledigt") in gemeldet
    assert dokument in services.dokumente_zu(buchung)

    # 7. Der Wert steht im Kassenbuch und mit seinem umlagefähigen Anteil in der Belegmappe.
    assert (
        kassenbuch_services.buchungen_ohne_beleg(
            kassenbuch_services.gueltige_buchungen().filter(pk=buchung.pk)
        )
        == []
    )
    mappe = belegmappe.daten(buchung.objekt, heute.year)
    zeile = next(z for z in mappe.zeilen if z.beleg == beleg)
    assert zeile.umlagefaehig == Decimal("93.70")
    assert beleg.pk not in mappe.belege_ohne_buchung
