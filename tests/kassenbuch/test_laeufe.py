"""Läufe des Kassenbuchs: Einordnen nach dem Bankabruf, Abschluss am 3. des Monats."""

from __future__ import annotations

import configparser
import datetime
import io
from pathlib import Path

import pytest
from django.core.management import call_command
from django.test import override_settings

from apps.kassenbuch import services
from apps.kassenbuch.models import Buchung
from apps.kern import aktionen
from apps.kern.models import Arbeitsposten, Lauf
from tests.kassenbuch.conftest import KleinerHof, umsatz

LAEUFE = Path(__file__).resolve().parents[2] / "deploy" / "systemd" / "laeufe"


def _zeitplan(name: str) -> str:
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.read(LAEUFE / f"{name}.conf", encoding="utf-8")
    return parser["Timer"]["OnCalendar"]


def test_zeitplaene() -> None:
    """Einordnen täglich nach dem Bankabruf (Tageslauf um 6:00, Schritt 9), Abschluss am 3.
    jedes Monats."""
    from apps.cockpit import tageslauf

    assert _zeitplan("tageslauf") == "*-*-* 06:00:00"
    namen = [s.name for s in tageslauf.schritte()]
    assert namen.index("bank_abruf") + 1 == namen.index("kassenbuch_einordnen")
    assert _zeitplan("kassenbuch_monatsabschluss") == "*-*-03 07:00:00"


def test_lauf_einordnen_schreibt_laufprotokoll(klein: KleinerHof) -> None:
    """Aufgabe 3: Lauf ``kassenbuch_einordnen`` mit den drei Körben im Laufprotokoll."""
    eintrag = umsatz(klein.konto, datetime.date(2026, 1, 5), "250.00")
    umsatz(klein.konto, datetime.date(2026, 1, 6), "9.00", eingang=False, zweck="Unklar")
    services.regel_anlegen(
        "Miete",
        kategorie=klein.miete,
        objekt=klein.hof,
        gegen_iban=eintrag.gegen_iban,
        richtung="eingang",
    )

    call_command("kassenbuch_einordnen", stdout=io.StringIO())

    lauf = Lauf.objects.get(name="kassenbuch_einordnen")
    assert (lauf.status, lauf.gebucht, lauf.arbeitsposten, lauf.fehler) == ("erfolgreich", 1, 1, 0)
    assert Buchung.objects.get().lauf == lauf
    assert Arbeitsposten.objects.get(art=services.ART_EINORDNEN).lauf == lauf
    assert "1 Umsätze gebucht" in lauf.meldung


@override_settings(KASSENBUCH_BEGINN=datetime.date(2026, 1, 1))
def test_lauf_beginnt_beim_kassenbuch_beginn(klein: KleinerHof) -> None:
    """H6.3: Mit dem Jahr 2026 beginnen (``HOF_KASSENBUCH_BEGINN``)."""
    umsatz(klein.konto, datetime.date(2025, 12, 30), "9.00", eingang=False, zweck="alt")
    neu = umsatz(klein.konto, datetime.date(2026, 1, 2), "9.00", eingang=False, zweck="neu")

    call_command("kassenbuch_einordnen", stdout=io.StringIO())

    assert [p.bezug for p in Arbeitsposten.objects.filter(art=services.ART_EINORDNEN)] == [neu]


def test_aktion_braucht_ausfuehren_oder_seite() -> None:
    with pytest.raises(ValueError, match="genau eins"):
        aktionen.registrieren("test_leer", "Leer")
    with pytest.raises(ValueError, match="genau eins"):
        aktionen.registrieren("test_beides", "Beides", lambda p, b: None, seite=lambda p: "/")
