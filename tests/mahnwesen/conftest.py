"""Fixtures des Mahnwesens: Stufen nach der SOP, freigegebene Vorlagen, ein kleiner Hof mit
Vertrag und Datenstand der Bank, Senden und Zustellen an einem gespielten Tag."""

from __future__ import annotations

import datetime
import warnings
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.bank import services as bank_services
from apps.dokumente import pdf
from apps.dokumente import services as dokumente
from apps.dokumente.letterxpress import FakeLetterXpressClient
from apps.dokumente.models import Schreiben, Vorlage
from apps.dokumente.versand import FakeImapClient
from apps.kern.models import Arbeitsposten, Benutzer
from apps.mahnwesen import services
from apps.mahnwesen.models import Mahnfall, Mahnschritt
from apps.soll import services as soll
from apps.stammdaten import services as stammdaten
from apps.stammdaten.models import Partei, Vertrag
from testhof import dokumente as testhof_dokumente
from testhof import mahnwesen as testhof_mahnwesen
from tests.kassenbuch.conftest import KleinerHof, klein
from tests.stammdaten.conftest import garage_anlegen, mieter_anlegen

__all__ = ["klein"]

D = datetime.date
PDF_DA, PDF_GRUND = pdf.verfuegbar()
ATTRAPPE = b"%PDF-1.7\n% Attrappe: WeasyPrint ohne Systembibliotheken\n%%EOF\n"
#: „Heute“ der Sollstellung im kleinen Hof: Posten bis November 2026 stehen.
HEUTE = D(2026, 11, 20)
OKTOBER = D(2026, 10, 1)
#: Fälligkeit der Oktobermiete (dritter Werktag in NRW) und der Novembermiete.
FAELLIG_OKT = D(2026, 10, 5)
FAELLIG_NOV = D(2026, 11, 4)
ART = services.ART


@pytest.fixture(autouse=True)
def _fakes_leeren() -> Iterator[None]:
    FakeImapClient.zuruecksetzen()
    FakeLetterXpressClient.zuruecksetzen()
    yield
    FakeImapClient.zuruecksetzen()
    FakeLetterXpressClient.zuruecksetzen()


