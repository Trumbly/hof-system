"""Skript zulieferung/vr_hochladen.py für den hof-mac (Schritt 1, Aufgabe 4).

Die FinTS-Quelle ist ein erfundener Adapter; gesendet wird in die Django-Testumgebung.
"""

from __future__ import annotations

import ast
import datetime
import importlib.util
import json
import sys
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from django.test import Client

from apps.bank import services
from apps.bank.models import Bankkonto
from apps.kern import zeit

WURZEL = Path(__file__).resolve().parents[2]
SKRIPT = WURZEL / "zulieferung" / "vr_hochladen.py"
HEUTE = zeit.heute()

ADAPTER = """
import datetime
from decimal import Decimal

def umsaetze_abrufen(von, bis):
    return {
        "umsaetze": [
            {"buchungsdatum": bis, "betrag": Decimal("250.00"), "gegenpartei": "Paul Erfunden",
             "gegen_iban": "DE13012345670000000002", "verwendungszweck": "Pacht F-01"},
            {"buchungsdatum": bis.isoformat(), "betrag": "-30.00",
             "gegenpartei": "Stadtwerke Testdorf", "verwendungszweck": "Wasser"},
        ],
        "saldo": {"betrag": Decimal("1220.00"), "stand_am": bis},
    }

def kaputt(von, bis):
    raise RuntimeError("PIN falsch für Paul Erfunden DE13012345670000000002")
"""

VERBOTEN = ("Paul", "Erfunden", "Stadtwerke", "DE13", "Pacht", "Wasser", "250", "1220")


@pytest.fixture(scope="module")
def skript() -> ModuleType:
    spec = importlib.util.spec_from_file_location("vr_hochladen", SKRIPT)
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


@pytest.fixture
def adapter(tmp_path: Path) -> Path:
    datei = tmp_path / "hof_adapter.py"
    datei.write_text(ADAPTER, encoding="utf-8")
    return datei


def test_nur_standardbibliothek() -> None:
    """Das Skript ist eigenständig: nur die Standardbibliothek, kein Import aus dem Projekt."""
    baum = ast.parse(SKRIPT.read_text(encoding="utf-8"))
    module = set()
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Import):
            module |= {a.name.split(".")[0] for a in knoten.names}
        elif isinstance(knoten, ast.ImportFrom) and knoten.module:
            module.add(knoten.module.split(".")[0])
    assert module <= set(sys.stdlib_module_names) | {"__future__"}, module


