"""Rechnung der Sollstellung, ohne Datenbank: aus Konditionen werden Planposten.

Regeln (festgelegt in Schritt 5, getestet in ``tests/soll/test_rechnung.py``):

1. **Perioden.** Die Zahlweise der Kondition legt ein Raster im Kalender fest: monatlich je
   Kalendermonat, vierteljährlich ab Januar, April, Juli, Oktober, halbjährlich ab Januar und
   Juli, jährlich je Kalenderjahr. Eine fehlende Zahlweise gilt als monatlich.
2. **Teilzeiträume.** Jede Periode wird mit der Gültigkeit der Kondition und der Laufzeit
   des Vertrags geschnitten. Beginnt oder endet der Vertrag mitten in der Periode oder
   wechselt dort die Kondition (Staffel, Mieterhöhung), entsteht je Teil ein eigener Posten.
3. **Anteil taggenau nach Kalendertagen.** Ein Teilzeitraum kostet je berührtem
   Kalendermonat ``Monatsbetrag × Tage im Teilzeitraum / Tage des Monats``; ein voller
   Monat ist immer ein voller Monatsbetrag, gleich wie viele Tage er hat.
4. **Rundung auf Cent, Rest im letzten Teilposten.** Je Periode und Art wird die Summe der
   genauen Anteile kaufmännisch auf Cent gerundet. Alle Teilposten außer dem letzten werden
   für sich gerundet; der letzte bekommt den Rest, damit die Teile zusammen genau die
   gerundete Summe ergeben.
5. **Steuer** je Posten: ``netto × Steuersatz / 100``, kaufmännisch auf Cent gerundet.
   Der Betrag ist netto plus Steuer. Miete und Pauschale sind getrennte Posten mit dem
   Steuersatz der Kondition.
6. **Fälligkeit** nach der Regel des Vertrags (Standard: dritter Werktag des Monats, in dem
   der Teilzeitraum beginnt), frühestens am ersten (Werk-)Tag des Teilzeitraums.
7. **Übergang vom alten System.** Perioden, die vor dem Beginn der Sollstellung im
   Hof-System (``soll_ab``) anfangen, führt das alte System; sie entstehen hier nicht.
8. **Bis-Datum.** Es entstehen die Posten, deren Teilzeitraum spätestens am Bis-Datum
   beginnt. Die Rundung hängt nicht davon ab: Die Periode wird immer ganz gerechnet.
"""

from __future__ import annotations

import calendar
import datetime
import math
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

from apps.stammdaten import fristrechnung

EIN_TAG = datetime.timedelta(days=1)
CENT = Decimal("0.01")

#: Monate je Periode für jede Zahlweise der Kondition ("" = noch nicht erfasst: monatlich).
MONATE_JE_ZAHLWEISE: dict[str, int] = {
    "": 1,
    "monatlich": 1,
    "vierteljaehrlich": 3,
    "halbjaehrlich": 6,
    "jaehrlich": 12,
}

#: Arten, die aus der Kondition entstehen, mit dem Feld der Kondition.
ARTEN_AUS_KONDITION = (("miete", "miete"), ("pauschale", "pauschale"))


@dataclass(frozen=True)
class Stufe:
    """Eine Kondition, so wie die Rechnung sie braucht."""

    von: datetime.date
    bis: datetime.date | None
    miete: Decimal
    pauschale: Decimal
    steuersatz: Decimal
    zahlweise: str


@dataclass(frozen=True)
class Faelligkeitsregel:
    art: str  # "werktag" oder "kalendertag"
    tag: int
    land: str | None = "NW"
    samstag_ist_werktag: bool = False


@dataclass(frozen=True)
class Planposten:
    art: str
    von: datetime.date
    bis: datetime.date
    netto: Decimal
    steuersatz: Decimal
    steuer: Decimal
    betrag: Decimal
    faellig_am: datetime.date

    @property
    def schluessel(self) -> tuple[str, datetime.date, datetime.date]:
        return (self.art, self.von, self.bis)


# --- Bausteine -----------------------------------------------------------------------


def runden(wert: Fraction | Decimal) -> Decimal:
    """Kaufmännisch auf Cent (ab einem halben Cent aufwärts, für positive Werte)."""
    genau = Fraction(wert)
    cent = math.floor(genau * 100 + Fraction(1, 2))
    return (Decimal(cent) / 100).quantize(CENT)


