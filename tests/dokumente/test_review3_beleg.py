"""Review 3, Punkt 7: Beleg und Abbuchung. Der Hinweis auf einen abweichenden Betrag steht
schon vor dem Bestätigen am Feld; die Ablage zeigt bei offenen Belegen die aus dem PDF
gelesenen Werte (grau, „gelesen“) statt „–“."""

from __future__ import annotations

import datetime
import json
import re
from decimal import Decimal
from typing import Any

import pytest
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.dokumente import services, textschicht
from apps.dokumente.models import Beleg
from apps.kern import zahlen
from testhof.aufbau import Testhof
from testhof.pdfs import rechnung_pdf


def _hochladen(angemeldet: Client, pdf: bytes) -> Beleg:
    antwort = angemeldet.post(
        reverse("dokumente:beleg_neu"),
        {"datei": SimpleUploadedFile("rechnung.pdf", pdf, content_type="application/pdf")},
    )
    treffer = re.search(r"/belege/(\d+)/$", antwort["Location"])
    assert treffer is not None, antwort["Location"]
    return Beleg.objects.get(pk=int(treffer.group(1)))


def _neuer_beleg(angemeldet: Client, betrag: str = "86.40") -> Beleg:
    return _hochladen(
        angemeldet,
        rechnung_pdf(
            lieferant="Gelesen Beispiel (erfunden)",
            nummer="G-3",
            datum=datetime.date(2026, 10, 20),
            betrag=Decimal(betrag),
        ),
    )


def _betragsfeld(html: str) -> str:
    feld = re.search(r'<input[^>]*name="betrag"[^>]*>', html)
    assert feld is not None
    return feld.group(0)


def test_betragsfeld_kennt_den_pdf_betrag(testhof: Testhof, angemeldet: Client) -> None:
    """Review 3, Punkt 7: Das Betragsfeld trägt den gelesenen PDF-Betrag als Vergleich; die
    vorgeschlagene Abbuchung ihren Betrag. So steht der Hinweis schon beim Tippen am Feld
    (bausteine.js), nicht erst nach dem Bestätigen."""
    beleg = Beleg.objects.get(pk=testhof.belege.offen.pk)
    angaben = textschicht.angaben_aus_dokument(beleg.dokument)
    assert angaben.betrag is not None
    html = angemeldet.get(reverse("dokumente:beleg", args=[beleg.pk])).content.decode()
    feld = _betragsfeld(html)
    roh = re.search(r"data-vergleich=\"([^\"]*)\"", feld)
    assert roh is not None, feld
    vergleich = json.loads(roh.group(1).replace("&quot;", '"'))
    assert vergleich == [{"betrag": str(angaben.betrag), "text": "vom gelesenen PDF-Betrag"}]
    for radio in re.findall(r'<input type="radio" name="buchung" value="\d+"[^>]*>', html):
        assert 'data-vergleich-betrag="' in radio
        assert 'data-vergleich-text="von der gewählten Abbuchung"' in radio


def test_vorschlag_per_htmx_behaelt_den_vergleich(testhof: Testhof, angemeldet: Client) -> None:
    """Review 3, Punkt 7: Auch nach der Wahl des Lieferanten (HTMX ersetzt die Felder) kennt
    das Betragsfeld den PDF-Betrag."""
    beleg = _neuer_beleg(angemeldet)
    html = angemeldet.get(
        reverse("dokumente:beleg_vorschlag"), {"beleg": beleg.pk, "betrag": "80,00"}
    ).content.decode()
    assert "data-vergleich=" in _betragsfeld(html)
    assert "86.40" in _betragsfeld(html)


