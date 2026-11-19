"""Testhof-Modul der Sollstellung (Schritt 5): Soll aller Verträge bis zum Stichtag.

- **Umzug** (Review 1, Punkt 1): Das Hof-System führt das Soll im Testhof ab ``SOLL_AB``,
  dem Stichtag des Umzugs, und der ist derselbe Tag wie der Beginn des Abgleichs
  (``testhof.abgleich.BEGINN``, im Betrieb ``HOF_ABGLEICH_BEGINN``). Soll, Abgleich und
  Mahnlauf beginnen damit am selben Tag; nichts liegt ungeprüft „offen“ davor. Frühere
  Perioden führt das alte System. Gesetzt je Vertrag über die Sollregel, damit es nicht von
  ``HOF_SOLL_BEGINN`` abhängt. Der Jahreszahler beginnt mit seinem Jahr 2026 (die Periode,
  in die der Umzug fällt; er zahlt sie im Juli, ``testhof.abgleich``).
- **Vortrag**: Was beim Umzug im alten System offen war, steht als ein Posten „Vortrag“ am
  Stichtag. Im Testhof hat ihn der Vertrag mit dem Dauerauftrag über die alte Miete
  (``dauerauftrag_alte_miete``): Seit der Staffel am 01.01.2026 fehlt jeden Monat die
  Differenz, bis zum Umzug also sechs Monate (``VORTRAG_MONATE``). Alle anderen Verträge
  haben keinen Vortrag.
- Erzeugt wird wie im Lauf ``soll_monat`` am Stichtag (``aufbau.STICHTAG``), nie zu „heute“.
- **Sonderfälle** (``testhof.sonderfaelle``), die Verträge legt ``testhof.stammdaten`` an:
  ``vertrag_monatsmitte`` (Teilmonate am Beginn 15.03.2025 und am Ende 15.06.2026),
  ``staffelmiete`` (Stufe am 01.01.2026), ``jahreszahler`` (Zahlweise jährlich, ein Posten
  je Kalenderjahr). Teilmonate und Staffel liegen vor dem Umzug; ihre Tests stellen das
  Soll des Vertrags dafür ab einem früheren Tag (``sollregel_setzen``).

Geschrieben wird über ``apps.soll.services``.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from apps.soll import services
from apps.soll.models import Sollposten
from apps.stammdaten.models import Kondition, Vertrag
from testhof import register
from testhof.aufbau import Kontext
from testhof.stammdaten import StammdatenDaten

NAME = "soll"
SONDERFAELLE = ("vertrag_monatsmitte", "staffelmiete", "jahreszahler")

#: Beginn der Sollstellung im Testhof: der Stichtag des Umzugs, gleich dem Beginn des
#: Abgleichs (``testhof.abgleich.BEGINN``).
SOLL_AB = datetime.date(2026, 7, 1)
#: Der Jahreszahler beginnt mit der Periode, in die der Umzug fällt.
SOLL_AB_JAHRESZAHLER = datetime.date(2026, 1, 1)
#: Vortrag des Dauerauftrags mit alter Miete: Monate seit der Staffel bis zum Umzug.
VORTRAG_MONATE = 6


@dataclass
class SollDaten:
    ergebnis: services.Ergebnis
    #: Vertrag-ID -> Vortrag
    vortraege: dict[int, Sollposten] = field(default_factory=dict)
    #: Sonderfall-Schlüssel -> beteiligte Verträge
    sonderfaelle: dict[str, list[Vertrag]] = field(default_factory=dict)

    def posten(self, vertrag: Vertrag) -> list[Sollposten]:
        return list(
            Sollposten.objects.filter(vertrag=vertrag)
            .exclude(status=Sollposten.Status.STORNIERT)
            .order_by("von", "art")
        )


def _monatsbetrag(kondition: Kondition | None) -> Decimal:
    if kondition is None:
        return Decimal("0.00")
    netto = kondition.miete + kondition.pauschale
    return (netto + netto * kondition.steuersatz / 100).quantize(Decimal("0.01"))


def vortrag_dauerauftrag(vertrag: Vertrag) -> Decimal:
    """Was dem Dauerauftrag mit alter Miete bis zum Umzug fehlt: sechs Monate Differenz
    zwischen der Miete nach und vor der Staffel."""
    alt = _monatsbetrag(vertrag.kondition_am(datetime.date(2025, 12, 31)))
    neu = _monatsbetrag(vertrag.kondition_am(datetime.date(2026, 1, 1)))
    return (neu - alt) * VORTRAG_MONATE


def aufbauen(kontext: Kontext) -> SollDaten:
    # Spät importiert: die Indizes der Sonderfälle stehen im Abgleich.
    from testhof.abgleich import DAUERAUFTRAG

    stammdaten: StammdatenDaten = kontext.ablage["stammdaten"]
    vertraege = sorted(stammdaten.vertraege.values(), key=lambda v: v.nummer)
    jahreszahler = [
        v
        for v in vertraege
        if v.konditionen.filter(zahlweise=Kondition.Zahlweise.JAEHRLICH).exists()
    ]
    with services.ohne_sollstellung("Testhof setzt die Regeln vor dem Lauf"):
        for vertrag in vertraege:
            ab = SOLL_AB_JAHRESZAHLER if vertrag in jahreszahler else SOLL_AB
            services.sollregel_setzen(vertrag, soll_ab=ab)
    ergebnis = services.monatslauf(kontext.stichtag)

    dauerauftrag = vertraege[DAUERAUFTRAG]
    vortrag = services.vortrag_setzen(dauerauftrag, vortrag_dauerauftrag(dauerauftrag))
    return SollDaten(
        ergebnis=ergebnis,
        vortraege={dauerauftrag.pk: vortrag} if vortrag is not None else {},
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
