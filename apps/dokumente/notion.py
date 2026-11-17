"""Vorlagen-Texte aus einem Markdown-Export von Notion lesen (H8.1, C2).

Eine Datei ist eine Seite der Vorlagen-Bibliothek, so wie Notion sie exportiert::

    # Zahlungserinnerung Garage

    Art: Zahlungserinnerung
    Betreff: Zahlungserinnerung Vertrag {{ vertrag.nummer }}
    Status: Entwurf

    {{ empfaenger.anrede }},

    für den Vertrag {{ vertrag.nummer }} ist **{{ betrag.offen|euro }}** offen.

- Die Überschrift ist der Name; fehlt sie, gilt der Dateiname ohne die Kennung, die
  Notion anhängt.
- Die Zeilen „Schlüssel: Wert“ direkt darunter sind Eigenschaften. Gelesen werden die
  Schlüssel aus ``EIGENSCHAFTEN``; „Art“ ist Pflicht (Schlüssel oder Titel einer
  Vorlagenart), alle anderen Eigenschaften werden übergangen. Der Status aus Notion
  zählt nicht: Importiert wird immer als Entwurf.
- Der Rest ist der Text in Markdown; er wird HTML (Zeilenumbrüche bleiben). Platzhalter
  (``{{ … }}``) und Tags (``{% … %}``) bleiben dabei unverändert.

Annahme: So sieht der Export aus (Beispiel ``tests/fixtures/vorlagen/``). Weicht der
echte Export ab, ist nur ``EIGENSCHAFTEN`` bzw. ``lesen()`` anzupassen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import markdown

from apps.dokumente import vorlagenarten

#: Eigenschaft in Notion (klein geschrieben) → Feld der Vorlage.
EIGENSCHAFTEN = {"art": "art", "typ": "art", "betreff": "betreff"}

_EIGENSCHAFT = re.compile(r"^([^\s:][^:]{0,40}):\s*(.*)$")
_NOTION_KENNUNG = re.compile(r"\s+[0-9a-f]{32}$")
_PLATZHALTER = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.DOTALL)


class ImportFehler(ValueError):
    """Eine Datei lässt sich nicht übernehmen. ``art`` nennt die Abweichungsart."""

    def __init__(self, art: str, meldung: str) -> None:
        self.art = art
        super().__init__(meldung)


@dataclass(frozen=True)
class Vorlagentext:
    name: str
    art: str
    betreff: str
    text: str


def markdown_zu_html(text: str) -> str:
    """Markdown → HTML; Platzhalter und Tags bleiben, wie sie sind."""
    geschuetzt: list[str] = []

    def merken(treffer: re.Match[str]) -> str:
        geschuetzt.append(treffer.group(0))
        return f"\x00{len(geschuetzt) - 1}\x00"

    html = markdown.markdown(
        _PLATZHALTER.sub(merken, text), extensions=["tables", "sane_lists", "nl2br"]
    )
    return re.sub(r"\x00(\d+)\x00", lambda t: geschuetzt[int(t.group(1))], html).strip() + "\n"


def lesen(inhalt: str, dateiname: str) -> Vorlagentext:
    zeilen = inhalt.replace("\r\n", "\n").split("\n")
    name = ""
    while zeilen and not zeilen[0].strip():
        zeilen.pop(0)
    if zeilen and zeilen[0].startswith("# "):
        name = zeilen.pop(0)[2:].strip()
    if not name:
        name = _NOTION_KENNUNG.sub("", Path(dateiname).stem).strip()
    while zeilen and not zeilen[0].strip():
        zeilen.pop(0)
    eigenschaften: dict[str, str] = {}
    while zeilen and (treffer := _EIGENSCHAFT.match(zeilen[0].strip())):
        schluessel = treffer.group(1).strip().casefold()
        if schluessel in EIGENSCHAFTEN:
            eigenschaften[EIGENSCHAFTEN[schluessel]] = treffer.group(2).strip()
        zeilen.pop(0)
    rumpf = "\n".join(zeilen).strip()

    if not name:
        raise ImportFehler("ohne_name", "Kein Name (Überschrift fehlt).")
    if "art" not in eigenschaften:
        raise ImportFehler("ohne_art", "Eigenschaft „Art“ fehlt.")
    art = vorlagenarten.finden(eigenschaften["art"])
    if art is None:
        raise ImportFehler("unbekannte_art", "Unbekannte Vorlagenart.")
    if not rumpf:
        raise ImportFehler("ohne_text", "Kein Text.")
    return Vorlagentext(
        name=name,
        art=art.schluessel,
        betreff=eigenschaften.get("betreff", ""),
        text=markdown_zu_html(rumpf),
    )
