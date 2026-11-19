"""Übergabe ohne Kaution: neuer Vertrag (Entwurf) für eine freie Garage, keine Kaution.

Vorbereitung (zählt nicht): Vertrag mit Beginn heute für eine freie Einheit mit einer
vorhandenen Partei anlegen (Seite „Neuer Vertrag“, Partei über die Suche, Kaution offen).
Gemessen wird ab der Vertragsseite.

- ``durchspielen`` (Ziel, Review 2, A10 und B8): alles im Assistenten „Übergabe am Handy“,
  im Schritt „Kaution“ „keine Kaution vereinbart“ anhaken.
- ``umweg``: zuerst auf der Vertragsseite Kaution 0 € im Dialog speichern (mehr ▾ → Kaution
  bearbeiten), dann der Assistent: Vorab (unterschrieben anhaken), Zähler, Fotos (ohne Foto),
  Schlüssel, Kaution (schon „keine“), Unterschrift (Foto der Quittung), Abschluss.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Any

from PIL import Image

from tests.e2e.klickmessung.messung import Gescheitert, Messung

NAME = "uebergabe_ohne_kaution"
TITEL = "Übergabe ohne Kaution"

#: Alle Selektoren des Ablaufs.
SELEKTOREN = {
    # Vorbereitung
    "einheit": "main select[name=einheit]",
    "partei_suche": "main input[name=partei_suche]",
    "partei_treffer": "main #partei-treffer input[name=mieter]",
    "beginn": "main input[name=beginn]",
    "miete": "main input[name=miete]",
    "vertrag_anlegen": "main button:has-text('Vertrag anlegen')",
    # Kaution auf der Vertragsseite (Umweg), als Dialog
    "mehr": "main summary:has-text('mehr')",
    "kaution_dialog": "main a:has-text('Kaution bearbeiten')",
    "kaution_betrag": "dialog[open] input[name=kaution-betrag]",
    "kaution_speichern": "dialog[open] button[type=submit]",
    # Assistent
    "starten": "main button:has-text('Übergabe am Handy')",
    "keine_kaution": "main input[name=keine]",
    "unterschrieben": "input[name=vertrag_unterschrieben]",
    "weiter": "main button:has-text('Speichern und weiter')",
    # Schritt ohne Inhalt (kein Zähler, kein Schlüssel): kurz begründen und überspringen
    "begruendung": "main input[name=begruendung]",
    "ueberspringen": "main button:has-text('Überspringen und weiter')",
    "unterschrift": "input[type=file][name=unterschrift]",
    "abschliessen": "main button:has-text('abschließen')",
    "hindernisse": ".hindernisse",
}
#: Suchtext für eine vorhandene Partei des Testhofs (Vorbereitung).
PARTEI_SUCHE = "e"
#: Übergabe am Tag des Beginns (der Assistent schlägt heute vor; davor lehnt er ab).
BEGINN = datetime.date.today().isoformat()
MIETE = "55,00"


def vorbereiten(m: Messung, nw_id: str, pfad_neu: str) -> str:
    """Legt den Vertrag an (Messung läuft noch nicht) und gibt die Adresse seiner Seite."""
    s = SELEKTOREN
    m.seite.goto(pfad_neu)
    m.waehle(s["einheit"], rf"^{nw_id}\b")
    m.seite.fill(s["partei_suche"], PARTEI_SUCHE)
    m.seite.wait_for_selector(s["partei_treffer"])
    m.seite.locator(s["partei_treffer"]).first.check()
    m.tippe(s["beginn"], BEGINN)
    m.tippe(s["miete"], MIETE)
    m.klick(s["vertrag_anlegen"])
    if not re.search(r"/vertraege/\d+/", m.seite.url):
        raise Gescheitert(f"Vorbereitung: kein Vertrag angelegt ({m.seite.url}).")
    return m.seite.url


def _unterschrift(ablage: Path) -> str:
    pfad = ablage / "unterschrift.png"
    Image.new("RGB", (320, 120), (245, 245, 240)).save(pfad)
    return str(pfad)


def _assistent(m: Messung, ablage: Path) -> None:
    """Vom Start des Assistenten bis zum Abschluss."""
    s = SELEKTOREN
    m.klick(s["starten"])
    while not m.gibt_es(s["abschliessen"]):
        for haken in ("keine_kaution", "unterschrieben"):
            if m.gibt_es(s[haken]):
                m.haken(s[haken])
        if m.gibt_es(s["unterschrift"]):
            m.datei(s["unterschrift"], _unterschrift(ablage))
        if m.ergebnis.klicks > 40:
            raise Gescheitert("Der Assistent kommt nicht zum Abschluss.")
        if m.gibt_es(s["ueberspringen"]):
            m.tippe(s["begruendung"], "Stellplatz ohne Strom und Schloss", vorbelegt_reicht=True)
            m.klick(s["ueberspringen"])
        else:
            m.klick(s["weiter"])
    try:
        m.klick(s["abschliessen"])
    finally:
        m.anhalten()
    if m.gibt_es(s["abschliessen"]):
        hindernisse = " ".join(m.seite.locator(s["hindernisse"]).all_inner_texts())
        raise Gescheitert(f"Abschluss verweigert: {hindernisse}")
    assert "abgeschlossen" in m.text(), "Die Übergabe ist nicht abgeschlossen."


def durchspielen(m: Messung, adressen: dict[str, Any], ablage: Path) -> None:
    """Ziel: ohne Umweg, alles im Assistenten."""
    vertrag = vorbereiten(m, adressen["einheiten_frei"][0], adressen["vertrag_neu"])
    m.beginnen(vertrag)
    _assistent(m, ablage)


def umweg(m: Messung, adressen: dict[str, Any], ablage: Path) -> None:
    """Heute: Kaution 0 € auf der Vertragsseite, dann der Assistent."""
    s = SELEKTOREN
    vertrag = vorbereiten(m, adressen["einheiten_frei"][1], adressen["vertrag_neu"])
    m.beginnen(vertrag)
    m.klick(s["mehr"])
    m.klick(s["kaution_dialog"])
    m.tippe(s["kaution_betrag"], "0")
    m.klick(s["kaution_speichern"])
    m.seite.wait_for_selector("#dialog:not([open])", state="attached")  # gespeichert
    _assistent(m, ablage)
