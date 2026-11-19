"""Buchungsanfrage → Vertrag: Anfrage für NW-G-0004 von der Website annehmen.

Weg vor R2-7 (v1, „Den Vertrag legst du von Hand an“, 18 Klicks): Anfrage öffnen, Partei
von Hand anlegen, zwei Kontaktwege einzeln hinzufügen, zurück zur Anfrage, „Neuer Vertrag“,
Mieter wählen, Beginn und Miete tippen, zurück zur Anfrage, Vertrag wählen, „Vertrag
angelegt“.

Weg seit R2-7 (Review 2, B5, Ziel 3 Klicks):
1. Arbeitslisten → „Anfrage öffnen“.
2. „Annehmen und Vertrag anlegen“ öffnet den Dialog „Neuer Vertrag“, vorausgefüllt: Einheit,
   Beginn (Wunschtermin), Miete (Zielmiete), Partei (vorhandene mit gleicher E-Mail oder
   „Neue Partei“ mit den Kontaktdaten der Anfrage).
3. „Vertrag anlegen“: Vertrag, Partei mit Kontaktwegen, Anfrage verknüpft und erledigt.
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
    "kontakt_name": "main .fakt-name",
    "kontakt_email": "main .fakt a[href^='mailto:']",
    "annehmen": "main a:has-text('Annehmen und Vertrag anlegen')",
    "mieter_gewaehlt": "dialog[open] input[name=mieter]:checked",
    "mieter_neu": "dialog[open] input[name=mieter][value=neu]",
    "neu_vorname": "dialog[open] input[name=neu_vorname]",
    "neu_nachname": "dialog[open] input[name=neu_nachname]",
    "neu_email": "dialog[open] input[name=neu_email]",
    "beginn": "dialog[open] input[name=beginn]",
    "miete": "dialog[open] input[name=miete]",
    "vertrag_anlegen": "dialog[open] button:has-text('Vertrag anlegen')",
}


def _wunsch(text: str) -> str:
    treffer = re.search(r"Gewünscht ab\s*(\d\d\.\d\d\.\d{4})", text)
    if not treffer:
        return ""
    return datetime.datetime.strptime(treffer[1], "%d.%m.%Y").date().isoformat()


def durchspielen(m: Messung, adressen: dict[str, Any]) -> None:
    s = {k: v.format(posten=adressen["posten"]["anfrage_buchung"]) for k, v in SELEKTOREN.items()}
    m.beginnen(adressen["arbeitslisten"])
    m.klick(s["anfrage_oeffnen"])
    # Ablesen zählt nicht: Name, E-Mail und Wunschtermin der Anfrage
    name = m.text(s["kontakt_name"]).strip()
    email = m.text(s["kontakt_email"]).strip()
    beginn = _wunsch(m.text())
    vorname, _, nachname = name.rpartition(" ")

    m.klick(s["annehmen"])
    if not m.gibt_es(s["mieter_gewaehlt"]):
        m.haken(s["mieter_neu"])
    if m.seite.locator(s["mieter_neu"]).is_checked():
        m.tippe(s["neu_vorname"], vorname, vorbelegt_reicht=True)
        m.tippe(s["neu_nachname"], nachname, vorbelegt_reicht=True)
        m.tippe(s["neu_email"], email, vorbelegt_reicht=True)
    m.tippe(s["beginn"], beginn, vorbelegt_reicht=True)
    m.tippe(s["miete"], adressen["zielmiete_anfrage"].replace(".", ","), vorbelegt_reicht=True)
    m.klick(s["vertrag_anlegen"])
    # HTMX leitet per HX-Redirect weiter, sobald Vertrag und PDF stehen (dauert länger als
    # die Wartezeit der Messung).
    m.seite.wait_for_url(re.compile(r"/stammdaten/vertraege/\d+/"))
    m.seite.wait_for_load_state("networkidle")
    m.anhalten()
    nummer = re.search(r"V-\d{4}", m.seite.title())
    assert nummer, f"Kein Vertrag angelegt: {m.seite.url}"
    assert "verknüpft" in m.text("body"), "Die Anfrage ist nicht mit dem Vertrag verknüpft."
