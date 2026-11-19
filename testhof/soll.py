"""Testhof-Modul der Sollstellung (Schritt 5): Soll aller Verträge bis zum Stichtag.

- Das Hof-System führt das Soll im Testhof ab ``SOLL_AB`` (24 Monate vor dem Stichtag,
  der Testzeitraum); frühere Perioden „führt das alte System“. Gesetzt je Vertrag über die
  Sollregel, damit es nicht von ``HOF_SOLL_BEGINN`` abhängt.
- Erzeugt wird wie im Lauf ``soll_monat`` am Stichtag (``aufbau.STICHTAG``), nie zu „heute“.
- **Sonderfälle** (``testhof.sonderfaelle``), die Verträge legt ``testhof.stammdaten`` an:
  ``vertrag_monatsmitte`` (Teilmonate am Beginn 15.03.2025 und am Ende 15.06.2026),
  ``staffelmiete`` (Stufe am 01.01.2026), ``jahreszahler`` (Zahlweise jährlich, ein Posten
  je Kalenderjahr).

Geschrieben wird über ``apps.soll.services``.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field

from apps.soll import services
from apps.soll.models import Sollposten
from apps.stammdaten.models import Kondition, Vertrag
from testhof import register
from testhof.aufbau import Kontext
from testhof.stammdaten import StammdatenDaten

NAME = "soll"
SONDERFAELLE = ("vertrag_monatsmitte", "staffelmiete", "jahreszahler")

#: Beginn der Sollstellung im Testhof: 24 Monate vor dem Stichtag (01.11.2026).
SOLL_AB = datetime.date(2024, 11, 1)


@dataclass
class SollDaten:
    ergebnis: services.Ergebnis
    #: Sonderfall-Schlüssel -> beteiligte Verträge
    sonderfaelle: dict[str, list[Vertrag]] = field(default_factory=dict)

    def posten(self, vertrag: Vertrag) -> list[Sollposten]:
        return list(
            Sollposten.objects.filter(vertrag=vertrag)
            .exclude(status=Sollposten.Status.STORNIERT)
            .order_by("von", "art")
        )


def aufbauen(kontext: Kontext) -> SollDaten:
    stammdaten: StammdatenDaten = kontext.ablage["stammdaten"]
    vertraege = sorted(stammdaten.vertraege.values(), key=lambda v: v.nummer)
    with services.ohne_sollstellung("Testhof setzt die Regeln vor dem Lauf"):
        for vertrag in vertraege:
            services.sollregel_setzen(vertrag, soll_ab=SOLL_AB)
    ergebnis = services.monatslauf(kontext.stichtag)

    jahreszahler = [
        v
        for v in vertraege
        if v.konditionen.filter(zahlweise=Kondition.Zahlweise.JAEHRLICH).exists()
    ]
    return SollDaten(
        ergebnis=ergebnis,
        sonderfaelle={
            "vertrag_monatsmitte": list(stammdaten.sonderfaelle["vertrag_monatsmitte"]),
            "staffelmiete": list(stammdaten.sonderfaelle["staffelmiete"]),
            "jahreszahler": jahreszahler,
        },
    )


register.registrieren(
    NAME,
    abhaengig_von=("stammdaten",),
    aufbauen=aufbauen,
    sonderfaelle=SONDERFAELLE,
)