def test_probelauf_zaehlt_nur(
    skript: ModuleType,
    adapter: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Schritt 1, Aufgabe 4: --probelauf zählt nur, sendet nichts; die Ausgabe enthält keine
    Personendaten und keine Beträge."""
    gesendet: list[Any] = []
    monkeypatch.setattr(skript, "senden", lambda *a: gesendet.append(a))

    code = skript.main(["--probelauf", "--quelle", f"{adapter}:umsaetze_abrufen"])

    ausgabe = capsys.readouterr()
    assert code == 0
    assert gesendet == []
    assert "Probelauf: 2 Umsätze" in ausgabe.out
    assert "1 Eingänge, 1 Ausgänge" in ausgabe.out
    for wert in VERBOTEN:
        assert wert not in ausgabe.out + ausgabe.err, wert


def test_senden_an_den_endpunkt(
    skript: ModuleType,
    adapter: Path,
    tmp_path: Path,
    vr: Bankkonto,
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Schritt 1, Aufgabe 4: Das Skript liefert über den Endpunkt; ein zweiter Lauf bringt
    keine Doppelten. Die Ausgabe nennt nur Zähler."""
    token_datei = tmp_path / "token"
    token_datei.write_text(services.zulieferung_token_erzeugen(vr) + "\n", encoding="utf-8")

    def senden(url: str, token: str, lieferung: Any) -> dict[str, Any]:
        antwort = client.post(
            "/api/bank/zulieferung",
            data=json.dumps(lieferung),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        assert antwort.status_code == 200, antwort.content
        daten: dict[str, Any] = antwort.json()
        return daten

    monkeypatch.setattr(skript, "senden", senden)
    argumente = [
        "--quelle",
        f"{adapter}:umsaetze_abrufen",
        "--url",
        "https://hof.invalid/api/bank/zulieferung",
        "--token-datei",
        str(token_datei),
    ]

    assert skript.main(argumente) == 0
    assert skript.main(argumente) == 0

    ausgabe = capsys.readouterr().out
    assert vr.umsaetze.count() == 2
    assert "2 neu" in ausgabe and "0 neu, 2 schon vorhanden" in ausgabe
    for wert in VERBOTEN:
        assert wert not in ausgabe, wert
    stand = vr.kontostaende.get()
    assert stand.saldo == Decimal("1000.00")  # Saldo jetzt minus die heutigen Umsätze


def test_fehler_der_quelle_ohne_daten(
    skript: ModuleType, adapter: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Fehlermeldungen der Bank können Daten enthalten: Das Skript nennt nur die Art."""
    code = skript.main(["--probelauf", "--quelle", f"{adapter}:kaputt"])
    ausgabe = capsys.readouterr()
    assert code == 1
    assert "RuntimeError" in ausgabe.err
    for wert in (*VERBOTEN, "PIN"):
        assert wert not in ausgabe.out + ausgabe.err, wert


def test_quelle_falsch_angegeben(skript: ModuleType, tmp_path: Path) -> None:
    with pytest.raises(skript.ZulieferungFehler):
        skript.quelle_laden("ohne-doppelpunkt")
    with pytest.raises(skript.ZulieferungFehler, match="gibt es nicht"):
        skript.quelle_laden(f"{tmp_path / 'fehlt.py'}:f")
    assert skript.main(["--probelauf", "--quelle", ""]) == 2


def test_quelle_als_modulpfad(skript: ModuleType) -> None:
    funktion = skript.quelle_laden("json:dumps")
    assert funktion is json.dumps


def test_nur_https(skript: ModuleType) -> None:
    with pytest.raises(skript.ZulieferungFehler, match="https"):
        skript.senden("http://hof.invalid/api/bank/zulieferung", "t", {})


def test_umsatz_ausserhalb_des_zeitraums(skript: ModuleType) -> None:
    gestern = HEUTE - datetime.timedelta(days=1)
    with pytest.raises(skript.ZulieferungFehler, match="außerhalb"):
        skript.lieferung_bauen(
            {"umsaetze": [{"buchungsdatum": gestern, "betrag": "1"}]}, HEUTE, HEUTE
        )


def test_helfer_fuer_mt940(skript: ModuleType) -> None:
    """Der Adapter für python-fints: MT940-Transaktionen und Saldo in die Form der Quelle."""

    class Betrag:
        def __init__(self, wert: str) -> None:
            self.amount = Decimal(wert)

    class Transaktion:
        def __init__(self, daten: dict[str, Any]) -> None:
            self.data = daten

    umsaetze = skript.aus_mt940(
        [
            Transaktion(
                {
                    "date": HEUTE,
                    "entry_date": HEUTE,
                    "amount": Betrag("-12.30"),
                    "applicant_name": "Paul Erfunden",
                    "applicant_iban": "DE13012345670000000002",
                    "purpose": "Pacht",
                    "customer_reference": "NONREF",
                }
            )
        ]
    )
    saldo = skript.saldo_aus_mt940(Betrag("5.00"), stand_am=HEUTE)

    lieferung = skript.lieferung_bauen({"umsaetze": umsaetze, "saldo": saldo}, HEUTE, HEUTE)
    (umsatz,) = lieferung["umsaetze"]
    assert umsatz["betrag"] == "-12.30"
    assert umsatz["gegen_iban"] == "DE13012345670000000002"
    assert umsatz["bank_referenz"] == "NONREF"
    assert umsatz["rohdaten"]["amount"]  # JSON-fähig
    json.dumps(lieferung)
    assert lieferung["saldo"] == {"betrag": "5.00", "stand_am": HEUTE.isoformat()}