@pytest.fixture(autouse=True)
def _pdf_ohne_weasyprint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fehlt WeasyPrint, laufen die Abläufe mit einer PDF-Attrappe weiter (mit Warnung)."""
    if PDF_DA:
        return
    warnings.warn(f"PDF-Attrappe statt WeasyPrint: {PDF_GRUND}", stacklevel=1)
    zaehler = iter(range(1, 1_000_000))
    monkeypatch.setattr(pdf, "erzeugen", lambda html: ATTRAPPE + str(next(zaehler)).encode())


@pytest.fixture
def stufen(db: None) -> None:
    """Die Stufen nach der SOP (die Datenmigration legt sie an; nach einem Leeren der
    Datenbank legt der Service sie neu an)."""
    services.startdaten_anlegen()


def freigeben(name: str, art: str, text: str, benutzer: Benutzer, betreff: str = "") -> Vorlage:
    vorlage = dokumente.vorlage_anlegen(name, art, text, betreff=betreff)
    return dokumente.vorlage_freigeben(vorlage, benutzer, freigegeben_von="Test")


def _testhof_text(art: str) -> tuple[str, str, str]:
    for name, a, betreff, text in (*testhof_dokumente.VORLAGEN, *testhof_mahnwesen.VORLAGEN):
        if a == art:
            return name, betreff, text
    raise KeyError(art)


FRISTLOS = """\
<p>{{ empfaenger.anrede }},</p>
<p>wir kündigen den Vertrag {{ vertrag.nummer }} fristlos, hilfsweise ordentlich zum
{{ kuendigung.fristende_ordentlich|datum }}. Rückstand: {{ betrag.offen|euro }}.
Bitte räumen Sie bis zum {{ kuendigung.raeumung_bis|datum }}.</p>
"""


@dataclass
class Vorlagen:
    benutzer: Benutzer

    def freigeben(self, art: str, name: str | None = None, text: str | None = None) -> Vorlage:
        testhof_name, betreff, testhof_text = (
            _testhof_text(art) if text is None else ("", "{{ vertrag.nummer }}", "")
        )
        return freigeben(name or testhof_name, art, text or testhof_text, self.benutzer, betreff)

    def fristlos(self) -> Vorlage:
        return self.freigeben("kuendigung", "Fristlose Kündigung", FRISTLOS)


@pytest.fixture
def vorlagen(benutzer: Benutzer, stufen: None) -> Vorlagen:
    """Briefkopf, Zahlungserinnerung, Mahnung und Abmahnung freigegeben (Texte des
    Testhofs). Die fristlose Kündigung fehlt: Diese Stufe ist im Probebetrieb."""
    v = Vorlagen(benutzer)
    for art in ("briefkopf", "zahlungserinnerung", "mahnung", "abmahnung"):
        v.freigeben(art)
    return v


@dataclass
class Mahnhof:
    klein: KleinerHof
    mieter: Partei

    def vertrag(self, miete: str = "85.00", beginn: datetime.date = OKTOBER) -> Vertrag:
        einheit = garage_anlegen(self.klein.hof, frei_ab=D(2019, 1, 1))
        return stammdaten.vertrag_anlegen(
            einheiten=[einheit],
            mieter=[self.mieter],
            beginn=beginn,
            miete=Decimal(miete),
            status=Vertrag.Status.AKTIV,
            kaution=Decimal("0.00"),
        )

    def datenstand(self, tag: datetime.date) -> None:
        """Bankdaten liegen bis ``tag`` vor."""
        bank_services.kontostand_erfassen(self.klein.konto, tag, Decimal("1000.00"))


@pytest.fixture
def mahnhof(klein: KleinerHof, monkeypatch: pytest.MonkeyPatch, settings: object) -> Mahnhof:
    settings.ABGLEICH_BEGINN = D(2026, 7, 1)  # type: ignore[attr-defined]
    monkeypatch.setattr(soll, "heute", lambda: HEUTE)
    hof = Mahnhof(klein=klein, mieter=mieter_anlegen())
    hof.datenstand(D(2026, 10, 31))
    return hof


def lauf(stichtag: datetime.date) -> services.Ergebnis:
    return services.mahnlauf(stichtag)


def fall_zu(vertrag: Vertrag) -> Mahnfall:
    return Mahnfall.objects.get(vertrag=vertrag, status__in=("offen", "gesperrt"))


def schritte(vertrag: Vertrag) -> list[Mahnschritt]:
    return list(
        Mahnschritt.objects.filter(fall__vertrag=vertrag)
        .select_related("stufe", "schreiben")
        .order_by("stichtag", "pk")
    )


def letzter(vertrag: Vertrag) -> Mahnschritt:
    return schritte(vertrag)[-1]


def senden(
    schreiben: Schreiben, benutzer: Benutzer, am: datetime.date, *, anwalt: bool = False
) -> Schreiben:
    """Senden wie auf der Seite (mit Prüfsumme der Vorschau). Die Tests spielen Wochen nach:
    Das Senden gilt als ``am`` geschehen, nicht heute."""
    dokumente.schreiben_senden(
        schreiben, benutzer, pruefsumme=schreiben.pdf_sha256, anwalt_gesehen=anwalt
    )
    zeit = datetime.datetime.combine(am, datetime.time(10), tzinfo=ZoneInfo("Europe/Berlin"))
    Schreiben.objects.filter(pk=schreiben.pk).update(gesendet_am=zeit)
    schreiben.refresh_from_db()
    return schreiben


def zustellen(schreiben: Schreiben, am: datetime.date) -> Schreiben:
    return dokumente.schreiben_abschliessen(
        schreiben, zugestellt_am=am, vermerk=f"Einwurf durch Boten am {am:%d.%m.%Y} (Test)"
    )


def mahnposten(fall: Mahnfall | None = None) -> list[Arbeitsposten]:
    qs = Arbeitsposten.objects.filter(art=ART).order_by("pk")
    if fall is not None:
        qs = qs.filter(bezug_id=fall.pk)
    return list(qs)
