"""Andockstelle zur App ``fristen`` (Schritt 9).

Die Stammdaten melden hier Fristen an: Rückgabetermin nach einer Kündigung, Frist für
die Kautionsabrechnung, Ende eines befristeten Vertrags.

**Heute (vor Schritt 9):** Jede Frist wird ein Arbeitsposten der Art ``ART`` mit
Fälligkeit, Bezug und der Fristart im Hinweis. So steht sie in der Arbeitsliste.

**Umstellung, sobald ``apps.fristen`` steht:** Nur ``fristen_anfordern`` ändert sich. Es ruft
dann den Service der Fristen-App, etwa
``fristen.services.frist_anlegen(art=art, datum=datum, bezug=bezug, quelle="stammdaten")``,
und legt keinen Arbeitsposten mehr an. Die Aufrufer in ``stammdaten.services`` bleiben gleich.
"""

from __future__ import annotations

import datetime

from django.db import models

from apps.kern import services as kern
from apps.kern.models import Arbeitsposten

#: Art der Arbeitsposten, solange die Fristen-App fehlt.
ART = "Frist"

#: Fristarten der Stammdaten mit ihrem Titel (Schlüssel bleiben bei der Umstellung gleich).
FRISTARTEN: dict[str, str] = {
    "rueckgabetermin": "Rückgabetermin",
    "kautionsabrechnung": "Kautionsabrechnung",
    "vertragsende": "Vertragsende (befristet)",
}


def fristen_anfordern(
    art: str, datum: datetime.date, bezug: models.Model, hinweis: str = ""
) -> Arbeitsposten:
    """Meldet eine Frist an. Bis Schritt 9: ein Arbeitsposten mit Fälligkeit ``datum``.

    ``hinweis`` nennt nur IDs (z. B. „Vertrag V-0003“), keine Namen.
    """
    if art not in FRISTARTEN:
        raise ValueError(f"Unbekannte Fristart {art}")
    text = FRISTARTEN[art]
    if hinweis:
        text = f"{text}: {hinweis}"
    return kern.arbeitsposten_anlegen(ART, bezug=bezug, faellig_am=datum, hinweis=text)
