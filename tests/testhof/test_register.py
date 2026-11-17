"""Anmeldung der Generator-Module und Reihenfolge (Schritt 0c, Aufgabe 2)."""

from __future__ import annotations

import pytest

from testhof import register


def _modul(name: str, *abhaengig_von: str) -> register.Modul:
    return register.Modul(
        name=name,
        abhaengig_von=abhaengig_von,
        aufbauen=lambda kontext: None,
        leeren=lambda: None,
    )


def _namen(module: list[register.Modul]) -> list[str]:
    return [m.name for m in module]


def test_reihenfolge_nach_abhaengigkeiten() -> None:
    """Schritt 0c, Aufgabe 2: Module laufen in fester Reihenfolge; jedes nach denen,
    von denen es abhängt."""
    module = {
        m.name: m
        for m in (
            _modul("abgleich", "bank", "soll"),
            _modul("soll", "stammdaten"),
            _modul("bank", "kern"),
            _modul("stammdaten", "kern"),
            _modul("kern"),
        )
    }

    assert _namen(register.reihenfolge(module)) == [
        "kern",
        "bank",
        "stammdaten",
        "soll",
        "abgleich",
    ]


def test_reihenfolge_haengt_nicht_von_der_anmeldung_ab() -> None:
    """Schritt 0c, Aufgabe 1: Gleichrangige Module sortiert der Name, nicht die Reihenfolge
    der Anmeldung."""
    a = {m.name: m for m in (_modul("kern"), _modul("zaehler", "kern"), _modul("bank", "kern"))}
    b = {m.name: m for m in (_modul("bank", "kern"), _modul("zaehler", "kern"), _modul("kern"))}

    assert _namen(register.reihenfolge(a)) == _namen(register.reihenfolge(b))


def test_unbekannte_abhaengigkeit() -> None:
    with pytest.raises(register.RegisterFehler, match="unbekannten"):
        register.reihenfolge({"bank": _modul("bank", "kern")})


def test_abhaengigkeit_im_kreis() -> None:
    module = {m.name: m for m in (_modul("a", "b"), _modul("b", "a"), _modul("kern"))}
    with pytest.raises(register.RegisterFehler, match="Kreis"):
        register.reihenfolge(module)


def test_modul_nur_einmal_anmelden() -> None:
    register.laden()
    with pytest.raises(register.RegisterFehler, match="schon angemeldet"):
        register.registrieren("kern", aufbauen=lambda kontext: None, leeren=lambda: None)


def test_kern_ist_angemeldet() -> None:
    """Schritt 0c, Aufgabe 2: Für jetzt gibt es das Modul des Kerns."""
    module = register.laden()

    assert "kern" in module
    assert _namen(register.reihenfolge())[0] == "kern"
