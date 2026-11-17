"""Liste der Sonderfälle (Schritt 0c, Aufgabe 4)."""

from __future__ import annotations

import re
import warnings
from pathlib import Path

from testhof import register, sonderfaelle

BAUPLAN = Path(__file__).resolve().parents[2] / "docs" / "bauplan.md"


def _tabelle_im_bauplan() -> dict[str, tuple[int, ...]]:
    """Die Tabelle „Sonderfälle“ aus dem Abschnitt „Testdaten“ des Bauplans."""
    text = BAUPLAN.read_text(encoding="utf-8")
    abschnitt = text.split("## Testdaten", 1)[1].split("\n## ", 1)[0]
    faelle = {}
    for zeile in abschnitt.splitlines():
        teile = [t.strip() for t in zeile.strip().strip("|").split("|")]
        if len(teile) == 2 and re.fullmatch(r"\d+(, \d+)*", teile[1]):
            faelle[teile[0]] = tuple(int(s) for s in teile[1].split(", "))
    return faelle


def test_sechzehn_sonderfaelle_wie_im_bauplan() -> None:
    """Schritt 0c, Aufgabe 4: Die 16 Sonderfälle aus dem Bauplan mit Name, Beschreibung und
    den Schritten, die sie prüfen."""
    bauplan = _tabelle_im_bauplan()

    assert len(bauplan) == 16
    assert len(sonderfaelle.SONDERFAELLE) == 16
    assert {f.name: f.schritte for f in sonderfaelle.SONDERFAELLE} == bauplan
    assert len(sonderfaelle.nach_schluessel()) == 16
    for fall in sonderfaelle.SONDERFAELLE:
        assert re.fullmatch(r"[a-z_]+", fall.schluessel)
        assert fall.beschreibung


def test_module_nennen_nur_bekannte_sonderfaelle() -> None:
    bekannt = set(sonderfaelle.nach_schluessel())
    for modul in register.laden().values():
        assert set(modul.sonderfaelle) <= bekannt, modul.name


def test_sonderfaelle_ohne_modul_melden() -> None:
    """Schritt 0c, Aufgabe 4: Fälle ohne Erzeugungsmodul erscheinen als Warnung, nicht als
    Fehler."""
    offen = sonderfaelle.ohne_modul()
    if offen:
        warnings.warn(
            f"{len(offen)} von {len(sonderfaelle.SONDERFAELLE)} Sonderfällen haben noch kein "
            "Modul im Testhof: "
            + ", ".join(
                f"{f.schluessel} (Schritt {', '.join(map(str, f.schritte))})" for f in offen
            ),
            UserWarning,
            stacklevel=1,
        )
    assert len(offen) <= len(sonderfaelle.SONDERFAELLE)
