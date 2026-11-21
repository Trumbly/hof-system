"""Testhof-Band in eigener Farbe (Design-Review nachher, Befund F; PR H).

Das Band teilte das Bernstein der Warnungen. Jetzt ist es Violett, eine Farbe, die sonst
nirgends eine Bedeutung hat, mit Kontrast AA (4,5:1) in hell und dunkel.
"""

from __future__ import annotations

import re
from pathlib import Path

WURZEL = Path(__file__).resolve().parents[2]
BASIS = (WURZEL / "static/css/apple/basis.css").read_text()
WERTE = (WURZEL / "static/css/werte.css").read_text()


def _werte(css: str, name: str) -> list[str]:
    """Alle Werte einer Variablen in Reihenfolge der Datei: erst hell, dann dunkel."""
    return [w.lower() for w in re.findall(rf"--{name}:\s*(#[0-9a-fA-F]{{6}})\b", css)]


def _leuchtdichte(farbe: str) -> float:
    kanaele = [int(farbe[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = (c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in kanaele)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _kontrast(a: str, b: str) -> float:
    hell, dunkel = sorted((_leuchtdichte(a), _leuchtdichte(b)), reverse=True)
    return (hell + 0.05) / (dunkel + 0.05)


def test_band_hat_eigene_farbe_mit_kontrast_aa() -> None:
    """Befund F: Band und Schrift des Testhof-Bands sind in hell und dunkel keine der
    Statusfarben (ok, Hinweis, Warnung, Fehler, neutral) und keine Akzentfarbe; die Schrift
    hat auf dem Band mindestens 4,5:1."""
    band, text = _werte(BASIS, "band"), _werte(BASIS, "band-text")
    assert len(band) == len(text) == 2  # hell, dunkel
    belegt = set()
    for name in ("ok", "hinweis", "warnung", "fehler", "neutral", "gefahr"):
        belegt |= set(_werte(WERTE, name)) | set(_werte(WERTE, f"{name}-grund"))
    for name in ("primaer", "primaer-weich", "link"):
        belegt |= set(_werte(BASIS, name))
    for hintergrund, schrift in zip(band, text, strict=True):
        assert hintergrund not in belegt and schrift not in belegt
        assert _kontrast(schrift, hintergrund) >= 4.5, (schrift, hintergrund)
    # Die Werte sind im Review vermerkt (7,0:1 hell, 8,5:1 dunkel).
    assert round(_kontrast(text[0], band[0]), 1) == 7.0
    assert round(_kontrast(text[1], band[1]), 1) == 8.5
