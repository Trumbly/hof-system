"""Website-Kündigung übernehmen: Kündigung für NW-S-0001 (V-0010) von der Website.

Ziel (Review 2, A3): An der Anfrage öffnet „Kündigung anlegen“ den Kündigungsdialog des
Vertrags, vorausgefüllt (Zugang = Meldedatum, gekündigt von Mieter, Wunschtermin); nach dem
Speichern ist die Anfrage erledigt und mit der Kündigung verknüpft.

Heute gibt es den Knopf nicht: „Kündigung erfasst“ setzt nur die Anfrage auf erledigt. Der
Ablauf endet dann mit ``NichtMoeglich``. ``umweg`` misst zusätzlich den Weg von Hand
(Kündigung am Vertrag erfassen, dann die Anfrage auf „Kündigung erfasst“ setzen); Anfrage und
Kündigung sind dabei nicht verknüpft.
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
    # Ziel aus A3 (gibt es heute nicht)
    "kuendigung_anlegen": "main :is(button, a):has-text('Kündigung anlegen')",
    "dialog_speichern": (
        "dialog[open] button[type=submit], main form button:has-text('Kündigung erfassen')"
    ),
    # Umweg von Hand
    "vertrag": "main a:has-text('{vertrag}')",
    "zugang_am": "input[name=kuendigung-zugang_am]",
    "von": "select[name=kuendigung-von]",
    "zum_wunsch": "input[name=kuendigung-zum_wunsch]",
    "kuendigung_erfassen": "main button:has-text('Kündigung erfassen')",
    "nav_arbeitslisten": "nav a:has-text('Arbeitslisten')",
    "anfrage_erledigt": "main button:has-text('Kündigung erfasst')",
}


def _anfrage(text: str) -> dict[str, str]:
    def datum(muster: str) -> str:
        treffer = re.search(muster + r"\s*(\d\d\.\d\d\.\d{4})", text)
        if not treffer:
            return ""
        return datetime.datetime.strptime(treffer[1], "%d.%m.%Y").date().isoformat()

    vertrag = re.search(r"Vertrag: (V-\d{4})", text)
    return {
        "gemeldet": datum("Gemeldet am"),
        "wunsch": datum("Gewünscht ab"),
        "vertrag": vertrag[1] if vertrag else "",
    }


def _selektoren(adressen: dict[str, Any], vertrag: str = "") -> dict[str, str]:
    posten = adressen["posten"]["anfrage_kuendigung"]
    return {k: v.format(posten=posten, vertrag=vertrag) for k, v in SELEKTOREN.items()}


def durchspielen(m: Messung, adressen: dict[str, Any]) -> None:
    s = _selektoren(adressen)
    m.beginnen(adressen["arbeitslisten"])
    m.klick(s["anfrage_oeffnen"])
    if not m.gibt_es(s["kuendigung_anlegen"]):
        raise NichtMoeglich(
            "Die Anfrage bietet keinen Weg, die Kündigung anzulegen. „Kündigung erfasst“ "
            "setzt nur die Anfrage auf erledigt; der Vertrag bleibt aktiv, ohne Kündigung, "
            "Bestätigung und Fristen (Review 2, A3)."
        )
    m.klick(s["kuendigung_anlegen"])
    m.klick(s["dialog_speichern"])
    m.anhalten()
    assert "erledigt" in m.text(), "Die Anfrage ist nach dem Speichern nicht erledigt."


def umweg(m: Messung, adressen: dict[str, Any]) -> None:
    """Heute von Hand: Kündigung am Vertrag erfassen, Anfrage auf „Kündigung erfasst“."""
    s = _selektoren(adressen)
    m.beginnen(adressen["arbeitslisten"])
    m.klick(s["anfrage_oeffnen"])
    anfrage = _anfrage(m.text())
    s = _selektoren(adressen, anfrage["vertrag"])
    m.klick(s["vertrag"])
    m.tippe(s["zugang_am"], anfrage["gemeldet"])
    m.waehle(s["von"], r"^Mieter$")
    m.tippe(s["zum_wunsch"], anfrage["wunsch"])
    m.klick(s["kuendigung_erfassen"])
    m.klick(s["nav_arbeitslisten"])
    m.klick(s["anfrage_oeffnen"])
    m.klick(s["anfrage_erledigt"])
    m.anhalten()
    assert "erledigt" in m.text(), "Die Anfrage ist nicht erledigt."
