"""Apple-Design: Designschalter, Cookie, Voreinstellung und die Stylesheets.

Der Schalter erscheint nur mit ``DEBUG``; ohne ``DEBUG`` wirken weder ``?design=`` noch das
Cookie (apps.kern.design). In dev und E2E ist „apple“ voreingestellt, in Tests und prod
„standard“ (``HOF_DESIGN_STANDARD``).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.test import Client
from django.urls import reverse

from apps.kern import design

WURZEL = Path(__file__).resolve().parents[2]
APPLE_CSS = "css/apple/basis.css"
APPLE_HTML = 'data-design="apple"'


def _seite(client: Client, pfad: str = "") -> str:
    return client.get(reverse("kern:startseite") + pfad).content.decode()


def test_ohne_debug_kein_schalter_und_parameter_wirkt_nicht(
    angemeldet: Client, settings: object
) -> None:
    """Apple-Design: Mit DEBUG = False gibt es keinen Schalter, und ?design=apple
    wirkt nicht (auch kein Cookie)."""
    settings.DEBUG = False  # type: ignore[attr-defined]
    antwort = angemeldet.get(reverse("kern:startseite") + "?design=apple")
    html = antwort.content.decode()
    assert "design-schalter" not in html
    assert APPLE_HTML not in html
    assert APPLE_CSS not in html
    assert design.COOKIE not in antwort.cookies


def test_ohne_debug_wirkt_das_cookie_nicht(angemeldet: Client, settings: object) -> None:
    """Apple-Design: Ein Cookie aus dev ändert ohne DEBUG nichts."""
    settings.DEBUG = False  # type: ignore[attr-defined]
    angemeldet.cookies[design.COOKIE] = "apple"
    assert APPLE_HTML not in _seite(angemeldet)


def test_mit_debug_schalter_in_seitenleiste_und_mehr(angemeldet: Client, settings: object) -> None:
    """Apple-Design: Mit DEBUG steht der Schalter „Design: Standard · Apple“
    unten in der Seitenleiste und im Blatt „Mehr“ (Handy)."""
    settings.DEBUG = True  # type: ignore[attr-defined]
    html = _seite(angemeldet)
    assert html.count('class="design-schalter"') == 2
    assert "?design=standard" in html
    assert "?design=apple" in html


def test_mit_debug_parameter_setzt_design_und_cookie(angemeldet: Client, settings: object) -> None:
    """Apple-Design: ?design=apple schaltet um und merkt die Wahl im Cookie;
    ?design=standard schaltet zurück."""
    settings.DEBUG = True  # type: ignore[attr-defined]
    settings.HOF_DESIGN_STANDARD = "standard"  # type: ignore[attr-defined]
    assert APPLE_HTML not in _seite(angemeldet)

    antwort = angemeldet.get(reverse("kern:startseite") + "?design=apple")
    html = antwort.content.decode()
    assert APPLE_HTML in html
    assert APPLE_CSS in html
    assert antwort.cookies[design.COOKIE].value == "apple"
    assert 'href="/?design=apple" aria-current="true"' in html

    # Die Wahl bleibt ohne Parameter (Cookie), auch auf anderen Seiten.
    assert APPLE_HTML in angemeldet.get(reverse("kern:arbeitslisten")).content.decode()

    antwort = angemeldet.get(reverse("kern:startseite") + "?design=standard")
    assert APPLE_HTML not in antwort.content.decode()
    assert antwort.cookies[design.COOKIE].value == "standard"
    assert APPLE_HTML not in _seite(angemeldet)


def test_unbekanntes_design_bleibt_bei_der_voreinstellung(
    angemeldet: Client, settings: object
) -> None:
    settings.DEBUG = True  # type: ignore[attr-defined]
    settings.HOF_DESIGN_STANDARD = "apple"  # type: ignore[attr-defined]
    antwort = angemeldet.get(reverse("kern:startseite") + "?design=gibtsnicht")
    assert APPLE_HTML in antwort.content.decode()
    assert design.COOKIE not in antwort.cookies


def test_voreinstellung_ohne_debug(angemeldet: Client, settings: object) -> None:
    """Apple-Design: HOF_DESIGN_STANDARD gilt auch ohne DEBUG (so laufen die
    E2E-Tests im Apple-Design), nur ohne Schalter."""
    settings.DEBUG = False  # type: ignore[attr-defined]
    settings.HOF_DESIGN_STANDARD = "apple"  # type: ignore[attr-defined]
    html = _seite(angemeldet)
    assert APPLE_HTML in html
    assert "design-schalter" not in html


def test_standard_design_unveraendert(angemeldet: Client) -> None:
    """Apple-Design: In Tests gilt „standard“; das Gerüst lädt nur die
    bisherigen Stylesheets."""
    html = _seite(angemeldet)
    assert '<html lang="de">' in html
    assert APPLE_CSS not in html
    assert 'placeholder="Suchen …"' in html


@pytest.mark.parametrize(
    ("modul", "umgebung", "erwartet"),
    [
        ("hof.settings.dev", {}, "apple"),
        ("hof.settings.dev", {"HOF_DESIGN_STANDARD": "standard"}, "standard"),
        ("hof.settings.e2e", {}, "apple"),
        ("hof.settings.e2e", {"HOF_DESIGN_STANDARD": "standard"}, "standard"),
        ("hof.settings.test", {}, "standard"),
        ("hof.settings.prod", {"HOF_DESIGN_STANDARD": "apple"}, "standard"),
    ],
)
def test_voreinstellung_je_umgebung(modul: str, umgebung: dict[str, str], erwartet: str) -> None:
    """Apple-Design: In dev und E2E ist „apple“ voreingestellt, in Tests und prod „standard“
    (prod auch dann, wenn die Umgebung etwas anderes sagt)."""
    env = {k: v for k, v in os.environ.items() if k != "HOF_DESIGN_STANDARD"}
    env.update(
        DJANGO_SETTINGS_MODULE=modul,
        DJANGO_SECRET_KEY="nur-fuer-den-test",
        DATABASE_URL="postgres://hof:hof@localhost:5432/hof_nicht_benutzt",
        EMAIL_URL="smtp://localhost:1025",
        **umgebung,
    )
    ergebnis = subprocess.run(
        [
            sys.executable,
            "-c",
            "import django; django.setup(); from django.conf import settings; "
            "print(settings.HOF_DESIGN_STANDARD, settings.DEBUG)",
        ],
        cwd=WURZEL,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert ergebnis.stdout.split()[0] == erwartet


def test_apple_laedt_alle_stylesheets_in_fester_reihenfolge(
    angemeldet: Client, settings: object
) -> None:
    """Apple-Design (PR A): base.html lädt nach bausteine.css basis, bausteine und die fünf
    Bereichsdateien, in dieser Reihenfolge, ohne @import. Jede Datei gibt es."""
    from django.contrib.staticfiles import finders

    settings.HOF_DESIGN_STANDARD = "apple"  # type: ignore[attr-defined]
    html = _seite(angemeldet)
    stellen = [html.index("css/bausteine.css")]
    for datei in design.APPLE_CSS:
        assert datei in html, datei
        stellen.append(html.index(datei))
        pfad = finders.find(datei)
        assert pfad, datei
        zeilen = Path(pfad).read_text(encoding="utf-8").splitlines()
        assert not [z for z in zeilen if z.lstrip().startswith("@import")], datei
    assert stellen == sorted(stellen)
    assert [d.rsplit("/", 1)[1] for d in design.APPLE_CSS] == [
        "basis.css",
        "bausteine.css",
        "arbeitslisten.css",
        "stammdaten.css",
        "geld.css",
        "dokumente.css",
        "system.css",
    ]


def test_bereichsdateien_nennen_die_regel(settings: object) -> None:
    """Apple-Design (PR A): Jede Bereichsdatei sagt im Kopf, dass Gemeinsames in die
    Bausteine gehört; apple/bausteine.css nennt die Regeln für die Bereiche."""
    from django.contrib.staticfiles import finders

    for datei in design.APPLE_CSS[2:]:
        text = Path(finders.find(datei)).read_text(encoding="utf-8")
        assert "apple/bausteine.css" in text, datei
    bausteine = Path(finders.find("css/apple/bausteine.css")).read_text(encoding="utf-8")
    assert "Was mehr als ein Bereich braucht" in bausteine