def periode(datum: datetime.date, monate: int) -> tuple[datetime.date, datetime.date]:
    """Die Periode im Raster von ``monate`` Monaten (ab Januar), in die ``datum`` fällt."""
    erster_monat = ((datum.month - 1) // monate) * monate + 1
    beginn = datetime.date(datum.year, erster_monat, 1)
    ende = fristrechnung.monatsende(fristrechnung.monate_addieren(beginn, monate - 1))
    return beginn, ende


def monatsanteil(von: datetime.date, bis: datetime.date) -> Fraction:
    """Anzahl Monate in ``von``–``bis``, je Kalendermonat taggenau (Regel 3)."""
    summe = Fraction(0)
    tag = von
    while tag <= bis:
        tage_im_monat = calendar.monthrange(tag.year, tag.month)[1]
        ende = min(bis, fristrechnung.monatsende(tag))
        summe += Fraction((ende - tag).days + 1, tage_im_monat)
        tag = ende + EIN_TAG
    return summe


def faellig_am(von: datetime.date, regel: Faelligkeitsregel) -> datetime.date:
    """Fälligkeit eines Postens, der am Tag ``von`` beginnt (Regel 6)."""
    erster = von.replace(day=1)
    if regel.art == "kalendertag":
        tag = min(regel.tag, calendar.monthrange(von.year, von.month)[1])
        return max(erster.replace(day=tag), von)

    def werktag(n: int, ab: datetime.date) -> datetime.date:
        return fristrechnung.nter_werktag(
            ab, n, land=regel.land, samstag_ist_werktag=regel.samstag_ist_werktag
        )

    stichtag = werktag(regel.tag, erster)
    return stichtag if stichtag >= von else werktag(1, von)


# --- Plan ----------------------------------------------------------------------------


@dataclass
class _Teil:
    art: str
    von: datetime.date
    bis: datetime.date
    genau: Fraction
    steuersatz: Decimal


def plan(
    stufen: Iterable[Stufe],
    *,
    beginn: datetime.date,
    ende: datetime.date | None,
    bis_datum: datetime.date,
    regel: Faelligkeitsregel,
    soll_ab: datetime.date | None = None,
) -> list[Planposten]:
    """Alle Posten aus den Stufen für den Vertrag ``beginn``–``ende`` bis ``bis_datum``."""
    uebergang = soll_ab if soll_ab is not None and soll_ab > beginn else None
    gruppen: dict[tuple[str, int, datetime.date], list[_Teil]] = {}
    for stufe in sorted(stufen, key=lambda s: s.von):
        s_von = max(stufe.von, beginn)
        enden = [d for d in (stufe.bis, ende) if d is not None]
        s_bis = min(enden) if enden else None
        if s_bis is not None and s_bis < s_von:
            continue
        monate = MONATE_JE_ZAHLWEISE.get(stufe.zahlweise, 1)
        p_von, p_bis = periode(s_von, monate)
        while p_von <= bis_datum and (s_bis is None or p_von <= s_bis):
            if uebergang is None or p_von >= uebergang:
                t_von = max(p_von, s_von)
                t_bis = p_bis if s_bis is None else min(p_bis, s_bis)
                anteil = monatsanteil(t_von, t_bis)
                for art, feld in ARTEN_AUS_KONDITION:
                    monatsbetrag: Decimal = getattr(stufe, feld)
                    if monatsbetrag <= 0:
                        continue
                    gruppen.setdefault((art, monate, p_von), []).append(
                        _Teil(art, t_von, t_bis, Fraction(monatsbetrag) * anteil, stufe.steuersatz)
                    )
            p_von = p_bis + EIN_TAG
            p_bis = periode(p_von, monate)[1]

    ergebnis: list[Planposten] = []
    for teile in gruppen.values():
        summe = runden(sum((t.genau for t in teile), Fraction(0)))
        bisher = Decimal("0.00")
        for nummer, teil in enumerate(teile):
            letzter = nummer == len(teile) - 1
            netto = summe - bisher if letzter else runden(teil.genau)
            bisher += netto
            if netto <= 0 or teil.von > bis_datum:
                continue
            steuer = runden(netto * teil.steuersatz / 100)
            ergebnis.append(
                Planposten(
                    art=teil.art,
                    von=teil.von,
                    bis=teil.bis,
                    netto=netto,
                    steuersatz=teil.steuersatz,
                    steuer=steuer,
                    betrag=netto + steuer,
                    faellig_am=faellig_am(teil.von, regel),
                )
            )
    reihenfolge = {art: i for i, (art, _) in enumerate(ARTEN_AUS_KONDITION)}
    return sorted(ergebnis, key=lambda p: (p.von, reihenfolge[p.art]))


def ueberschneiden(
    a_von: datetime.date, a_bis: datetime.date, b_von: datetime.date, b_bis: datetime.date
) -> bool:
    return a_von <= b_bis and b_von <= a_bis
