"""Vertragsseite in der neuen Gestaltung (Review 2, B7 und C; PR R2-4)."""

from __future__ import annotations

import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.test import Client
from django.urls import reverse

from apps.abgleich import services as abgleich
from apps.kern import zahlen
from apps.stammdaten import vertragsseite
from apps.stammdaten.models import Vertrag

pytestmark = pytest.mark.django_db
D = datetime.date
E = Decimal
HTMX = {"HTTP_HX_REQUEST": "true"}


def _url(vertrag: Vertrag) -> str:
    return reverse("stammdaten:vertrag", args=[vertrag.pk])


def _kopf(html: str) -> str:
    return html.split('id="vertrag-kopf"', 1)[1].split('id="vertrag-reiter"', 1)[0]


def test_kopf_zeigt_mieter_und_saldo(aktiver_vertrag: Vertrag, angemeldet: Client) -> None:
    """Review 2, B7: Der Kopf zeigt V-Nummer, Status, Einheit, Mieter mit Kontakt, Miete
    heute, Saldo und nächste Frist; rechts die Aktionen."""
    html = angemeldet.get(_url(aktiver_vertrag)).content.decode()
    kopf = _kopf(html)
    einheit = aktiver_vertrag.einheiten.get()
    konto = abgleich.mietkonto(aktiver_vertrag)
    assert f"<h1>{aktiver_vertrag.nummer} " in kopf
    assert '<span class="badge badge-ok">aktiv</span>' in kopf
    assert einheit.nw_id in kopf
    assert "Erika Beispiel" in kopf and 'href="mailto:beispiel@testhof.invalid"' in kopf
    assert "Miete heute" in kopf and "85,00\u00a0€" in kopf
    assert "Saldo" in kopf and zahlen.euro(abs(konto.saldo)) in kopf
    assert ("offen" if konto.saldo < 0 else "Guthaben" if konto.saldo > 0 else "ausgeglichen") in (
        kopf
    )
    assert "Nächste Frist" in kopf
    for aktion in (
        "Miete ändern",
        "Kündigung erfassen",
        "Rückgabe am Handy",
        "Schreiben erstellen",
        "mehr ▾",
    ):
        assert aktion in kopf, aktion
    # Keine dauerhaft offenen Formulare mehr auf der Seite
    assert 'name="kondition-miete"' not in html


@pytest.mark.parametrize("reiter", ["uebersicht", "mietkonto", "dokumente", "mahnwesen", "verlauf"])
def test_reiter(reiter: str, aktiver_vertrag: Vertrag, angemeldet: Client) -> None:
    """Review 2, B7: Reiter Übersicht · Mietkonto · Dokumente und Schreiben · Mahnwesen ·
    Verlauf; jeder Reiter lässt sich direkt aufrufen und per HTMX nachladen."""
    url = f"{_url(aktiver_vertrag)}?reiter={reiter}"
    html = angemeldet.get(url).content.decode()
    for titel in ("Übersicht", "Mietkonto", "Dokumente und Schreiben", "Mahnwesen", "Verlauf"):
        assert f">{titel}</a>" in html
    teil = angemeldet.get(url, HTTP_HX_TARGET="vertrag-reiter", **HTMX).content.decode()
    assert teil.lstrip().startswith('<div id="vertrag-reiter"')
    assert "<html" not in teil


def test_miete_aendern_als_dialog(aktiver_vertrag: Vertrag, angemeldet: Client) -> None:
    """Review 2, B7: „Miete ändern“ ist ein Dialog, vorausgefüllt mit der Miete von heute.
    Speichern ersetzt Kopf und Reiter (die Seite springt nicht) und meldet mit Link."""
    url = _url(aktiver_vertrag)
    dialog = angemeldet.get(f"{url}?dialog=kondition", **HTMX).content.decode()
    assert "Miete ändern" in dialog and f'hx-post="{url}"' in dialog
    assert 'value="85.00"' in dialog and 'inputmode="decimal"' in dialog

    antwort = angemeldet.post(
        url,
        {
            "aktion": "kondition",
            "kondition-gueltig_ab": "2026-10-01",
            "kondition-miete": "99.00",
            "kondition-zahlweise": "monatlich",
        },
        **HTMX,
    )
    html = antwort.content.decode()
    assert antwort.status_code == 200
    assert antwort["HX-Trigger"] == '{"dialogSchliessen": true}'
    assert '<div id="vertrag-kopf" hx-swap-oob="true">' in html
    assert '<div id="vertrag-reiter" hx-swap-oob="true">' in html
    assert "Neue Miete 99,00\u00a0€ ab 01.10.2026" in html
    assert f'href="{url}?reiter=mietkonto"' in html
    assert aktiver_vertrag.kondition_am(D(2026, 10, 1)).miete == E("99.00")  # type: ignore[union-attr]


