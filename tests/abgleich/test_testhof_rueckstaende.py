"""Abnahme Schritt 6: Die Rückstände sind nach dem Lauf aktuell (gegen den Testhof)."""

from __future__ import annotations

from apps.abgleich import services
from testhof.aufbau import Testhof
from tests.abgleich.conftest import laeufe


def test_rueckstaende_nach_dem_lauf_aktuell(testhof: Testhof, stichtag: object) -> None:
    """Abnahme Schritt 6: Die Rückstände sind nach dem Lauf aktuell (Standardablauf 2)."""
    vorher = services.rueckstaende()
    assert len(vorher.mit_rueckstand) > 30  # ohne Abgleich ist fast alles offen

    laeufe()

    nachher = services.rueckstaende()
    erwartet = {
        testhof.abgleich.sonderfaelle[name][0].nummer
        for name in (
            "dauerauftrag_alte_miete",
            "falsche_einheit_im_verwendungszweck",
            "rueckbuchung",
        )
    }
    assert {r.vertrag.nummer for r in nachher.mit_rueckstand} == erwartet
    zweck = testhof.abgleich.sonderfaelle["falsche_einheit_im_verwendungszweck"][0]
    assert next(r for r in nachher.vertraege if r.vertrag == zweck).in_klaerung
    assert nachher.datenstand == testhof.bank.ende
    zahlen = nachher.kennzahlen()
    assert zahlen["vertraege_mit_rueckstand"] == 3
    assert zahlen["summe"] == nachher.summe > 0
