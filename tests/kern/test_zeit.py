"""Eine Uhr (Review 3, Punkt 1): ``apps.kern.zeit`` und der Wächter gegen zweite Uhren."""

from __future__ import annotations

import datetime
import io
import json
import os
import re
import subprocess
import sys
import tokenize
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django.conf import settings

from apps.kern import zeit
from apps.kern.laeufe import LaufBefehl
from apps.kern.models import Lauf
from testhof.aufbau import STICHTAG, ZEITPUNKT

WURZEL = Path(__file__).resolve().parents[2]
BERLIN = ZoneInfo("Europe/Berlin")

# --- Wächter -------------------------------------------------------------------------

#: Ordner, in denen fachlicher Code liegt. Tests und Migrationen gehören nicht dazu.
GEPRUEFT = ("apps", "hof", "testhof")
#: Die eine Uhr selbst.
AUSGENOMMEN = {"apps/kern/zeit.py"}
#: Kennzeichen für Stellen, die absichtlich echte Zeit nehmen (technische Zeitstempel).
MARKER = "zeit: echt"

#: Aufrufe, die nach dem heutigen Tag oder der Uhrzeit fragen, an ``kern.zeit`` vorbei.
#: Geprüft wird nur Code, nicht Kommentare und Texte.
VERBOTEN = re.compile(
    r"\bdate\.today\b"
    r"|\bdatetime\.(?:utc)?now\b"
    r"|\btimezone\.now\b"
    r"|\blocal(?:date|time)\(\)"
    r"|\btimezone\.local(?:date|time)\b(?!\()"
    r"|\bimport[^;]*\b(?:now|localdate|localtime)\b"
)


def _code_je_zeile(quelltext: str) -> tuple[dict[int, str], dict[int, str]]:
    """Code (ohne Texte und Kommentare) und Kommentare je Zeilennummer."""
    code: dict[int, str] = {}
    kommentare: dict[int, str] = {}
    ohne = {
        tokenize.STRING,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.FSTRING_MIDDLE,
    }
    for token in tokenize.generate_tokens(io.StringIO(quelltext).readline):
        zeile = token.start[0]
        if token.type == tokenize.COMMENT:
            kommentare[zeile] = kommentare.get(zeile, "") + token.string
        elif token.type not in ohne:
            code[zeile] = code.get(zeile, "") + token.string + " "
    # „timezone . now ( )“ → „timezone.now()“
    return {n: re.sub(r"\s*([.()])\s*", r"\1", z) for n, z in code.items()}, kommentare


def fundstellen(quelltext: str, name: str = "<code>") -> list[str]:
    """Verbotene Aufrufe in ``quelltext`` als „Datei:Zeile: Code“.

    Erlaubt ist eine Stelle mit dem Marker ``# zeit: echt`` in derselben oder der Zeile
    direkt darüber (dort steht die Begründung).
    """
    code, kommentare = _code_je_zeile(quelltext)
    zeilen = quelltext.splitlines()
    treffer = []
    for nummer, text in sorted(code.items()):
        if not VERBOTEN.search(text):
            continue
        if MARKER in kommentare.get(nummer, "") or MARKER in kommentare.get(nummer - 1, ""):
            continue
        treffer.append(f"{name}:{nummer}: {zeilen[nummer - 1].strip()}")
    return treffer


def _dateien() -> list[Path]:
    dateien = []
    for ordner in GEPRUEFT:
        for pfad in sorted((WURZEL / ordner).rglob("*.py")):
            relativ = pfad.relative_to(WURZEL).as_posix()
            if "migrations" in pfad.parts or relativ in AUSGENOMMEN:
                continue
            dateien.append(pfad)
    return dateien


def test_waechter_keine_zweite_uhr() -> None:
    """Review 3, Punkt 1: Außerhalb von ``kern.zeit`` fragt kein Code die echte Uhr nach dem
    heutigen Tag (``date.today()``, ``timezone.now()``, ``timezone.localdate()`` …). Ausnahmen
    tragen ``# zeit: echt`` mit Begründung (technische Zeitstempel)."""
    treffer = []
    for pfad in _dateien():
        treffer += fundstellen(
            pfad.read_text(encoding="utf-8"), pfad.relative_to(WURZEL).as_posix()
        )
    assert not treffer, "Bitte apps.kern.zeit.heute()/jetzt() nehmen:\n" + "\n".join(treffer)


