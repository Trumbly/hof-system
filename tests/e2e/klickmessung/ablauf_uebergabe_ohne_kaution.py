"""Übergabe ohne Kaution: neuer Vertrag (Entwurf) für eine freie Garage, keine Kaution.

Vorbereitung (zählt nicht): Vertrag mit Beginn heute für eine freie Einheit mit einer
vorhandenen Partei anlegen. Gemessen wird ab der Vertragsseite.

- ``durchspielen`` (Ziel, Review 2, A10): alles im Assistenten „Übergabe am Handy“. Heute hat
  er keinen Kautionsschritt und keine Wahl „keine Kaution vereinbart“; der Abschluss wird
  mit „Die Kaution ist nicht erfasst.“ verweigert → ``Gescheitert``.
- ``umweg``: zuerst auf der Vertragsseite Kaution 0 € speichern, dann der Assistent: Vorab
  (unterschrieben anhaken), Zähler, Fotos (ohne Foto), Schlüssel, Unterschrift (Foto der
  Quittung, das verlangt der Abschluss), Abschluss. Wer erst im Assistenten merkt, dass die
  Kaution fehlt, muss ihn verlassen; der Assistent verlinkt den Vertrag nicht.
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
    "einheit": "select[name=einheit]",
    "mieter": "select[name=mieter]",
    "beginn": "input[name=beginn]",
    "miete": "input[name=miete]",
    "vertrag_anlegen": "main button:has-text('Anlegen')",
    # Kaution auf der Vertragsseite (Umweg)
    "kaution_betrag": "input[name=kaution-betrag]",
    "kaution_speichern": "form:has(input[name=kaution-betrag]) button[type=submit]",
    # Assistent; „keine Kaution“ gibt es heute nicht (Ziel aus A10)
    "starten": "main button:has-text('Übergabe am Handy')",
    "keine_kaution": "main input[name$=keine_kaution]",
    "unterschrieben": "input[name=vertrag_unterschrieben]",
    "weiter": "main button:has-text('Speichern und weiter')",
    "unterschrift": "input[type=file][name=unterschrift]",
    "abschliessen": "main button:has-text('abschließen')",
    "hindernisse": ".hindernisse",
}
#: Übergabe am Tag des Beginns (der Assistent schlägt heute vor; davor lehnt er ab).
BEGINN = datetime.date.today().isoformat()
MIETE = "55.00"


def vorbereiten(m: Messung, nw_id: str, pfad_neu: str) -> str:
    """Legt den Vertrag an (Messung läuft noch nicht) und gibt die Adresse seiner Seite."""
    s = SELEKTOREN
    m.seite.goto(pfad_neu)
    m.waehle(s["einheit"], rf"^{nw_id}\b")
    m.waehle(s["mieter"], r"\w")  # erste Partei der Liste (nicht „---------“)
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
    m.tippe(s["kaution_betrag"], "0")
    m.klick(s["kaution_speichern"])
    _assistent(m, ablage)
