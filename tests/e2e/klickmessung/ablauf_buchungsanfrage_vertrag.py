"""Buchungsanfrage → Vertrag: Anfrage für NW-G-0004 von der Website annehmen.

Weg heute (v1, „Den Vertrag legst du von Hand an“):
1. Arbeitslisten → „Anfrage öffnen“, Kontakt ablesen.
2. Stammdaten → Parteien → „Neue Partei“: Vor- und Nachname tippen, anlegen.
3. An der Partei zwei Kontaktwege (E-Mail, Telefon) einzeln hinzufügen.
4. Zurück zur Anfrage (Arbeitslisten → „Anfrage öffnen“) → „Neuer Vertrag für …“: Mieter
   wählen, Beginn und Miete (Zielmiete) tippen, anlegen.
5. Zurück zur Anfrage → Vertrag wählen → „Vertrag angelegt“.
"""

from __future__ import annotations

import datetime
import re
from typing import Any

from tests.e2e.klickmessung.messung import Messung

NAME = "buchungsanfrage_vertrag"
TITEL = "Buchungsanfrage → Vertrag"

#: Alle Selektoren des Ablaufs. ``{posten}``: Arbeitsposten „Buchungsanfrage prüfen“.
SELEKTOREN = {
    "anfrage_oeffnen": "#posten-{posten} a:has-text('Anfrage öffnen')",
    "nav_stammdaten": "nav a:has-text('Stammdaten')",
    "nav_arbeitslisten": "nav a:has-text('Arbeitslisten')",
    "parteien": "main a[href$='/stammdaten/parteien/']",
    "partei_neu": "main a:has-text('Neue Partei')",
    "vorname": "input[name=vorname]",
    "nachname": "input[name=nachname]",
    "partei_anlegen": "main button:has-text('Anlegen')",
    "kontakt_art": "select[name=kontaktwege-art]",
    "kontakt_wert": "input[name=kontaktwege-wert]",
    "kontakt_hinzufuegen": "main button:has-text('Hinzufügen')",
    "vertrag_neu": "main a:has-text('Neuer Vertrag für')",
    "mieter": "select[name=mieter]",
    "beginn": "input[name=beginn]",
    "miete": "input[name=miete]",
    "vertrag_anlegen": "main button:has-text('Anlegen')",
    "vertrag_waehlen": "select[name=vertrag]",
    "vertrag_angelegt": "main button:has-text('Vertrag angelegt')",
}


def _kontakt(text: str) -> dict[str, str]:
    def feld(name: str) -> str:
        treffer = re.search(rf"{name}:\s*(.+)", text)
        return treffer[1].strip() if treffer else ""

    wunsch = re.search(r"Gewünscht ab (\d\d\.\d\d\.\d{4})", text)
    return {
        "name": feld("Name"),
        "email": feld("E-Mail"),
        "telefon": feld("Telefon"),
        "beginn": datetime.datetime.strptime(wunsch[1], "%d.%m.%Y").date().isoformat()
        if wunsch
        else "",
    }


def durchspielen(m: Messung, adressen: dict[str, Any]) -> None:
    s = {k: v.format(posten=adressen["posten"]["anfrage_buchung"]) for k, v in SELEKTOREN.items()}
    m.beginnen(adressen["arbeitslisten"])
    m.klick(s["anfrage_oeffnen"])
    kontakt = _kontakt(m.text())
    vorname, _, nachname = kontakt["name"].partition(" ")

    # Partei anlegen
    m.klick(s["nav_stammdaten"])
    m.klick(s["parteien"])
    m.klick(s["partei_neu"])
    m.tippe(s["vorname"], vorname)
    m.tippe(s["nachname"], nachname)
    m.klick(s["partei_anlegen"])
    partei = re.search(r"/parteien/(\d+)/", m.seite.url)
    assert partei, f"Keine Partei angelegt: {m.seite.url}"
    for art, wert in (("E-Mail", kontakt["email"]), ("Telefon", kontakt["telefon"])):
        m.waehle(s["kontakt_art"], rf"^{art}$")
        m.tippe(s["kontakt_wert"], wert)
        m.klick(s["kontakt_hinzufuegen"])

    # Vertrag aus der Anfrage
    m.klick(s["nav_arbeitslisten"])
    m.klick(s["anfrage_oeffnen"])
    m.klick(s["vertrag_neu"])
    m.waehle(s["mieter"], rf"#{partei[1]}\b|{re.escape(kontakt['name'])}")
    m.tippe(s["beginn"], kontakt["beginn"], vorbelegt_reicht=True)
    m.tippe(s["miete"], adressen["zielmiete_anfrage"], vorbelegt_reicht=True)
    m.klick(s["vertrag_anlegen"])
    nummer = re.search(r"V-\d{4}", m.seite.title())
    assert nummer, f"Kein Vertrag angelegt: {m.seite.url}"

    # Anfrage mit dem Vertrag verknüpfen
    m.klick(s["nav_arbeitslisten"])
    m.klick(s["anfrage_oeffnen"])
    m.waehle(s["vertrag_waehlen"], rf"^{nummer[0]}\b")
    m.klick(s["vertrag_angelegt"])
    m.anhalten()
    assert "Vertrag angelegt" in m.text(), "Die Anfrage steht nicht auf „Vertrag angelegt“."