def test_ablage_zeigt_gelesene_werte(testhof: Testhof, angemeldet: Client) -> None:
    """Review 3, Punkt 7: Bei einem noch nicht bestätigten Beleg ohne Lieferant zeigt die
    Ablage Lieferant und Betrag aus dem PDF, grau mit dem Zusatz „gelesen“, statt „–“."""
    beleg = _neuer_beleg(angemeldet)
    assert beleg.lieferant is None and beleg.betrag is None
    html = angemeldet.get(reverse("dokumente:ablage")).content.decode()
    zeile = next(
        z for z in re.findall(r"<tr>.*?</tr>", html, re.S) if f"Dokument #{beleg.dokument_id}<" in z
    )
    assert "Gelesen Beispiel (erfunden)" in zeile
    assert zahlen.euro(Decimal("86.40")) in zeile
    assert zeile.count('class="gelesen-zusatz">gelesen<') == 2
    # Auch die Karte „Belege zuordnen“ nennt den gelesenen Namen statt „noch unbekannt“.
    karte = html.split('id="dokumente"')[0] if 'id="dokumente"' in html else html
    assert karte.count("Gelesen Beispiel (erfunden)") >= 1


def test_bestaetigter_beleg_ohne_gelesen(testhof: Testhof, angemeldet: Client) -> None:
    """Review 3, Punkt 7: Ein bestätigter Beleg zeigt seine eigenen Werte, ohne „gelesen“."""
    beleg = testhof.belege.rechnung.beleg
    html = angemeldet.get(reverse("dokumente:ablage")).content.decode()
    zeile = next(
        z for z in re.findall(r"<tr>.*?</tr>", html, re.S) if f"Dokument #{beleg.dokument_id}<" in z
    )
    assert "gelesen-zusatz" not in zeile


@pytest.mark.django_db
def test_gelesene_werte_aus_dem_cache(
    testhof: Testhof, angemeldet: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review 3, Punkt 7: Gelesen wird beim Anzeigen, mit Cache: Das zweite Mal liest
    niemand das PDF; ein neuer Lieferant gilt sofort (Stand der Lieferanten im Schlüssel)."""
    cache.clear()
    beleg = _neuer_beleg(angemeldet, "12.30")
    gelesen: list[Any] = []
    echt = textschicht.angaben_aus_pdf

    def zaehlen(*args: Any, **kwargs: Any) -> textschicht.Angaben:
        gelesen.append(1)
        return echt(*args, **kwargs)

    monkeypatch.setattr(textschicht, "angaben_aus_pdf", zaehlen)
    erst = textschicht.angaben_gemerkt([beleg.dokument])[beleg.dokument_id]
    dann = textschicht.angaben_gemerkt([beleg.dokument])[beleg.dokument_id]
    assert erst == dann and erst.betrag == Decimal("12.30")
    assert erst.lieferant is None and erst.lieferant_name == "Gelesen Beispiel (erfunden)"
    assert len(gelesen) == 1

    neu = services.lieferant_anlegen("Gelesen Beispiel (erfunden)")
    danach = textschicht.angaben_gemerkt([beleg.dokument])[beleg.dokument_id]
    assert len(gelesen) == 2
    assert danach.lieferant == neu


#: Richtungswörter, die am Handy nicht stimmen, weil die Felder dort untereinander stehen.
RICHTUNGEN = ("rechts", "links", "daneben", "darunter", "darüber", "oben", "unten")


def test_hinweis_zum_neuen_lieferanten_ohne_richtung(testhof: Testhof, angemeldet: Client) -> None:
    """Review 3, Nachtrag: Der Hinweis am Lieferanten nennt keine Richtung (am Handy steht das
    Feld für den neuen Lieferanten darunter, nicht rechts), sondern die Beschriftung des
    Feldes, so wie sie auf der Seite steht."""
    beleg = Beleg.objects.get(pk=testhof.belege.offen.pk)
    html = angemeldet.get(reverse("dokumente:beleg", args=[beleg.pk])).content.decode()
    hinweis = re.search(r'<p class="feld-hilfe" id="id_lieferant-hilfe">([^<]*)</p>', html)
    assert hinweis is not None
    beschriftung = re.search(r'<label class="feld-label" for="id_lieferant_neu">([^<]*)<', html)
    assert beschriftung is not None
    text = hinweis.group(1)
    assert f"„{beschriftung.group(1).strip()}“" in text, text
    assert not [w for w in RICHTUNGEN if re.search(rf"\b{w}\b", text, re.I)], text