def test_dialog_fehler_bleibt_im_dialog(aktiver_vertrag: Vertrag, angemeldet: Client) -> None:
    """Review 2, C3: Ein Fehler aus dem Service erscheint als Satz im Dialog; nichts wird
    ersetzt, der Dialog bleibt offen."""
    antwort = angemeldet.post(
        _url(aktiver_vertrag),
        {"aktion": "kondition", "kondition-gueltig_ab": "2020-01-01", "kondition-miete": "1"},
        **HTMX,
    )
    html = antwort.content.decode()
    assert antwort.status_code == 200 and "HX-Trigger" not in antwort
    assert "vor Vertragsbeginn" in html and 'class="formfehler"' in html
    assert 'hx-swap-oob="true"' not in html


def test_kuendigung_als_dialog_mit_rueckmeldung(
    aktiver_vertrag: Vertrag, angemeldet: Client
) -> None:
    """Review 2, B-Leitlinien: Nach der Kündigung sagt die Meldung, was passiert ist
    (Fristende, Bestätigung), und der Kopf zeigt den neuen Status."""
    url = _url(aktiver_vertrag)
    assert "Zugang am" in angemeldet.get(f"{url}?dialog=kuendigung", **HTMX).content.decode()
    antwort = angemeldet.post(
        url,
        {"aktion": "kuendigung", "kuendigung-zugang_am": "2026-11-03", "kuendigung-von": "mieter"},
        **HTMX,
    )
    html = antwort.content.decode()
    assert "Kündigung erfasst · Ende 31.01.2027" in html
    assert '<span class="badge badge-warnung">gekündigt</span>' in html
    assert "Fristende <strong>31.01.2027</strong>" in html


def test_unbekannter_dialog(aktiver_vertrag: Vertrag, angemeldet: Client) -> None:
    assert angemeldet.get(f"{_url(aktiver_vertrag)}?dialog=gibtsnicht").status_code == 404


def test_ohne_javascript_bleibt_das_formular_auf_der_seite(
    aktiver_vertrag: Vertrag, angemeldet: Client
) -> None:
    """Ohne JavaScript: Ein falsches Formular steht mit seinen Fehlern auf der Seite."""
    antwort = angemeldet.post(
        _url(aktiver_vertrag), {"aktion": "kondition", "kondition-gueltig_ab": "2026-10-01"}
    )
    html = antwort.content.decode()
    assert antwort.status_code == 200
    assert 'name="kondition-miete"' in html and 'class="feld-fehler"' in html


def test_mietkonto_mit_laufendem_saldo() -> None:
    """Review 2, B7: Mietkonto mit Sollposten und Zahlungen nach Datum und laufendem Saldo.
    Eine auf zwei Posten verteilte Zahlung ist eine Zeile; spätere Posten zählen nicht."""

    def posten(faellig: D, betrag: str, zuordnungen: list[SimpleNamespace]) -> SimpleNamespace:
        p = SimpleNamespace(
            faellig_am=faellig,
            betrag=E(betrag),
            status="offen",
            zeitraum_text=f"{faellig:%m/%Y}",
            get_art_display=lambda: "Miete",
        )
        return SimpleNamespace(posten=p, zuordnungen=zuordnungen)

    def bank(buchung: int, datum: D, betrag: str) -> SimpleNamespace:
        return SimpleNamespace(
            buchung_id=buchung, guthaben_id=None, handzahlung_id=None, datum=datum, betrag=E(betrag)
        )

    konto = SimpleNamespace(
        zeilen=[
            posten(D(2026, 9, 3), "85.00", [bank(1, D(2026, 9, 5), "85.00")]),
            posten(D(2026, 10, 3), "85.00", [bank(2, D(2026, 10, 20), "50.00")]),
            posten(D(2026, 11, 3), "85.00", [bank(2, D(2026, 10, 20), "20.00")]),
            posten(D(2026, 12, 3), "85.00", []),
        ],
        guthaben=[],
    )
    zeilen = vertragsseite.mietkonto_zeilen(konto, D(2026, 11, 19))
    assert [(z.datum, z.soll, z.zahlung, z.saldo, z.kuenftig) for z in zeilen] == [
        (D(2026, 9, 3), E("85.00"), None, E("85.00"), False),
        (D(2026, 9, 5), None, E("85.00"), E("0.00"), False),
        (D(2026, 10, 3), E("85.00"), None, E("85.00"), False),
        (D(2026, 10, 20), None, E("70.00"), E("15.00"), False),
        (D(2026, 11, 3), E("85.00"), None, E("100.00"), False),
        (D(2026, 12, 3), E("85.00"), None, None, True),
    ]


def test_mietkonto_reiter_auf_der_seite(aktiver_vertrag: Vertrag, angemeldet: Client) -> None:
    html = angemeldet.get(f"{_url(aktiver_vertrag)}?reiter=mietkonto").content.decode()
    assert '<th class="zahl">Saldo</th>' in html
    assert 'aria-current="page"' in html.split('class="reiter"', 1)[1].split("Dokumente", 1)[0]
