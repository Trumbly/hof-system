"""Website-Kündigung übernehmen: Kündigung für NW-S-0001 (V-0010) von der Website.

Ziel (Review 2, A3 und B, zwei Klicks): Arbeitslisten → „Anfrage öffnen“; die Anfrage zeigt
das Kündigungsformular des Vertrags vorausgefüllt (Zugang = Meldedatum, gekündigt vom
Mieter, Wunschtermin, Zugangsweg Website). „Kündigung anlegen“ speichert die Kündigung am
Vertrag; die Anfrage ist danach erledigt und mit ihr verknüpft. Passen mehrere Verträge,
führt „Kündigung anlegen“ in den Dialog des Vertrags; dann kommt dort „Speichern“ dazu.

``umweg`` misst den Weg von Hand: Kündigung im Dialog des Vertrags erfassen, dann die
Anfrage mit der erfassten Kündigung verknüpfen.
"""

from __future__ import annotations

import datetime
import re
from typing import Any

from tests.e2e.klickmessung.messung import Messung, NichtMoeglich

NAME = "website_kuendigung"
TITEL = "Website-Kündigung übernehmen"

#: Alle Selektoren des Ablaufs. ``{posten}``: Arbeitsposten „Buchungsanfrage prüfen“.
SELEKTOREN = {
    "anfrage_oeffnen": "#posten-{posten} a:has-text('Anfrage öffnen')",
    "kuendigung_anlegen": "main :is(button, a):has-text('Kündigung anlegen')",
    "dialog_speichern": "dialog[open] button[type=submit]",
    "fristende": "input[name=kuendigung-wunsch_wahl][value=fristende]",
    # Umweg von Hand
    "vertrag": "main a:has-text('{vertrag}')",
    "kuendigung_dialog": "main a:has-text('Kündigung erfassen')",
    "zugang_am": "dialog[open] input[name=kuendigung-zugang_am]",
    "von": "dialog[open] select[name=kuendigung-von]",
    "zum_wunsch": "dialog[open] input[name=kuendigung-zum_wunsch]",
    "nav_arbeitslisten": "nav a:has-text('Arbeitslisten')",
    "schon_erfasst": "main summary:has-text('Schon erfasst')",
    "anfrage_erledigt": "main button:has-text('verknüpfen und erledigen')",
}


def _anfrage(text: str) -> dict[str, str]:
    def datum(muster: str) -> str:
        treffer = re.search(muster + r"\s*(\d\d\.\d\d\.\d{4})", text, re.IGNORECASE)
        if not treffer:
            return ""
        return datetime.datetime.strptime(treffer[1], "%d.%m.%Y").date().isoformat()

    vertrag = re.search(r"(V-\d{4})", text)
    return {
        "gemeldet": datum("gemeldet am"),
        "wunsch": datum(r"Gewünschtes Ende|Gewünscht ab"),
        "vertrag": vertrag[1] if vertrag else "",
    }


def _selektoren(adressen: dict[str, Any], vertrag: str = "") -> dict[str, str]:
    posten = adressen["posten"]["anfrage_kuendigung"]
    return {k: v.format(posten=posten, vertrag=vertrag) for k, v in SELEKTOREN.items()}


def _wahl_falls_noetig(m: Messung, s: dict[str, str]) -> None:
    """Liegt der Wunschtermin vor dem Fristende, fragt das Formular nach (Fristende)."""
    if m.gibt_es(f"dialog[open] {s['fristende']}"):
        m.haken(f"dialog[open] {s['fristende']}")
        m.klick(s["dialog_speichern"])


def durchspielen(m: Messung, adressen: dict[str, Any]) -> None:
    s = _selektoren(adressen)
    m.beginnen(adressen["arbeitslisten"])
    m.klick(s["anfrage_oeffnen"])
    if not m.gibt_es(s["kuendigung_anlegen"]):
        raise NichtMoeglich("Die Anfrage bietet keinen Weg, die Kündigung anzulegen.")
    m.klick(s["kuendigung_anlegen"])
    if m.gibt_es(s["dialog_speichern"]):
        m.klick(s["dialog_speichern"])
        _wahl_falls_noetig(m, s)
    m.anhalten()
    assert "erledigt" in m.text("body"), "Die Anfrage ist nach dem Speichern nicht erledigt."


def umweg(m: Messung, adressen: dict[str, Any]) -> None:
    """Von Hand: Kündigung im Dialog des Vertrags, dann die Anfrage damit verknüpfen."""
    s = _selektoren(adressen)
    m.beginnen(adressen["arbeitslisten"])
    if not m.gibt_es(s["anfrage_oeffnen"]):
        raise NichtMoeglich(
            "entfällt: Der direkte Weg („Website-Kündigung übernehmen“) hat die Anfrage im "
            "selben Lauf schon erledigt; ein Umweg ist nicht mehr nötig."
        )
    m.klick(s["anfrage_oeffnen"])
    anfrage = _anfrage(m.text())
    s = _selektoren(adressen, anfrage["vertrag"])
    m.klick(s["vertrag"])
    m.klick(s["kuendigung_dialog"])
    m.tippe(s["zugang_am"], anfrage["gemeldet"])
    m.waehle(s["von"], r"^Mieter$")
    m.tippe(s["zum_wunsch"], anfrage["wunsch"])
    m.klick(s["dialog_speichern"])
    _wahl_falls_noetig(m, s)
    m.klick(s["nav_arbeitslisten"])
    m.klick(s["anfrage_oeffnen"])
    m.klick(s["schon_erfasst"])
    m.klick(s["anfrage_erledigt"])
    m.anhalten()
    assert "erledigt" in m.text("body"), "Die Anfrage ist nicht erledigt."
