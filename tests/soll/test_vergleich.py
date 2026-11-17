"""Vergleich mit dem Soll-Export aus HelloHousing (Schritt 5, Aufgabe 6).

Die Beispieldatei ``tests/fixtures/soll/hellohousing_soll.csv`` ist erfunden; das Format
ist eine Annahme (Spaltenzuordnung in ``apps/soll/konfiguration.toml``).
"""

from __future__ import annotations

import datetime
import io
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.kern.models import Lauf, Objekt
from apps.soll import services as soll
from apps.soll import vergleich
from apps.soll.models import Sollposten
from apps.stammdaten import services
from apps.stammdaten.models import Einheit, EinheitStatus, Partei, Vertrag

D = datetime.date
E = Decimal
BEISPIEL = Path(__file__).resolve().parents[1] / "fixtures" / "soll" / "hellohousing_soll.csv"


def _einheit(objekt: Objekt, typ: str, nw_id: str) -> Einheit:
    return services.einheit_anlegen(
        objekt, typ, nw_id=nw_id, status=EinheitStatus.Status.FREI, status_ab=D(2020, 1, 1)
    )


def _vertrag(
    einheit: Einheit, mieter: Partei, beginn: datetime.date, miete: str, **kw: object
) -> Vertrag:
    return services.vertrag_anlegen(
        einheiten=[einheit],
        mieter=[mieter],
        beginn=beginn,
        miete=E(miete),
        kaution=E("0"),
        status=Vertrag.Status.AKTIV,
        **kw,  # type: ignore[arg-type]
    )


@pytest.fixture
def vertraege(objekt: Objekt, mieter: Partei) -> dict[str, Vertrag]:
    """Vier Verträge passend zur Beispieldatei, Soll gestellt bis Dezember 2026."""
    ergebnis = {
        "A": _vertrag(
            _einheit(objekt, Einheit.Typ.GARAGE, "NW-G-0001"),
            mieter,
            D(2026, 1, 1),
            "85.00",
            herkunft="hellohousing:MV-2001",
        ),
        "B": _vertrag(
            _einheit(objekt, Einheit.Typ.HALLE, "NW-H-0001"),
            mieter,
            D(2025, 1, 1),
            "1000.00",
            pauschale=E("50.00"),
            steuersatz=E("19"),
            herkunft="hellohousing:MV-2002",
        ),
        "C": _vertrag(
            _einheit(objekt, Einheit.Typ.STELLPLATZ, "NW-S-0001"),
            mieter,
            D(2026, 8, 15),
            "31.00",
            herkunft="hellohousing:MV-2003",
        ),
        # ohne Herkunft: gefunden über die Einheit
        "D": _vertrag(
            _einheit(objekt, Einheit.Typ.LAGER, "NW-L-0001"), mieter, D(2024, 5, 1), "40.00"
        ),
    }
    soll.monatslauf(D(2026, 12, 1))
    return ergebnis


def test_vergleich_zaehlt_je_vertrag(vertraege: dict[str, Vertrag]) -> None:
    """Abnahme Schritt 5: Der Vergleich läuft gegen die Beispieldatei und zählt je Vertrag die
    Abweichungen."""
    bericht = vergleich.vergleichen(BEISPIEL)
    a, b, c, d = (vertraege[k].nummer for k in "ABCD")

    assert (bericht.von, bericht.bis, bericht.zeilen) == (D(2026, 7, 1), D(2026, 12, 1), 31)
    assert bericht.je_vertrag == {
        a: Counter(),
        b: Counter({vergleich.BETRAG: 1, vergleich.FEHLT_IM_EXPORT: 1}),
        c: Counter({vergleich.FEHLT_IM_HOF: 1}),
        d: Counter({vergleich.FAELLIGKEIT: 1}),
    }
    assert bericht.mit_abweichung == [b, c, d]
    assert dict(bericht.uebersprungen) == {
        vergleich.OHNE_VERTRAG: [31],
        vergleich.ART_UNBEKANNT: [32],
    }


def test_bericht_ohne_betraege_und_namen(vertraege: dict[str, Vertrag], mieter: Partei) -> None:
    """Schritt 5, Aufgabe 6 (KI-Grenze): Der Bericht nennt nur Zähler, Vertragsnummer und
    NW-ID, keine Beträge je Vertrag und keine Namen."""
    text = vergleich.vergleichen(BEISPIEL).text()
    b = vertraege["B"].nummer
    assert f"{b} (NW-H-0001): fehlt im Export 1, Betrag weicht ab 1" in text
    assert "Verträge verglichen: 4, ohne Abweichung: 1, mit Abweichungen: 3" in text
    assert "kein Vertrag gefunden: 1 (Zeile 31)" in text
    for verboten in ("1.190", "1190", "1.180", "59,50", "85,00", "MV-2002", mieter.nachname):
        assert verboten not in text


def test_befehl_soll_vergleich(vertraege: dict[str, Vertrag]) -> None:
    """Schritt 5, Aufgabe 6: ``soll_vergleich --export <datei>`` gibt den Bericht aus und
    schreibt einen Lauf mit Zählern; er ändert nichts."""
    vorher = Sollposten.objects.count()
    ausgabe = io.StringIO()
    call_command("soll_vergleich", "--export", str(BEISPIEL), stdout=ausgabe)
    assert "mit Abweichungen: 3" in ausgabe.getvalue()
    lauf = Lauf.objects.get(name="soll_vergleich")
    assert lauf.status == "erfolgreich"
    assert lauf.meldung.startswith("4 Verträge verglichen, 3 mit Abweichungen (4 gesamt)")
    assert Sollposten.objects.count() == vorher

    # Engerer Zeitraum: nur Juli.
    call_command(
        "soll_vergleich", "--export", str(BEISPIEL), "--bis", "2026-07-31", stdout=io.StringIO()
    )
    juli = Lauf.objects.filter(name="soll_vergleich").first()
    assert juli is not None and juli.meldung.startswith("4 Verträge verglichen, 2 mit")
    assert juli.meldung.endswith(f"{vertraege['C'].nummer}, {vertraege['D'].nummer}")


def test_falsche_datei(tmp_path: Path, db: None) -> None:
    falsch = tmp_path / "falsch.csv"
    falsch.write_text("A;B\n1;2\n", encoding="utf-8")
    with pytest.raises(CommandError, match="Spalten fehlen: Sollart"):
        call_command("soll_vergleich", "--export", str(falsch), stdout=io.StringIO())
    with pytest.raises(CommandError, match="nicht gefunden"):
        call_command("soll_vergleich", "--export", str(tmp_path / "fehlt.csv"))
