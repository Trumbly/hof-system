"""Review 2, A11: Checkliste „Vertrag aus Vorlage erzeugt“, Bonität ab 100 € (P8), eine
Zählung der Verträge, Vertragstext ohne doppelten Typ und ohne Leerzeichen vor dem Komma."""

from __future__ import annotations

import datetime
import re
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse
from django.utils.html import strip_tags

from apps.dokumente import platzhalter
from apps.dokumente.models import Schreiben
from apps.stammdaten import schreiben, services
from apps.stammdaten.models import Checkliste, Einheit, Partei, Vertrag
from testhof.dokumente import VORLAGEN
from tests.stammdaten.conftest import braucht_weasyprint, vertrag_anlegen

pytestmark = pytest.mark.django_db
D = datetime.date
ERZEUGT = "Vertrag und Anlagen aus der Vorlage erzeugt"


def _punkte(vertrag: Vertrag) -> dict[str, datetime.date | None]:
    liste = Checkliste.objects.get(vertrag=vertrag, phase="vertrag")
    return {p.text: p.erledigt_am for p in liste.punkte.all()}


def test_checkpunkt_vertrag_erzeugt_wird_abgehakt(
    entwurf: Vertrag, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review 2, A11: Ist das PDF des Vertrags erzeugt, hakt das System „Vertrag und
    Anlagen aus der Vorlage erzeugt“ in der Checkliste ab. Ohne PDF (Arbeitsposten) nicht."""
    services.checkliste_anlegen("vertrag", vertrag=entwurf)
    services.vertragsdokument_erzeugen(entwurf)  # ohne Vorlage: Arbeitsposten
    assert _punkte(entwurf)[ERZEUGT] is None

    monkeypatch.setattr(schreiben, "vertragsdokument_erzeugen", lambda v: Schreiben())
    services.vertragsdokument_erzeugen(entwurf)
    assert _punkte(entwurf)[ERZEUGT] is not None


@braucht_weasyprint
def test_checkpunkt_abgehakt_mit_echtem_pdf(entwurf: Vertrag, vorlagen: object) -> None:
    """Review 2, A11: wie oben mit echtem PDF; auch eine danach angelegte Checkliste hat den
    Punkt schon abgehakt (wie in „Neuer Vertrag“, wo das PDF gleich mit entsteht)."""
    ergebnis = services.vertragsdokument_erzeugen(entwurf)
    assert isinstance(ergebnis, Schreiben)
    services.checkliste_anlegen("vertrag", vertrag=entwurf)
    assert _punkte(entwurf)[ERZEUGT] is not None


def test_bonitaet_ab_100_euro_im_vertragsablauf(
    garage: Einheit, mieter: Partei, angemeldet: Client
) -> None:
    """Review 2, A11 (P8): Ab 100 € Monatsmiete steht ein Bonitäts-Hinweis am Entwurf und
    ein eigener Punkt in der Checkliste „Vertrag schließen“. Darunter nicht."""
    teuer = services.vertrag_anlegen(
        einheiten=[garage], mieter=[mieter], beginn=D(2026, 12, 1), miete=Decimal("100.00")
    )
    services.checkliste_anlegen("vertrag", vertrag=teuer)
    assert services.bonitaet_noetig(teuer)
    assert any(t.startswith("Bonität geprüft") for t in _punkte(teuer))
    seite = angemeldet.get(reverse("stammdaten:vertrag", args=[teuer.pk])).content.decode()
    assert 'id="bonitaet"' in seite and "Bonitätsnachweis" in seite

    services.vertrag_aendern(teuer, ende=D(2026, 12, 31))  # Einheit wieder frei ab 2027
    guenstig = services.vertrag_anlegen(
        einheiten=[garage], mieter=[mieter], beginn=D(2027, 1, 1), miete=Decimal("99.99")
    )
    services.checkliste_anlegen("vertrag", vertrag=guenstig)
    assert not services.bonitaet_noetig(guenstig)
    assert not any(t.startswith("Bonität") for t in _punkte(guenstig))
    seite = angemeldet.get(reverse("stammdaten:vertrag", args=[guenstig.pk])).content.decode()
    assert 'id="bonitaet"' not in seite


def test_neuer_vertrag_meldet_bonitaet(garage: Einheit, mieter: Partei, angemeldet: Client) -> None:
    """Review 2, A11 (P8): „Neuer Vertrag“ mit 120 € meldet den Bonitätsnachweis."""
    antwort = angemeldet.post(
        reverse("stammdaten:vertrag_neu"),
        {
            "einheit": garage.pk,
            "mieter": mieter.pk,
            "mandant": garage.objekt.mandant_id,
            "beginn": "2026-12-01",
            "miete": "120.00",
            "zahlweise": "monatlich",
        },
        follow=True,
    )
    assert "Bonitätsnachweis" in antwort.content.decode()


def test_eine_zaehlung_der_vertraege(
    garage: Einheit, mieter: Partei, objekt: object, angemeldet: Client
) -> None:
    """Review 2, A11: Stammdaten und Vertragsliste zählen gleich: laufend = aktiv oder
    gekündigt. Entwürfe und beendete stehen getrennt daneben."""
    from tests.stammdaten.conftest import garage_anlegen

    vertrag_anlegen(garage, mieter, status="aktiv")
    vertrag_anlegen(garage_anlegen(objekt), mieter, status="aktiv")  # type: ignore[arg-type]
    vertrag_anlegen(garage_anlegen(objekt), mieter)  # type: ignore[arg-type]
    zaehlung = Vertrag.objects.zaehlung()
    assert zaehlung == {"laufend": 2, "entwurf": 1, "beendet": 0, "alle": 3}
    assert Vertrag.objects.laufend().count() == 2
    assert Vertrag.objects.bindend().count() == 3

    uebersicht = angemeldet.get(reverse("stammdaten:uebersicht")).content.decode()
    assert re.search(r"<strong>2</strong> laufende Verträge", uebersicht)
    liste = angemeldet.get(reverse("stammdaten:vertraege")).content.decode()
    assert len(re.findall(r'data-titel="Nummer"', liste)) == 2
    assert "laufend (2)" in liste and "Entwürfe (1)" in liste and "alle (3)" in liste


def _vertragstext(vertrag: Vertrag) -> str:
    (text,) = [t for name, art, _, t in VORLAGEN if art == "vertrag"]
    html = platzhalter.rendern(text, schreiben.kontext_vertrag(vertrag))
    return re.sub(r"\s+", " ", strip_tags(html))


def test_vertragstext_ohne_doppelten_typ_und_ohne_leerzeichen_vor_komma(
    objekt: object, mieter: Partei
) -> None:
    """Review 2, A11: Im gerenderten Vertrag steht „Garten 3“ statt „Garten Garten 3“ und
    „140,00 € je Monat, zahlbar“ statt „140,00 € je Monat , zahlbar“."""
    garten = services.einheit_anlegen(
        objekt,  # type: ignore[arg-type]
        Einheit.Typ.GARTEN,
        bezeichnung="Garten 3",
        flaeche_qm=Decimal("379.62"),
    )
    vertrag = services.vertrag_anlegen(
        einheiten=[garten], mieter=[mieter], beginn=D(2026, 12, 1), miete=Decimal("140.00")
    )
    text = _vertragstext(vertrag)
    assert "Garten 3" in text and "Garten Garten" not in text
    assert "140,00 € je Monat, zahlbar monatlich." in text
    assert " ," not in text

    zweiter = services.einheit_anlegen(objekt, Einheit.Typ.GARTEN, bezeichnung="Süd")  # type: ignore[arg-type]
    mit_pauschale = services.vertrag_anlegen(
        einheiten=[zweiter],
        mieter=[mieter],
        beginn=D(2026, 12, 1),
        miete=Decimal("140.00"),
        pauschale=Decimal("10.00"),
    )
    text = _vertragstext(mit_pauschale)
    assert "Garten Süd" in text
    assert "je Monat zuzüglich einer Pauschale von 10,00 €, zahlbar" in text


def test_einheit_titel() -> None:
    """Review 2, A11: Typ und Bezeichnung ohne Doppelung."""
    for bezeichnung, erwartet in (
        ("Garten 3", "Garten 3"),
        ("Süd", "Garten Süd"),
        ("", "Garten NW-GA-0001"),
    ):
        einheit = Einheit(typ=Einheit.Typ.GARTEN, bezeichnung=bezeichnung, nw_id="NW-GA-0001")
        assert schreiben.einheit_titel(einheit) == erwartet
