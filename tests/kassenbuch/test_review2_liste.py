"""Review 2, B11: Kassenbuch mit zugeklappten Filtern, Spalten Gegenpartei und Zweck,
Kontonamen, 50 Zeilen mit „mehr laden“ und einer Unternavigation als Reiter."""

from __future__ import annotations

import datetime
import re

from django.test import Client
from django.urls import reverse

from tests.kassenbuch.conftest import KleinerHof, ganz, umsatz

LISTE = reverse("kassenbuch:buchungen")
JAN = datetime.date(2026, 1, 1)


def _buchungen(klein: KleinerHof, anzahl: int) -> None:
    for tag in range(anzahl):
        eintrag = umsatz(
            klein.konto,
            JAN + datetime.timedelta(days=tag % 28),
            f"{100 + tag}.00",
            name=f"Zahler {tag:02d} (erfunden)",
            zweck=f"Miete Garage {tag:02d}",
        )
        ganz(klein, eintrag)


def _zeilen(html: str) -> list[str]:
    return re.findall(r'<tr id="buchung-(\d+)"', html)


def test_filter_zugeklappt_spalten_und_kontonamen(angemeldet: Client, klein: KleinerHof) -> None:
    """Review 2, B11: Sichtbar sind nur Suche und Monat, die übrigen Filter stehen
    zugeklappt; Spalten Gegenpartei und Zweck; das Konto mit Namen statt „Bankkonto #3“."""
    _buchungen(klein, 3)
    html = angemeldet.get(LISTE, {"monat_0": "01", "monat_1": "2026"}).content.decode()

    assert 'name="q"' in html and 'name="monat_0"' in html
    weitere = re.search(r'<details class="aufklapper">\s*<summary>Weitere Filter', html)
    assert weitere is not None  # zugeklappt (ohne „open“)
    assert "<th>Gegenpartei</th><th>Zweck</th>" in html
    assert "Zahler 00 (erfunden)" in html and "Miete Garage 00" in html
    assert klein.konto.bezeichnung in html
    assert f"Bankkonto #{klein.konto.pk}" not in html
    assert len(_zeilen(html)) == 3


def test_suche_findet_gegenpartei_zweck_und_betrag(angemeldet: Client, klein: KleinerHof) -> None:
    """Review 2, B11: Die Suche findet Gegenpartei, Zweck und Betrag."""
    _buchungen(klein, 5)
    jahr = {"von": "2026-01-01", "bis": "2026-12-31"}
    assert len(_zeilen(angemeldet.get(LISTE, {**jahr, "q": "Zahler 03"}).content.decode())) == 1
    assert len(_zeilen(angemeldet.get(LISTE, {**jahr, "q": "garage 04"}).content.decode())) == 1
    assert len(_zeilen(angemeldet.get(LISTE, {**jahr, "q": "102,00"}).content.decode())) == 1


def test_50_zeilen_und_mehr_laden(angemeldet: Client, klein: KleinerHof) -> None:
    """Review 2, B11: 50 Zeilen, dann „mehr laden“ (HTMX hängt die nächsten an), statt
    Seiten zu 100."""
    _buchungen(klein, 55)
    parameter = {"von": "2026-01-01", "bis": "2026-12-31"}
    html = angemeldet.get(LISTE, parameter).content.decode()
    assert len(_zeilen(html)) == 50
    mehr = re.search(r'hx-get="(\?[^"]*ab=50[^"]*)"', html)
    assert mehr is not None and "mehr laden" in html

    teil = angemeldet.get(
        LISTE + mehr.group(1).replace("&amp;", "&"), HTTP_HX_REQUEST="true"
    ).content.decode()
    assert len(_zeilen(teil)) == 5
    assert "<html" not in teil and "mehr laden" not in teil
    assert not set(_zeilen(teil)) & set(_zeilen(html))


def test_unternavigation_als_reiter(angemeldet: Client, klein: KleinerHof) -> None:
    """Review 2, B11: Die Unternavigation ist eine Zeile Reiter (kein Raster mit losen „·“),
    auch auf Regeln, Abschlüssen und dem Monatsbericht."""
    for name in ("kassenbuch:buchungen", "kassenbuch:regeln", "kassenbuch:bericht"):
        html = angemeldet.get(reverse(name)).content.decode()
        nav = re.search(r'<nav class="reiter[^"]*" aria-label="Kassenbuch">(.*?)</nav>', html, re.S)
        assert nav is not None, name
        assert "·" not in nav.group(1)
        assert f'href="{reverse(name)}" aria-current="page"' in nav.group(1)
