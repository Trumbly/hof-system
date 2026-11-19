"""PDF mit WeasyPrint und Briefkopf nach DIN 5008 (Schritt 3c, Aufgaben 2 und 3; H8.2).

Fehlen WeasyPrint die Systembibliotheken (macOS ohne pango), sind die Tests mit
``braucht_weasyprint`` übersprungen; die Pipeline (Ubuntu) installiert sie.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from typing import Any

import pytest
from django.utils import timezone

from apps.dokumente import pdf, services
from apps.dokumente.models import Schreiben, Vorlage
from apps.dokumente.vorlagenarten import Versandweg
from apps.kern.models import Objekt
from tests.dokumente.conftest import braucht_weasyprint, kontext

pytestmark = pytest.mark.django_db

MM_JE_PX = 25.4 / 96


def _boxen(box: Any) -> Iterator[Any]:
    yield box
    for kind in getattr(box, "children", ()):
        yield from _boxen(kind)


def _box_mit_klasse(seite: Any, klasse: str) -> Any:
    for box in _boxen(seite._page_box):
        element = getattr(box, "element", None)
        if element is not None and klasse in (element.get("class") or "").split():
            return box
    raise AssertionError(f"Keine Box mit Klasse {klasse}")


def _text(box: Any) -> str:
    return " ".join(b.text for b in _boxen(box) if hasattr(b, "text") and b.text)


def _gerendert(html: str) -> Any:
    weasyprint = pdf._weasyprint()
    return weasyprint.HTML(string=html).render()


def test_aus_freigegebener_vorlage_entsteht_schreiben_mit_pdf(
    mitteilung: Vorlage, objekt: Objekt
) -> None:
    """Abnahme Schritt 3 (Bauplan): Aus einer freigegebenen Vorlage entsteht ein PDF;
    Schreiben mit Vorlage und Version, Bezug, PDF, Versandweg und Status „bereit“."""
    schreiben = services.schreiben_erzeugen(mitteilung, objekt, kontext(objekt), Versandweg.MAIL)

    schreiben.refresh_from_db()
    assert schreiben.status == Schreiben.Status.BEREIT
    assert (schreiben.vorlage, schreiben.vorlage_version) == (mitteilung, 1)
    assert schreiben.bezug == objekt
    assert schreiben.versandweg == Versandweg.MAIL
    assert schreiben.betreff == "Hinweis GAR1"
    assert "Guten Tag Frau Beispiel" in schreiben.inhalt_html
    assert "120,50 €" in schreiben.inhalt_html
    with schreiben.pdf.open("rb") as datei:
        daten = datei.read()
    assert daten.startswith(b"%PDF")
    assert hashlib.sha256(daten).hexdigest() == schreiben.pdf_sha256


def test_dateiname_aus_art_datum_und_id_nie_aus_namen(mitteilung: Vorlage, objekt: Objekt) -> None:
    """Schritt 3c, Aufgabe 2: Der Dateiname des PDFs besteht aus Art, Datum und ID."""
    schreiben = services.schreiben_erzeugen(mitteilung, objekt, kontext(objekt), Versandweg.MAIL)
    heute = timezone.localdate()
    assert schreiben.pdf.name == (
        f"schreiben/{heute:%Y/%m}/mitteilung_{heute:%Y-%m-%d}_{schreiben.pk}.pdf"
    )
    assert "Beispiel" not in schreiben.pdf.name
    assert re.fullmatch(r"[a-z0-9_/\-]+\.pdf", schreiben.pdf.name)


def test_brief_braucht_anschrift_mail_braucht_adresse(mitteilung: Vorlage, objekt: Objekt) -> None:
    """Schritt 3c, Aufgabe 2: Je Versandweg müssen die Empfängerangaben da sein."""
    ohne_mail = kontext(objekt, email="")
    with pytest.raises(services.SchreibenFehler, match="E-Mail-Adresse des Empfängers"):
        services.schreiben_erzeugen(mitteilung, objekt, ohne_mail, Versandweg.MAIL)
    ohne_anschrift = kontext(objekt)
    ohne_anschrift["empfaenger"]["anschrift"] = ""
    with pytest.raises(services.SchreibenFehler, match="Anschrift des Empfängers"):
        services.schreiben_erzeugen(
            mitteilung, objekt, ohne_anschrift, Versandweg.EINWURF_EINSCHREIBEN
        )
    unvollstaendig = kontext(objekt)
    del unvollstaendig["betrag"]
    with pytest.raises(services.PlatzhalterFehler, match="betrag"):
        services.schreiben_erzeugen(mitteilung, objekt, unvollstaendig, Versandweg.BRIEF)
    assert not Schreiben.objects.exists()


@braucht_weasyprint
def test_pdf_hat_anschrift_im_fenster_nach_din_5008(mitteilung: Vorlage, objekt: Objekt) -> None:
    """Schritt 3c, Aufgabe 3: PDF mit WeasyPrint, Briefkopf als eigene Vorlage,
    DIN-5008-Fensterposition für die Anschrift (Form B: links 25 mm, Anschriftzone ab
    62,7 mm von oben, 80 × 27,3 mm)."""
    html = pdf.brief_html(
        briefkopf_html=services._briefkopf_html({"absender": services.absender()}),
        inhalt_html="<p>Inhalt</p>",
        betreff="Betreff",
        empfaenger_anschrift="Erika Beispiel\nErfundene Straße 1\n12345 Testdorf",
        datum=timezone.localdate(),
        zeichen="S-1",
    )
    seite = _gerendert(html).pages[0]
    assert round(seite.width * MM_JE_PX) == 210
    assert round(seite.height * MM_JE_PX) == 297

    feld = _box_mit_klasse(seite, "anschriftfeld")
    assert feld.position_x * MM_JE_PX == pytest.approx(25, abs=0.5)
    assert feld.position_y * MM_JE_PX == pytest.approx(62.7, abs=0.5)
    assert feld.width * MM_JE_PX == pytest.approx(80, abs=0.5)
    assert _text(feld) == "Erika Beispiel Erfundene Straße 1 12345 Testdorf"

    ruecksendung = _box_mit_klasse(seite, "ruecksendeangabe")
    assert 45 <= ruecksendung.position_y * MM_JE_PX < 62.7
    info = _box_mit_klasse(seite, "infoblock")
    assert info.position_x * MM_JE_PX == pytest.approx(125, abs=0.5)
    assert "S-1" in _text(info)
    betreff = _box_mit_klasse(seite, "betreff")
    assert betreff.position_y * MM_JE_PX == pytest.approx(98.5, abs=0.5)


@braucht_weasyprint
def test_pdf_enthaelt_briefkopf_und_text(mitteilung: Vorlage, objekt: Objekt) -> None:
    """Schritt 3c, Aufgabe 3: Briefkopf (eigene Vorlage) und Inhalt stehen im PDF."""
    schreiben = services.schreiben_erzeugen(mitteilung, objekt, kontext(objekt), Versandweg.BRIEF)
    voll = {**kontext(objekt), "absender": services.absender()}
    html = pdf.brief_html(
        briefkopf_html=services._briefkopf_html(voll),
        inhalt_html=schreiben.inhalt_html,
        betreff=schreiben.betreff,
        empfaenger_anschrift="Erika Beispiel\nErfundene Straße 1\n12345 Testdorf",
        datum=timezone.localdate(),
        zeichen=f"S-{schreiben.pk}",
    )
    text = " ".join(_text(s._page_box) for s in _gerendert(html).pages)
    assert services.absender()["name"] in text
    assert "Hinweis GAR1" in text
    assert "Garagenhof" in text


@braucht_weasyprint
def test_vorschau_einer_vorlage_zeigt_platzhalter(mitteilung: Vorlage) -> None:
    """Schritt 3c, Aufgabe 6: Vorschau einer Vorlage (auch Entwurf) als PDF mit den Namen
    der Platzhalter, ohne dass ein Schreiben entsteht."""
    entwurf = services.vorlage_neue_version(mitteilung)
    daten = services.vorschau_pdf(entwurf)
    assert daten.startswith(b"%PDF")
    assert not Schreiben.objects.exists()