@pytest.mark.parametrize(
    "zeile",
    [
        "x = datetime.date.today()",
        "x = date.today()",
        "x = timezone.now()",
        "x = timezone.localdate()",
        "x = timezone.localtime()",
        "x = datetime.datetime.now(tz)",
        "x = datetime.now()",
        "feld = models.DateTimeField(default=timezone.now)",
        "feld = forms.DateField(initial=timezone.localdate)",
        "from django.utils.timezone import now",
        "x = f'{datetime.date.today():%Y}'",
    ],
)
def test_waechter_findet_verbotene_aufrufe(zeile: str) -> None:
    """Review 3, Punkt 1: Der Wächter erkennt jede Form der zweiten Uhr mit Datei und Zeile."""
    assert fundstellen(f"import datetime\n{zeile}\n", "beispiel.py") == [f"beispiel.py:2: {zeile}"]


@pytest.mark.parametrize(
    "quelltext",
    [
        "x = timezone.now()  # zeit: echt (Laufprotokoll)\n",
        "# zeit: echt (Ablauf für die Bank)\nx = timezone.now()\n",
        '"""Nicht timezone.now() nehmen, sondern zeit.jetzt()."""\n',
        "# früher: datetime.date.today()\n",
        "x = timezone.localdate(abruf.gestartet_am)\n",
        "x = timezone.localtime(ereignis.zeit)\n",
    ],
)
def test_waechter_laesst_ausnahmen_und_umrechnungen_zu(quelltext: str) -> None:
    """Review 3, Punkt 1: Markierte Ausnahmen, Texte, Kommentare und Umrechnungen eines
    gegebenen Zeitpunkts sind keine zweite Uhr."""
    assert fundstellen(quelltext) == []


# --- heute() und jetzt() -------------------------------------------------------------


def test_heute_in_test_ist_der_stichtag() -> None:
    """Review 3, Punkt 1: In dev und test liefert ``heute()`` den Stichtag des Testhofs,
    ``jetzt()`` denselben Tag in Europe/Berlin."""
    assert settings.HOF_STICHTAG == STICHTAG
    assert zeit.heute() == STICHTAG
    jetzt = zeit.jetzt()
    assert jetzt.date() == STICHTAG
    assert jetzt.tzinfo is not None
    assert jetzt.utcoffset() == datetime.datetime(2026, 11, 1, 12, tzinfo=BERLIN).utcoffset()


def test_jetzt_traegt_die_echte_uhrzeit() -> None:
    """Review 3, Punkt 1: ``jetzt()`` ist der Stichtag mit der echten Uhrzeit, damit Aktionen
    eines Tages in ihrer Reihenfolge bleiben."""
    vorher = datetime.datetime.now(BERLIN)  # zeit: echt (Vergleich im Test)
    jetzt = zeit.jetzt()
    nachher = datetime.datetime.now(BERLIN)  # zeit: echt (Vergleich im Test)
    assert vorher.time() <= jetzt.time() <= nachher.time() or vorher.date() != nachher.date()


def test_uhr_stellen_mit_tag_und_zeitpunkt_und_verschachtelt() -> None:
    """Review 3, Punkt 1: Tests und Läufe stellen die Uhr; danach gilt wieder der Stichtag."""
    with zeit.uhr_stellen(datetime.date(2027, 3, 15)):
        assert zeit.heute() == datetime.date(2027, 3, 15)
        assert zeit.jetzt().date() == datetime.date(2027, 3, 15)
        with zeit.uhr_stellen(ZEITPUNKT):
            assert zeit.heute() == STICHTAG
            assert zeit.jetzt() == ZEITPUNKT
        with zeit.uhr_stellen(datetime.datetime(2027, 1, 2, 9, 30)):
            assert zeit.jetzt() == datetime.datetime(2027, 1, 2, 9, 30, tzinfo=BERLIN)
        with zeit.uhr_stellen(None):
            assert zeit.heute() == datetime.date(2027, 3, 15)
        assert zeit.heute() == datetime.date(2027, 3, 15)
    assert zeit.heute() == STICHTAG


