"""Kacheln der Startseite in einem Muster (Design-Review nachher, Befunde B und E; PR H).

Jede Kachel ist ``{% karte "Titel" zahl=… ziel=… %}``: Titel ohne Doppelpunkt und ohne Zahl,
die Zahl als Kapsel (``.zaehler``) im Kopf, darunter die Zeilen. „Objekte“ steht als Kachel im
Mauerwerk statt darunter.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.test import Client
from django.urls import reverse
from django.utils.html import strip_tags

from testhof.aufbau import Testhof

pytestmark = pytest.mark.django_db

WURZEL = Path(__file__).resolve().parents[2]
TITEL = re.compile(r'<h2 class="karte-titel">(.*?)</h2>', re.S)
ZAEHLER = re.compile(r'\s*<span class="zaehler">([^<]*)</span>')

#: Kachel → Titel und Zahl (als Kapsel) im Testhof; None: Kachel ohne eine Zahl für alles.
ERWARTET = {
    "belege": "Belege zuordnen",
    "rueckstaende": "Rückstände",
    "offene_posten": "Offene Posten",
    "mahnfaelle": "Mahnfälle",
    "eingangsrechnungen": "Eingangsrechnungen offen",
    "leerstand": "Leerstand",
    "datenstand": "Lücken im Datenstand",
    "freigaben": "Offene Freigaben",
    "objekte": "Objekte",
}
OHNE_ZAHL = {"fristen", "wohnraum", "laeufe", "sicherung", "protokoll"}


def _kacheln(html: str) -> dict[str, str]:
    raster = html.split('class="cockpit-raster', 1)[1]
    teile = re.split(r'<li id="kachel-([a-z_]+)"', raster)[1:]
    return dict(zip(teile[::2], teile[1::2], strict=True))


def test_kacheln_in_einem_muster(testhof: Testhof, angemeldet: Client) -> None:
    """Befund E: Jede Kachel hat genau einen Titel ohne Doppelpunkt und ohne Zahl; wo es eine
    Zahl gibt, steht sie als Kapsel im Kopf („Belege zuordnen“ statt „Belege zuordnen: 1“)."""
    kacheln = _kacheln(angemeldet.get(reverse("kern:startseite")).content.decode())
    assert set(ERWARTET) | OHNE_ZAHL <= set(kacheln)
    for name, html in kacheln.items():
        (titel,) = TITEL.findall(html)
        zahlen = ZAEHLER.findall(titel)
        text = strip_tags(ZAEHLER.sub("", titel)).strip()
        assert ":" not in text and not re.search(r"\d", text), (name, text)
        if name in ERWARTET:
            assert text == ERWARTET[name], name
            assert len(zahlen) == 1 and zahlen[0].isdigit(), (name, titel)
        if name in OHNE_ZAHL:
            assert not zahlen, name


def test_kachel_ist_ganz_ein_link(testhof: Testhof, angemeldet: Client) -> None:
    """Befund E: Die kleinen Kacheln sind als Ganzes ein Link auf ihre Liste (``a.karte-ziel``),
    wie vorher; die großen (Freigaben, Fristen) haben den Link im Kopf."""
    kacheln = _kacheln(angemeldet.get(reverse("kern:startseite")).content.decode())
    for name, ziel in (
        ("belege", reverse("dokumente:ablage")),
        ("mahnfaelle", reverse("mahnwesen:faelle")),
        ("laeufe", reverse("kern:laeufe")),
        ("protokoll", reverse("kern:protokoll")),
    ):
        assert (
            kacheln[name]
            .lstrip(">")
            .lstrip()
            .startswith(f'<a class="karte karte-ziel" href="{ziel}"')
        ), name


def test_kachel_vorlagen_nutzen_den_baustein() -> None:
    """Befund E: Alle Kachel-Vorlagen der Apps (``_kachel_*.html``) nutzen ``{% karte %}`` oder
    die Karte mit Kopf; das alte Markup (``class="kachel"``, ``<strong>`` als Titel) ist weg."""
    vorlagen = sorted((WURZEL / "templates").glob("*/_kachel_*.html"))
    assert len(vorlagen) >= 14
    for pfad in vorlagen:
        text = pfad.read_text()
        assert 'class="kachel"' not in text and "kachel kachel-" not in text, pfad.name
        assert "{% karte " in text or 'class="karte-titel"' in text, pfad.name


def test_objekte_im_mauerwerk(testhof: Testhof, angemeldet: Client) -> None:
    """Befund B: „Objekte“ ist eine Kachel im Raster mit Mauerwerk (letzte), keine eigene Karte
    darunter; die Objekte des Testhofs stehen darin."""
    html = angemeldet.get(reverse("kern:startseite")).content.decode()
    raster = html.split("data-mauerwerk>", 1)[1].split("</ul>\n</div>", 1)[0]
    kacheln = _kacheln(html)
    assert list(kacheln)[-1] == "objekte"
    assert 'id="objekte"' in raster
    for kuerzel in ("HOF", "HALLE", "FELD"):
        assert f"<strong>{kuerzel}</strong>" in kacheln["objekte"]
