"""Vergleich mit dem alten Mahnlauf (``mahnwesen_vergleich``): je Woche Fälle und Stufen als
Zähler und NW-IDs, ohne Namen und Beträge; optional gegen die Exportdatei des alten Laufs."""

from __future__ import annotations

import datetime
import io
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.kern.models import Benutzer, Lauf
from apps.mahnwesen import services, vergleich
from apps.mahnwesen.models import Mahnsperre
from tests.mahnwesen.conftest import Mahnhof, Vorlagen, lauf, letzter, senden

pytestmark = pytest.mark.django_db
D = datetime.date
ALT = Path(__file__).resolve().parents[1] / "fixtures" / "mahnwesen" / "alter_mahnlauf.csv"


@pytest.fixture
def zwei_wochen(mahnhof: Mahnhof, vorlagen: Vorlagen, benutzer: Benutzer) -> Mahnhof:
    """Zwei Montage: NW-G-0001 bekommt Erinnerung und Mahnung, NW-G-0002 ist gesperrt."""
    erster = mahnhof.vertrag()
    zweiter = mahnhof.vertrag(miete="50.00")
    assert [e.nw_id for e in erster.einheiten.all()] == ["NW-G-0001"]
    services.sperre_setzen(zweiter, Mahnsperre.Grund.RATENPLAN, von=D(2026, 10, 1))
    lauf(D(2026, 10, 12))
    senden(letzter(erster).schreiben, benutzer, D(2026, 10, 12))  # type: ignore[arg-type]
    lauf(D(2026, 10, 19))
    return mahnhof


def _befehl(*argumente: str) -> str:
    ausgabe = io.StringIO()
    call_command("mahnwesen_vergleich", *argumente, stdout=ausgabe)
    return ausgabe.getvalue()


def test_je_woche_faelle_und_stufen(zwei_wochen: Mahnhof) -> None:
    """Abnahme Schritt 7 (Vorbereitung des Vergleichs über vier Wochen): je Woche die
    Vorschläge je Stufe mit Anzahl und NW-IDs, offene und gesperrte Fälle."""
    text = _befehl("--von", "2026-10-12", "--bis", "2026-10-25")
    assert "Woche ab 12.10.2026: 1 Fälle offen, 1 gesperrt (NW-G-0002)" in text
    assert "Vorschlag erinnerung: 1 (NW-G-0001)" in text
    assert "Woche ab 19.10.2026" in text
    assert "Vorschlag mahnung: 1 (NW-G-0001)" in text
    assert zwei_wochen.mieter.nachname not in text and "€" not in text
    eintrag = Lauf.objects.get(name="mahnwesen_vergleich")
    assert eintrag.meldung == "2 Wochen, 2 Vorschläge"


def test_gegen_den_alten_lauf(zwei_wochen: Mahnhof) -> None:
    """Mit ``--alt`` nennt der Bericht je Woche die Abweichungen (NW-ID, Stufe neu und alt)."""
    text = _befehl("--von", "2026-10-12", "--bis", "2026-10-25", "--alt", str(ALT))
    assert "NW-G-0002: neu –, alt erinnerung" in text
    assert text.count("Abweichungen zum alten Lauf: 0") == 1
    assert "1 Abweichungen zum alten Lauf" in Lauf.objects.get(name="mahnwesen_vergleich").meldung


def test_exportdatei_pruefen(tmp_path: Path, db: None) -> None:
    falsch = tmp_path / "falsch.csv"
    falsch.write_text("Datum;Einheit\n2026-10-12;NW-G-0001\n", encoding="utf-8")
    with pytest.raises(CommandError, match="Spalten fehlen"):
        _befehl("--von", "2026-10-12", "--bis", "2026-10-18", "--alt", str(falsch))
    stufe = tmp_path / "stufe.csv"
    stufe.write_text("Woche;NW-ID;Stufe\n2026-10-12;NW-G-0001;Inkasso\n", encoding="utf-8")
    with pytest.raises(CommandError, match="Zeile 2"):
        _befehl("--von", "2026-10-12", "--bis", "2026-10-18", "--alt", str(stufe))
    with pytest.raises(CommandError, match="nicht gefunden"):
        _befehl("--von", "2026-10-12", "--bis", "2026-10-18", "--alt", str(tmp_path / "x.csv"))


def test_alt_lesen() -> None:
    alt = vergleich.alt_lesen(ALT)
    assert alt == {
        D(2026, 10, 12): {"NW-G-0001": "erinnerung", "NW-G-0002": "erinnerung"},
        D(2026, 10, 19): {"NW-G-0001": "mahnung"},
    }