def test_fixture_uhr(uhr: Any) -> None:
    """Review 3, Punkt 1: Die Fixture ``uhr`` stellt die Uhr bis zum Ende des Tests."""
    uhr.stellen(datetime.date(2026, 12, 24))
    assert zeit.heute() == datetime.date(2026, 12, 24)
    uhr.stellen(datetime.date(2026, 12, 31))
    assert zeit.heute() == datetime.date(2026, 12, 31)


def test_fixture_uhr_ist_danach_zurueck() -> None:
    """Review 3, Punkt 1: Nach einem Test mit ``uhr`` gilt wieder der Stichtag."""
    assert zeit.heute() == STICHTAG


PROD_SKRIPT = """
import json, datetime, django
from zoneinfo import ZoneInfo
django.setup()
from django.conf import settings
from apps.kern import zeit
vorher = datetime.datetime.now(ZoneInfo("Europe/Berlin")).date()
heute = zeit.heute()
jetzt = zeit.jetzt()
nachher = datetime.datetime.now(ZoneInfo("Europe/Berlin")).date()
print(json.dumps({
    "stichtag": str(settings.HOF_STICHTAG),
    "heute": heute.isoformat(),
    "jetzt": jetzt.isoformat(),
    "echt": sorted({vorher.isoformat(), nachher.isoformat()}),
}))
"""


def test_heute_in_prod_ist_das_echte_datum() -> None:
    """Review 3, Punkt 1: In prod (``HOF_STICHTAG`` = None, auch wenn die Umgebung einen
    setzt) liefern ``heute()`` und ``jetzt()`` das echte Datum."""
    umgebung = {
        k: v for k, v in os.environ.items() if not k.startswith(("DJANGO_", "HOF_", "PYTEST"))
    }
    umgebung.update(
        DJANGO_SETTINGS_MODULE="hof.settings.prod",
        DJANGO_SECRET_KEY="nur-fuer-den-test-der-prod-einstellungen",
        DJANGO_ALLOWED_HOSTS="hof.testhof.invalid",
        DATABASE_URL="postgres://hof:hof@localhost:5432/gibt-es-nicht",
        EMAIL_URL="smtp://localhost:1025",
        HOF_STICHTAG="2026-11-01",
    )
    ergebnis = subprocess.run(  # noqa: S603  (fester Aufruf, keine Eingaben von außen)
        [sys.executable, "-W", "ignore", "-c", PROD_SKRIPT],
        cwd=WURZEL,
        env=umgebung,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert ergebnis.returncode == 0, ergebnis.stderr[-2000:]
    daten = json.loads(ergebnis.stdout.strip().splitlines()[-1])
    assert daten["stichtag"] == "None"
    assert daten["heute"] in daten["echt"]
    assert daten["jetzt"][:10] in daten["echt"]
    assert daten["jetzt"].endswith(("+01:00", "+02:00"))


# --- Läufe mit --stichtag ------------------------------------------------------------


class _UhrBefehl(LaufBefehl):
    lauf_name = "uhr_test"

    def ausfuehren(self, **optionen: Any) -> str:
        return f"heute {zeit.heute().isoformat()}"


@pytest.mark.django_db
def test_lauf_mit_stichtag_rechnet_mit_seinem_tag() -> None:
    """Review 3, Punkt 1: Ein mit ``--stichtag`` gestarteter Lauf behält seinen eigenen
    Stichtag, auch in den Diensten, die er aufruft; ohne gilt ``heute()``."""
    befehl = _UhrBefehl(stdout=io.StringIO())
    befehl.handle(stichtag=datetime.date(2026, 12, 7))
    assert Lauf.objects.get(pk=befehl.lauf.pk).meldung == "heute 2026-12-07"
    befehl.handle(stichtag=None)
    assert Lauf.objects.get(pk=befehl.lauf.pk).meldung == f"heute {STICHTAG.isoformat()}"
    assert zeit.heute() == STICHTAG
