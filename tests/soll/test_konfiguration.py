"""Konfiguration der Sollstellung (``apps/soll/konfiguration.toml``)."""

from __future__ import annotations

import pytest

from apps.soll import konfiguration


def test_standard_dritter_werktag_nrw() -> None:
    """Schritt 5, Aufgabe 4: Standard der Fälligkeit ist der dritte Werktag, Feiertage NRW."""
    f = konfiguration.laden().faelligkeit
    assert (f.art, f.tag, f.land, f.samstag_ist_werktag) == ("werktag", 3, "NW", False)


def test_spaltenzuordnung_an_einer_stelle() -> None:
    v = konfiguration.laden().vergleich
    assert set(konfiguration.VERGLEICH_PFLICHT) <= set(v.spalten)
    assert set(v.arten.values()) <= set(konfiguration.SOLLARTEN)


def test_fehler_werden_erkannt() -> None:
    daten = {
        "faelligkeit": {"art": "werktag", "tag": 3, "land": "NW"},
        "vergleich_hellohousing": {
            "datumsformat": "%d.%m.%Y",
            "spalten": {"art": "A", "von": "V", "bis": "B", "betrag": "E", "einheit": "X"},
            "arten": {"Kaltmiete": "miete"},
        },
    }
    assert konfiguration._lesen(daten).faelligkeit.tag == 3
    with pytest.raises(konfiguration.KonfigurationFehler, match="Tag 0"):
        konfiguration._lesen({**daten, "faelligkeit": {"art": "werktag", "tag": 0}})
    with pytest.raises(konfiguration.KonfigurationFehler, match="Land XX"):
        konfiguration._lesen({**daten, "faelligkeit": {"art": "werktag", "tag": 3, "land": "XX"}})
    falsch = {**daten["vergleich_hellohousing"], "arten": {"Miete": "wohnen"}}  # type: ignore[dict-item]
    with pytest.raises(konfiguration.KonfigurationFehler, match="wohnen"):
        konfiguration._lesen({**daten, "vergleich_hellohousing": falsch})
