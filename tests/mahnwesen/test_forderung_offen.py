"""Review 2, A2: Zahlungserinnerung und Mahnstufen nennen im Text und in der Tabelle denselben
offenen Betrag. Die Tabelle zeigt je Posten Soll, bezahlt und offen; Grundlage ist allein
``soll.services.offen`` (Review 1, Punkt 1)."""

from __future__ import annotations

import datetime
import io
import re
from decimal import Decimal

import pytest

from apps.dokumente import platzhalter
from apps.dokumente import services as dokumente
from apps.dokumente.models import Schreiben
from apps.mahnwesen import services
from apps.mahnwesen.models import Mahnstufe
from apps.soll import services as soll
from apps.soll.models import Sollposten
from apps.stammdaten.models import Vertrag
from testhof import dokumente as testhof_dokumente
from testhof import mahnwesen as testhof_mahnwesen
from tests.mahnwesen.conftest import (
    FAELLIG_NOV,
    FAELLIG_OKT,
    PDF_DA,
    PDF_GRUND,
    Mahnhof,
    Vorlagen,
    fall_zu,
    lauf,
    letzter,
)

pytestmark = pytest.mark.django_db
D = datetime.date
TAG = datetime.timedelta(days=1)
#: Stichtag: fünf Tage nach der Novembermiete (Erinnerung für Oktober und November).
STICHTAG = FAELLIG_NOV + 5 * TAG


def _teilzahlung(vertrag: Vertrag, monat: datetime.date, betrag: str) -> Sollposten:
    posten = Sollposten.objects.get(vertrag=vertrag, art=Sollposten.Art.MIETE, von=monat)
    return soll.zahlungsstand_setzen(posten, Decimal(betrag))


def _euro(text: str) -> Decimal:
    return Decimal(text.replace(".", "").replace(",", "."))


def _zellen(zeile: str) -> list[str]:
    return [
        re.sub(r"<[^>]+>", "", z).replace("\xa0", " ").strip()
        for z in re.findall(r"<td[^>]*>.*?</td>", zeile)
    ]


def _tabelle(html: str) -> tuple[list[list[str]], list[str]]:
    """(Zeilen der Posten, Summenzeile) aus der Aufstellung eines Schreibens."""
    rumpf = re.search(r"<tbody>(.*?)</tbody>", html, re.S)
    fuss = re.search(r"<tfoot>(.*?)</tfoot>", html, re.S)
    assert rumpf and fuss, "Aufstellung ohne Posten oder Summe"
    zeilen = [_zellen(z) for z in re.findall(r"<tr>.*?</tr>", rumpf.group(1), re.S)]
    return zeilen, _zellen(fuss.group(1))


def _betrag_im_text(html: str) -> Decimal:
    treffer = re.search(r"Betrag von\s+([\d.]+,\d\d)\s*€", html) or re.search(
        r"zusammen\s+([\d.]+,\d\d)\s*€", html
    )
    assert treffer, "Kein Betrag im Text"
    return _euro(treffer.group(1))


def _mahnhof_mit_teilzahlung(mahnhof: Mahnhof) -> Vertrag:
    """Oktober zu 30,00 € von 85,00 € bezahlt, November offen; Bankdaten bis Ende November."""
    vertrag = mahnhof.vertrag()
    mahnhof.datenstand(D(2026, 11, 30))
    _teilzahlung(vertrag, D(2026, 10, 1), "30.00")
    return vertrag


def test_offen_liefert_soll_bezahlt_offen_und_summen(mahnhof: Mahnhof) -> None:
    """Review 2, A2 / Review 1, Punkt 1: ``soll.offen`` liefert je Posten Soll, bezahlt und
    offen, dazu die Summen; der Stichtag zählt nur, was vorher fällig war."""
    vertrag = _mahnhof_mit_teilzahlung(mahnhof)

    stand = soll.offen(vertrag, STICHTAG)

    assert [(p.soll, p.bezahlt, p.offen) for p in stand.posten] == [
        (Decimal("85.00"), Decimal("30.00"), Decimal("55.00")),
        (Decimal("85.00"), Decimal("0.00"), Decimal("85.00")),
    ]
    assert (stand.summe_soll, stand.summe_bezahlt, stand.summe_offen) == (
        Decimal("170.00"),
        Decimal("30.00"),
        Decimal("140.00"),
    )
    # Am Fälligkeitstag ist die Novembermiete noch nicht überfällig.
    assert soll.offen(vertrag, FAELLIG_NOV).summe_offen == Decimal("55.00")
    # Ohne Stichtag: alle offenen Posten, über alle Verträge.
    assert soll.offen().summe_offen >= stand.summe_offen
    assert stand.kontext()["summe"] == stand.summe_offen


