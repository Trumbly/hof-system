"""Fixtures für Ablage, Belege, Vorlagen und Versand: Fakes leeren, Briefkopf und Vorlagen,
Bezug, ein kleiner Hof mit Kostenarten und Ausgaben, Mails für das Rechnungspostfach."""

from __future__ import annotations

import datetime
import os
import warnings
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from email.message import EmailMessage
from typing import Any

import pytest

from apps.bank import services as bank_services
from apps.bank.models import Bankkonto, Bankumsatz
from apps.dokumente import pdf, services
from apps.dokumente.letterxpress import FakeLetterXpressClient
from apps.dokumente.models import Lieferant, Vorlage
from apps.dokumente.postfach import FakePostfach
from apps.dokumente.versand import FakeImapClient
from apps.kassenbuch import services as kassenbuch_services
from apps.kassenbuch.models import Buchung, Kategorie
from apps.kern import services as kern_services
from apps.kern.models import Benutzer, Mandant, Objekt
from testhof.pdfs import einfaches_pdf
from tests.factories import ObjektFabrik
from tests.kassenbuch.conftest import neue_iban

PDF_DA, PDF_GRUND = pdf.verfuegbar()
# In der Pipeline sind die Bibliotheken installiert: Dort ist Überspringen ein Fehler.
if os.environ.get("HOF_PDF_PFLICHT") and not PDF_DA:
    raise RuntimeError(f"HOF_PDF_PFLICHT gesetzt, aber: {PDF_GRUND}")

#: Für Tests, die das echte PDF prüfen (Text, Lage des Anschriftfelds).
braucht_weasyprint = pytest.mark.skipif(not PDF_DA, reason=f"PDF-Test übersprungen: {PDF_GRUND}")

ATTRAPPE = b"%PDF-1.7\n% Attrappe: WeasyPrint ohne Systembibliotheken\n%%EOF\n"


@pytest.fixture(autouse=True)
def _fakes_leeren() -> Iterator[None]:
    FakeImapClient.zuruecksetzen()
    FakeLetterXpressClient.zuruecksetzen()
    FakePostfach.zuruecksetzen()
    yield
    FakeImapClient.zuruecksetzen()
    FakeLetterXpressClient.zuruecksetzen()
    FakePostfach.zuruecksetzen()


