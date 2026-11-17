"""Fixtures für Vorlagen und Versand: Fakes leeren, Briefkopf und Vorlagen, Bezug."""

from __future__ import annotations

import datetime
import os
import warnings
from collections.abc import Iterator
from typing import Any

import pytest

from apps.dokumente import pdf, services
from apps.dokumente.letterxpress import FakeLetterXpressClient
from apps.dokumente.models import Vorlage
from apps.dokumente.versand import FakeImapClient
from apps.kern.models import Benutzer, Objekt
from tests.factories import ObjektFabrik

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
    yield
    FakeImapClient.zuruecksetzen()
    FakeLetterXpressClient.zuruecksetzen()


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
    return services.vorlage_freigeben(vorlage, benutzer)


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