def test_zahlungserinnerung_teilzahlung_text_gleich_tabelle(
    mahnhof: Mahnhof, vorlagen: Vorlagen
) -> None:
    """Review 2, A2: Zahlungserinnerung bei Teilzahlung und zwei Posten. Die Tabelle zeigt je
    Posten Soll, bezahlt und offen; die Summe der offenen Beträge ist der Betrag im Text und
    entspricht ``soll.offen``. Vorher zeigte die Tabelle das Soll (85,00 €) statt des Rests."""
    vertrag = _mahnhof_mit_teilzahlung(mahnhof)

    lauf(STICHTAG)

    schritt = letzter(vertrag)
    assert schritt.stufe.schluessel == "erinnerung"
    schreiben = schritt.schreiben
    assert schreiben is not None and schreiben.art == "zahlungserinnerung"
    html = schreiben.inhalt_html
    zeilen, summe = _tabelle(html)
    assert [z[2:] for z in zeilen] == [
        ["85,00 €", "30,00 €", "55,00 €"],
        ["85,00 €", "0,00 €", "85,00 €"],
    ]
    assert summe[1:] == ["170,00 €", "30,00 €", "140,00 €"]
    offen_tabelle = sum((_euro(z[4].removesuffix(" €")) for z in zeilen), Decimal("0"))
    assert _betrag_im_text(html) == offen_tabelle == Decimal("140.00")
    assert offen_tabelle == services.forderung(vertrag, STICHTAG).summe_offen


@pytest.mark.skipif(not PDF_DA, reason=f"WeasyPrint fehlt: {PDF_GRUND}")
def test_zahlungserinnerung_pdf_nennt_denselben_betrag(
    mahnhof: Mahnhof, vorlagen: Vorlagen
) -> None:
    """Review 2, A2: Im fertigen PDF der Zahlungserinnerung stehen Soll, bezahlt und offen je
    Posten, und der Betrag im Text ist die Summe der offenen Beträge."""
    import pypdf

    vertrag = _mahnhof_mit_teilzahlung(mahnhof)
    lauf(STICHTAG)
    schreiben = letzter(vertrag).schreiben
    assert schreiben is not None
    with schreiben.pdf.open("rb") as datei:
        text = " ".join(
            seite.extract_text() for seite in pypdf.PdfReader(io.BytesIO(datei.read())).pages
        )
    text = re.sub(r"\s+", " ", text)
    assert re.search(r"Betrag von 140,00 €", text)
    for wert in ("55,00 €", "30,00 €", "170,00 €"):
        assert wert in text


def _kontext(vertrag: Vertrag, stufe: Mahnstufe) -> dict[str, object]:
    fall = fall_zu(vertrag)
    lage = services.lage_von(vertrag, STICHTAG)
    kontext = services.kontext_mahnung(fall, stufe, lage, STICHTAG)
    return {"absender": dokumente.absender(), **kontext, "datum": STICHTAG, "zeichen": "S-1"}


@pytest.mark.parametrize("art", ["zahlungserinnerung", "mahnung", "abmahnung"])
def test_alle_mahnvorlagen_des_testhofs_text_gleich_tabelle(
    mahnhof: Mahnhof, vorlagen: Vorlagen, art: str
) -> None:
    """Review 2, A2: In allen Beispielvorlagen des Testhofs (Erinnerung, Mahnung, Abmahnung)
    zeigt die Tabelle Soll, bezahlt und offen, und die Summe der offenen Beträge ist der
    Betrag im Text."""
    vertrag = _mahnhof_mit_teilzahlung(mahnhof)
    lauf(STICHTAG)
    stufe = Mahnstufe.objects.filter(vorlagenart=art).order_by("pk").first()
    assert stufe is not None
    (text,) = [
        t for _, a, _, t in (*testhof_dokumente.VORLAGEN, *testhof_mahnwesen.VORLAGEN) if a == art
    ]

    html = platzhalter.rendern(text, _kontext(vertrag, stufe))

    zeilen, summe = _tabelle(html)
    assert len(zeilen) == 2
    offen = sum((_euro(z[4].removesuffix(" €")) for z in zeilen), Decimal("0"))
    assert _betrag_im_text(html) == offen == _euro(summe[3].removesuffix(" €"))
    assert offen == soll.offen(vertrag, services.pruefstichtag(STICHTAG)).summe_offen


def test_forderung_nur_bis_zum_bankdatenstand(mahnhof: Mahnhof, vorlagen: Vorlagen) -> None:
    """Review 2, A2: Das Schreiben nennt nur Posten, die das Mahnwesen prüfen kann (fällig vor
    dem Stichtag und spätestens am Datenstand der Bank), genau wie der Mahnlauf."""
    vertrag = mahnhof.vertrag()  # Bankdaten bis 31.10.: die Novembermiete ist nicht prüfbar
    lauf(FAELLIG_OKT + 5 * TAG)
    lauf(STICHTAG)

    stand = services.forderung(vertrag, STICHTAG)

    assert [p.sollposten.von for p in stand.posten] == [D(2026, 10, 1)]
    assert stand.summe_offen == services.lage_von(vertrag, STICHTAG).rueckstand
    assert Schreiben.objects.filter(art="zahlungserinnerung").count() == 1
