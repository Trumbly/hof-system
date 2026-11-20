"""Apple-Design (PR G): die einzige Gestaltung, ohne Schalter.

Es gibt weder Designschalter noch ``?design=`` noch Cookie noch die Einstellung
``HOF_DESIGN_STANDARD``, auch nicht mit ``DEBUG``. base.html lädt immer werte.css,
bausteine.css und die Dateien unter css/apple/ in fester Reihenfolge, ohne ``@import``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.contrib.staticfiles import finders
from django.test import Client
from django.urls import reverse

WURZEL = Path(__file__).resolve().parents[2]
APPLE_HTML = '<html lang="de" data-design="apple">'

#: Reihenfolge der Stylesheets in base.html: Grundlage, dann Apple-Design.
STYLESHEETS = (
    "css/werte.css",
    "css/bausteine.css",
    "css/apple/basis.css",
    "css/apple/bausteine.css",
    "css/apple/arbeitslisten.css",
    "css/apple/stammdaten.css",
    "css/apple/geld.css",
    "css/apple/dokumente.css",
    "css/apple/system.css",
)


def _seite(client: Client, pfad: str = "") -> str:
    return client.get(reverse("kern:startseite") + pfad).content.decode()


@pytest.mark.parametrize("debug", [False, True])
def test_kein_schalter_auch_nicht_mit_debug(
    angemeldet: Client, settings: object, debug: bool
) -> None:
    """Apple-Design (PR G): Kein Schalter, auch nicht bei DEBUG; ?design=standard wirkt
    nicht, und es entsteht kein Cookie."""
    settings.DEBUG = debug  # type: ignore[attr-defined]
    antwort = angemeldet.get(reverse("kern:startseite") + "?design=standard")
    html = antwort.content.decode()
    assert APPLE_HTML in html
    assert "css/apple/basis.css" in html
    assert "design-schalter" not in html
    assert "?design=" not in html
    assert "hof_design" not in antwort.cookies


def test_cookie_aus_dem_experiment_wirkt_nicht(angemeldet: Client, settings: object) -> None:
    """Apple-Design (PR G): Ein altes Cookie „hof_design=standard“ ändert nichts."""
    settings.DEBUG = True  # type: ignore[attr-defined]
    angemeldet.cookies["hof_design"] = "standard"
    html = _seite(angemeldet)
    assert APPLE_HTML in html
    assert "css/apple/system.css" in html


def test_ohne_anmeldung_auch_apple(client: Client, db: None) -> None:
    """Apple-Design (PR G): Auch die Anmeldung trägt das Apple-Design."""
    html = client.get(reverse("account_login") + "?design=standard").content.decode()
    assert APPLE_HTML in html
    assert "css/apple/basis.css" in html


def test_kein_schalter_mehr_im_code() -> None:
    """Apple-Design (PR G): Modul, Vorlage, Middleware, Context Processor und Einstellung des
    Schalters sind weg."""
    from django.conf import settings

    assert not (WURZEL / "apps" / "kern" / "design.py").exists()
    assert not (WURZEL / "templates" / "bausteine" / "design_schalter.html").exists()
    assert not any("design" in m.lower() for m in settings.MIDDLEWARE)
    prozessoren = settings.TEMPLATES[0]["OPTIONS"]["context_processors"]
    assert not any("design" in p for p in prozessoren)
    assert not hasattr(settings, "HOF_DESIGN_STANDARD")
    for vorlage in (WURZEL / "templates").rglob("*.html"):
        assert "hof_design" not in vorlage.read_text(encoding="utf-8"), vorlage


@pytest.mark.parametrize("modul", ["hof.settings.dev", "hof.settings.e2e", "hof.settings.prod"])
def test_keine_einstellung_je_umgebung(modul: str) -> None:
    """Apple-Design (PR G): In keiner Umgebung gibt es HOF_DESIGN_STANDARD, auch wenn die
    Umgebung sie setzt; prod läuft ohne DEBUG."""
    env = dict(os.environ)
    env.update(
        DJANGO_SETTINGS_MODULE=modul,
        DJANGO_SECRET_KEY="nur-fuer-den-test",
        DJANGO_ALLOWED_HOSTS="hof.invalid",
        DATABASE_URL="postgres://hof:hof@localhost:5432/hof_nicht_benutzt",
        EMAIL_URL="smtp://localhost:1025",
        HOF_DESIGN_STANDARD="standard",
    )
    ergebnis = subprocess.run(
        [
            sys.executable,
            "-c",
            "import django; django.setup(); from django.conf import settings; "
            "print(hasattr(settings, 'HOF_DESIGN_STANDARD'), settings.DEBUG)",
        ],
        cwd=WURZEL,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    vorhanden, debug = ergebnis.stdout.split()
    assert vorhanden == "False"
    if modul == "hof.settings.prod":
        assert debug == "False"


def test_alle_stylesheets_in_fester_reihenfolge(angemeldet: Client) -> None:
    """Apple-Design (PR A, G): base.html lädt werte.css, bausteine.css, dann basis, bausteine
    und die fünf Bereichsdateien, in dieser Reihenfolge, ohne @import. Jede Datei gibt es,
    und es gibt keine weitere."""
    html = _seite(angemeldet)
    stellen = []
    for datei in STYLESHEETS:
        assert html.count(f"/static/{datei}") == 1, datei
        stellen.append(html.index(f"/static/{datei}"))
        pfad = finders.find(datei)
        assert pfad, datei
        zeilen = Path(pfad).read_text(encoding="utf-8").splitlines()
        assert not [z for z in zeilen if z.lstrip().startswith("@import")], datei
    assert stellen == sorted(stellen)
    assert html.count('rel="stylesheet"') == len(STYLESHEETS)
    vorhanden = {
        str(p.relative_to(WURZEL / "static")) for p in (WURZEL / "static" / "css").rglob("*.css")
    }
    assert vorhanden == set(STYLESHEETS)


def test_bereichsdateien_nennen_die_regel() -> None:
    """Apple-Design (PR A): Jede Bereichsdatei sagt im Kopf, dass Gemeinsames in die
    Bausteine gehört; apple/bausteine.css nennt die Regeln für die Bereiche."""
    for datei in STYLESHEETS[4:]:
        text = Path(finders.find(datei)).read_text(encoding="utf-8")
        assert "apple/bausteine.css" in text, datei
    bausteine = Path(finders.find("css/apple/bausteine.css")).read_text(encoding="utf-8")
    assert "Was mehr als ein Bereich braucht" in bausteine


def test_jede_apple_regel_haengt_am_attribut() -> None:
    """Apple-Design (PR G): Jede Regel unter css/apple/ beginnt mit [data-design="apple"]
    (das Attribut an <html> ist fest und hebt sie über die Grundlage)."""
    import re

    for datei in STYLESHEETS[2:]:
        css = Path(finders.find(datei)).read_text(encoding="utf-8")
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        for kopf in re.findall(r"([^{};]+)\{", css):
            kopf = kopf.strip()
            if kopf.startswith("@") or re.fullmatch(r"(from|to|\d+%)", kopf):
                continue
            for sel in kopf.split(","):
                sel = sel.strip()
                if not sel or sel.startswith(":not(") or sel.startswith("::"):
                    continue
                assert sel.startswith(('[data-design="apple"]', ':root[data-design="apple"]')), (
                    datei,
                    sel,
                )
