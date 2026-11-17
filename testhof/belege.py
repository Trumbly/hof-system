"""Testhof-Modul der Belege (Schritt 3a): Lieferanten, Belege zu Ausgaben, eine Mahnung.

Alles ist erfunden und deterministisch; die PDFs schreibt ``testhof.pdfs`` (gleiche Zeilen,
gleiche Bytes). Was entsteht:

- Je Gegenpartei der Ausgaben auf Vivid ein Lieferant (Name und IBAN aus ``testhof.bank``,
  E-Mail auf ``.invalid``).
- Für jede Ausgabe auf Vivid im Oktober 2026 (``MONAT_BELEGE``) ein bestätigter Beleg mit
  Betrag, Objekt HOF, Kostenart, Leistungsjahr und umlagefähigem Anteil. An einer Buchung
  hängen sie noch nicht: Die Umsätze bucht erst der Lauf ``kassenbuch_einordnen``.
  Ausgenommen sind Material (ohne Regel) und Kontoführung (Beleg ist der Kontoauszug).
- Die Sammelrechnung (Versicherung Januar 2026, ``testhof.kassenbuch``) als ein Dokument mit
  zwei Belegen, HOF und HALLE, je an ihrer Buchung (H6.4).
- Ein offener Beleg aus dem Rechnungspostfach mit Arbeitsposten „Belege zuordnen“; der
  Lieferant ist erkannt, Objekt, Kostenart und Leistungsjahr stehen als Vorschlag drin.
- Eine Mahnung eines Lieferanten, abgelegt am Lieferanten, ohne Buchung (H6.8).
- Eine offene Eingangsrechnung (Schritt 3b), fällig zwei Wochen nach dem Stichtag, mit
  Arbeitsposten „Rechnung zahlen“ und Frist (Zahlungsziel, drei Tage Vorlauf). Ihr Betrag
  liegt über allen Ausgaben des Testhofs, damit kein Umsatz zufällig passt.

Geschrieben wird über ``apps.dokumente.services``.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import Decimal

from apps.bank.models import Bankumsatz
from apps.dokumente import dokumentarten, services
from apps.dokumente.models import Beleg, Dokument, Dokumentbezug, Eingangsrechnung, Lieferant
from apps.fristen.models import Frist
from apps.kern.models import Arbeitsposten
from testhof import register
from testhof.aufbau import Kontext
from testhof.kassenbuch import AUSGABE_KATEGORIE
from testhof.pdfs import einfaches_pdf

NAME = "belege"

MONAT_BELEGE = (2026, 10)
#: Ausgaben ohne Beleg im Testhof
OHNE_BELEG = ("Material Baumarkt", "Kontoführung")
#: Die Rechnung liegt so viele Tage vor der Abbuchung.
RECHNUNG_VORLAUF = 10
#: Offene Eingangsrechnung: Betrag und Zahlungsziel nach dem Stichtag
OFFENE_RECHNUNG_BETRAG = Decimal("1234.56")
OFFENE_RECHNUNG_TAGE = 14


@dataclass
class BelegeDaten:
    lieferanten: dict[str, Lieferant]  # nach Gegen-IBAN
    belege: list[Beleg]  # bestätigte Belege im Monat MONAT_BELEGE, nach Datum
    sammelrechnung: list[Beleg]  # HOF, HALLE
    offen: Beleg
    mahnung: Dokument
    rechnung: Eingangsrechnung


def _art(umsatz: Bankumsatz) -> str | None:
    for art in AUSGABE_KATEGORIE:
        if umsatz.verwendungszweck.startswith(f"{art} "):
            return art
    return None


def _rechnung_pdf(lieferant: Lieferant, umsatz: Bankumsatz, datum: datetime.date) -> bytes:
    nummer = umsatz.verwendungszweck.rsplit(" ", 1)[-1]
    return einfaches_pdf(
        [
            lieferant.name,
            "Rechnung (erfunden, Testhof)",
            f"Rechnungsnummer {nummer}",
            f"Datum {datum:%d.%m.%Y}",
            f"Leistung: {umsatz.verwendungszweck.rsplit(' Rechnung', 1)[0]}",
            f"Betrag {umsatz.betrag:.2f} EUR",
        ]
    )


def _lieferanten(ausgaben: list[Bankumsatz]) -> dict[str, Lieferant]:
    lieferanten: dict[str, Lieferant] = {}
    for umsatz in ausgaben:
        if umsatz.gegen_iban in lieferanten:
            continue
        nummer = len(lieferanten) + 1
        lieferanten[umsatz.gegen_iban] = services.lieferant_anlegen(
            umsatz.gegenpartei,
            iban=umsatz.gegen_iban,
            email=f"rechnung{nummer:02d}@lieferant.testhof.invalid",
        )
    return lieferanten


def aufbauen(kontext: Kontext) -> BelegeDaten:
    kern = kontext.ablage["kern"]
    bank = kontext.ablage["bank"]
    kassenbuch = kontext.ablage["kassenbuch"]
    hof = kern.objekte["HOF"]
    vivid = bank.konten["vivid"]
    alle_ausgaben = list(
        vivid.umsaetze.filter(richtung=Bankumsatz.Richtung.AUSGANG)
        .exclude(gegen_iban=bank.entnahme_iban)
        .order_by("buchungsdatum", "pk")
    )
    ausgaben = [u for u in alle_ausgaben if _art(u) is not None]
    lieferanten = _lieferanten(ausgaben)
    jahr, monat = MONAT_BELEGE

    belege: list[Beleg] = []
    for umsatz in ausgaben:
        art = _art(umsatz)
        if art is None or art in OHNE_BELEG:
            continue
        if (umsatz.buchungsdatum.year, umsatz.buchungsdatum.month) != (jahr, monat):
            continue
        lieferant = lieferanten[umsatz.gegen_iban]
        datum = umsatz.buchungsdatum - datetime.timedelta(days=RECHNUNG_VORLAUF)
        ablage = services.dokument_ablegen(
            _rechnung_pdf(lieferant, umsatz, datum),
            art=dokumentarten.BELEG,
            datum=datum,
            herkunft=Dokument.Herkunft.TESTHOF,
        )
        beleg = services.beleg_anlegen(ablage.dokument, lieferant=lieferant, posten=False)
        belege.append(
            services.beleg_bestaetigen(
                beleg,
                lieferant=lieferant,
                betrag=umsatz.betrag,
                objekt=hof,
                kategorie=kassenbuch.kategorien[AUSGABE_KATEGORIE[art]],
                leistungsjahr=umsatz.buchungsdatum.year,
                am=kontext.zeitpunkt,
            )
        )

    # Sammelrechnung: ein Dokument, zwei Belege, je an ihrer Buchung.
    umsatz, buchungen = kassenbuch.sammelrechnung
    lieferant = lieferanten[umsatz.gegen_iban]
    datum = umsatz.buchungsdatum - datetime.timedelta(days=RECHNUNG_VORLAUF)
    ablage = services.dokument_ablegen(
        _rechnung_pdf(lieferant, umsatz, datum),
        art=dokumentarten.BELEG,
        datum=datum,
        herkunft=Dokument.Herkunft.TESTHOF,
    )
    gesamt = services.beleg_anlegen(ablage.dokument, lieferant=lieferant, posten=False)
    gesamt = services.beleg_bestaetigen(
        gesamt,
        lieferant=lieferant,
        betrag=umsatz.betrag,
        objekt=hof,
        kategorie=buchungen[0].kategorie,
        leistungsjahr=umsatz.buchungsdatum.year,
        am=kontext.zeitpunkt,
    )
    teile = services.beleg_aufteilen(
        gesamt,
        [
            services.Belegteil(
                betrag=b.betrag,
                objekt=b.objekt,
                kategorie=b.kategorie,
                leistungsjahr=b.leistungsjahr,
            )
            for b in buchungen
        ],
        am=kontext.zeitpunkt,
    )
    sammelrechnung = [
        services.beleg_an_buchung(teil, buchung)
        for teil, buchung in zip(teile, buchungen, strict=True)
    ]

    # Offener Beleg aus dem Postfach: gestern eingegangen, noch nicht bestätigt.
    letzte = ausgaben[-1]
    bekannt = lieferanten[letzte.gegen_iban]
    eingang = kontext.stichtag - datetime.timedelta(days=1)
    ablage = services.dokument_ablegen(
        _rechnung_pdf(bekannt, letzte, eingang) + b"% neue Rechnung\n",
        art=dokumentarten.BELEG,
        datum=eingang,
        herkunft=Dokument.Herkunft.POSTFACH,
        eingangskennung="<testhof-postfach-1@lieferant.testhof.invalid>",
    )
    offen = services.beleg_anlegen(ablage.dokument, lieferant=bekannt)

    # Mahnung eines Lieferanten (H6.8): am Lieferanten, ohne Buchung.
    gemahnt = lieferanten[ausgaben[0].gegen_iban]
    mahnung = services.lieferantendokument_ablegen(
        einfaches_pdf([gemahnt.name, "Zahlungserinnerung (erfunden, Testhof)"]),
        art=dokumentarten.MAHNUNG_LIEFERANT,
        lieferant=gemahnt,
        datum=kontext.stichtag - datetime.timedelta(days=20),
        herkunft=Dokument.Herkunft.TESTHOF,
    ).dokument

    # Offene Eingangsrechnung (Schritt 3b): noch zu zahlen.
    zahlen = lieferanten[ausgaben[1].gegen_iban]
    datum = kontext.stichtag - datetime.timedelta(days=3)
    nummer = "TH-2026-0815"
    ablage = services.dokument_ablegen(
        einfaches_pdf(
            [
                zahlen.name,
                "Rechnung (erfunden, Testhof)",
                f"Rechnungsnummer {nummer}",
                f"Betrag {OFFENE_RECHNUNG_BETRAG:.2f} EUR",
            ]
        ),
        art=dokumentarten.BELEG,
        datum=datum,
        herkunft=Dokument.Herkunft.TESTHOF,
    )
    beleg = services.beleg_bestaetigen(
        services.beleg_anlegen(ablage.dokument, lieferant=zahlen, posten=False),
        lieferant=zahlen,
        betrag=OFFENE_RECHNUNG_BETRAG,
        objekt=hof,
        kategorie=kassenbuch.kategorien["Reparaturen und Instandhaltung"],
        leistungsjahr=datum.year,
        am=kontext.zeitpunkt,
        faellig_am=kontext.stichtag + datetime.timedelta(days=OFFENE_RECHNUNG_TAGE),
        rechnungsnummer=nummer,
    )

    return BelegeDaten(
        lieferanten=lieferanten,
        belege=belege,
        sammelrechnung=sammelrechnung,
        offen=offen,
        mahnung=mahnung,
        rechnung=beleg.eingangsrechnung,
    )


def leeren() -> None:
    """Löscht Belege, Dokumente und Lieferanten (nur erfundene Daten, siehe ``aufbau.leeren``)."""
    Arbeitsposten.objects.filter(
        art__in=[
            services.ART_BELEGE_ZUORDNEN,
            services.ART_MAIL_OHNE_PDF,
            services.ART_RECHNUNG_ZAHLEN,
            services.ART_RECHNUNG_ZUORDNEN,
        ]
    ).delete()
    Frist.objects.filter(quelle=Frist.Quelle.EINGANGSRECHNUNG).delete()
    Eingangsrechnung.objects.all().delete()
    Beleg.objects.all().delete()
    Dokumentbezug.objects.all().delete()
    for dokument in Dokument.objects.all():
        dokument.datei.delete(save=False)
        if dokument.original:
            dokument.original.delete(save=False)
    Dokument.objects.all().delete()
    Lieferant.objects.all().delete()


register.registrieren(
    NAME,
    abhaengig_von=("kern", "bank", "kassenbuch", "dokumente", "fristen"),
    aufbauen=aufbauen,
    leeren=leeren,
)