@pytest.fixture(autouse=True)
def _pdf_ohne_weasyprint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fehlt WeasyPrint, laufen die Abläufe mit einer PDF-Attrappe weiter (mit Warnung);
    die eigentlichen PDF-Tests sind dann übersprungen (``braucht_weasyprint``)."""
    if PDF_DA:
        return
    warnings.warn(f"PDF-Attrappe statt WeasyPrint: {PDF_GRUND}", stacklevel=1)
    zaehler = iter(range(1, 1_000_000))
    monkeypatch.setattr(pdf, "erzeugen", lambda html: ATTRAPPE + str(next(zaehler)).encode())


BRIEFKOPF = (
    '<div class="kopf"><p>{{ absender.name }}</p></div>'
    '<div class="ruecksendeangabe">{{ absender.name }} · {{ absender.anschrift }}</div>'
    '<div class="fuss">{{ absender.email }}</div>'
)

MITTEILUNG = (
    "<p>{{ empfaenger.anrede }},</p>"
    "<p>zum Objekt {{ objekt.bezeichnung }} ({{ objekt.kuerzel }}) teilen wir mit: "
    "offen sind {{ betrag.offen|euro }}.</p>"
)

KUENDIGUNG = (
    "<p>{{ empfaenger.anrede }},</p>"
    "<p>wir kündigen den Vertrag {{ vertrag.nummer }} zum {{ vertrag.ende|datum }}.</p>"
)


def freigegeben(name: str, art: str, text: str, benutzer: Benutzer, betreff: str = "") -> Vorlage:
    vorlage = services.vorlage_anlegen(name, art, text, betreff=betreff)
    return services.vorlage_freigeben(vorlage, benutzer, freigegeben_von="Test")


@pytest.fixture
def briefkopf(benutzer: Benutzer) -> Vorlage:
    return freigegeben("Briefkopf", "briefkopf", BRIEFKOPF, benutzer)


@pytest.fixture
def mitteilung(briefkopf: Vorlage, benutzer: Benutzer) -> Vorlage:
    return freigegeben(
        "Mitteilung", "mitteilung", MITTEILUNG, benutzer, betreff="Hinweis {{ objekt.kuerzel }}"
    )


@pytest.fixture
def kuendigung(briefkopf: Vorlage, benutzer: Benutzer) -> Vorlage:
    return freigegeben(
        "Kündigung", "kuendigung", KUENDIGUNG, benutzer, betreff="Kündigung {{ vertrag.nummer }}"
    )


@pytest.fixture
def objekt(db: None) -> Objekt:
    return ObjektFabrik(kuerzel="GAR1", bezeichnung="Garagenhof")  # type: ignore[no-any-return]


def kontext(objekt: Objekt, *, email: str = "empfaenger@testhof.invalid") -> dict[str, Any]:
    """Kontext mit erfundenen Werten (Faker-frei, damit Tests gezielt suchen können)."""
    return {
        "empfaenger": {
            "name": "Erika Beispiel",
            "anrede": "Guten Tag Frau Beispiel",
            "anschrift": "Erfundene Straße 1\n12345 Testdorf",
            "email": email,
        },
        "objekt": {
            "kuerzel": objekt.kuerzel,
            "bezeichnung": objekt.bezeichnung,
            "anschrift": objekt.anschrift,
        },
        "betrag": {"offen": "120.50"},
        "vertrag": {"nummer": "V-0815", "ende": datetime.date(2027, 3, 31)},
    }


# --- Ablage und Belege (Schritt 3a) --------------------------------------------------


def rechnung_pdf(nummer: str = "R-1", betrag: str = "119.00") -> bytes:
    """Ein kleines erfundenes PDF; gleiche Angaben ergeben dieselben Bytes."""
    return einfaches_pdf(["Erfundene Lieferant GmbH", f"Rechnung {nummer}", f"Betrag {betrag}"])


def rechnungsmail(
    *anlagen: bytes,
    absender: str = "rechnung@lieferant.testhof.invalid",
    datum: str = "Tue, 17 Nov 2026 09:15:00 +0100",
    message_id: str = "<r1@lieferant.testhof.invalid>",
    pdf_als_octet_stream: bool = False,
) -> bytes:
    """Rohdaten einer Mail ans Rechnungspostfach mit PDF-Anlagen (erfundene Werte)."""
    nachricht = EmailMessage()
    nachricht["From"] = f"Erfundene Lieferant GmbH <{absender}>"
    nachricht["To"] = "rechnungen@hof.invalid"
    nachricht["Subject"] = "Ihre Rechnung"
    nachricht["Date"] = datum
    nachricht["Message-ID"] = message_id
    nachricht.set_content("Anbei die Rechnung.")
    for nummer, inhalt in enumerate(anlagen, start=1):
        maintype, subtype = (
            ("application", "octet-stream") if pdf_als_octet_stream else ("application", "pdf")
        )
        nachricht.add_attachment(
            inhalt, maintype=maintype, subtype=subtype, filename=f"rechnung-{nummer}.pdf"
        )
    return nachricht.as_bytes()


@dataclass
class Belegwelt:
    gbr: Mandant
    hof: Objekt
    halle: Objekt
    konto: Bankkonto
    strom: Kategorie
    versicherung: Kategorie
    kontofuehrung: Kategorie
    miete: Kategorie
    lieferant: Lieferant

    def ausgabe(
        self,
        datum: datetime.date,
        betrag: str,
        *,
        kategorie: Kategorie | None = None,
        objekt: Objekt | None = None,
        zweck: str = "Rechnung R-1",
        iban: str | None = None,
        buchen: bool = True,
    ) -> Bankumsatz:
        """Eine Abbuchung über die Bank, auf Wunsch gleich ganz gebucht."""
        ergebnis = bank_services.umsaetze_uebernehmen(
            self.konto,
            [
                bank_services.UmsatzDaten(
                    buchungsdatum=datum,
                    betrag=Decimal(betrag),
                    richtung="ausgang",
                    gegenpartei="Erfundene Lieferant GmbH",
                    gegen_iban=iban or self.lieferant.iban,
                    verwendungszweck=zweck,
                )
            ],
        )
        umsatz = ergebnis.umsaetze[0]
        if buchen:
            kassenbuch_services.umsatz_zuordnen(
                umsatz, kategorie=kategorie or self.strom, objekt=objekt or self.hof
            )
        return umsatz


def gueltige_buchung(umsatz: Bankumsatz) -> Buchung:
    return kassenbuch_services.gueltige_buchungen().get(umsatz=umsatz)


@pytest.fixture
def belegwelt(db: None) -> Belegwelt:
    gbr = kern_services.mandant_anlegen(Mandant.Art.GBR, "GBR", "GbR Beispiel")
    return Belegwelt(
        gbr=gbr,
        hof=kern_services.objekt_anlegen(gbr, "HOF", "Hofanlage"),
        halle=kern_services.objekt_anlegen(gbr, "HALLE", "Halle"),
        konto=bank_services.bankkonto_anlegen(
            gbr,
            bank="Vivid",
            bezeichnung="Vivid Test",
            iban=neue_iban(),
            zugang=Bankkonto.Zugang.ENABLE_BANKING,
        ),
        strom=kassenbuch_services.kategorie_anlegen(
            "Strom", Kategorie.Art.AUSGABE, umlagefaehiger_anteil=50
        ),
        versicherung=kassenbuch_services.kategorie_anlegen(
            "Versicherungen", Kategorie.Art.AUSGABE, umlagefaehiger_anteil=100
        ),
        kontofuehrung=kassenbuch_services.kategorie_anlegen("Kontoführung", Kategorie.Art.AUSGABE),
        miete=kassenbuch_services.kategorie_anlegen("Mieteinnahmen", Kategorie.Art.EINNAHME),
        lieferant=services.lieferant_anlegen(
            "Erfundene Lieferant GmbH",
            iban=neue_iban(),
            email="rechnung@lieferant.testhof.invalid",
        ),
    )
