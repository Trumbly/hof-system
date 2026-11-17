"""Leerstand je Einheit: Tage frei und entgangener Ertrag nach Zielmiete (H2.11).

Ein Tag zählt als frei, wenn die Einheit an ihm den Status „frei“ hat und kein laufender
oder beendeter Vertrag ihn abdeckt (eine Rückgabe vor dem Vertragsende kostet also keinen
Ertrag). Der entgangene Ertrag ist je freiem Tag die an ihm gültige Zielmiete geteilt durch
die Tage des Monats. Tage ohne Zielmiete zählen mit, aber ohne Betrag.

Nur lesend; Wohnraum (Verwaltio) ist ausgenommen.
"""

from __future__ import annotations

import calendar
import datetime
from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Prefetch, Q

from apps.stammdaten.models import Einheit, EinheitStatus, Vertrag, Zielmiete

EIN_TAG = datetime.timedelta(days=1)
ABDECKEND = (Vertrag.Status.AKTIV, Vertrag.Status.GEKUENDIGT, Vertrag.Status.BEENDET)


@dataclass(frozen=True)
class Leerstand:
    einheit: Einheit
    tage_frei: int
    #: Entgangener Ertrag nach Zielmiete, auf Cent gerundet.
    entgangen: Decimal
    #: Freie Tage ohne gültige Zielmiete (Ertrag dort unbekannt).
    tage_ohne_zielmiete: int
    #: Seit wann die Einheit am Ende des Zeitraums frei ist (oder None).
    frei_seit: datetime.date | None
    zielmiete_aktuell: Decimal | None


def _tage(von: datetime.date, bis: datetime.date) -> list[datetime.date]:
    anzahl = (bis - von).days + 1
    return [von + datetime.timedelta(days=i) for i in range(max(anzahl, 0))]


def berechnen(
    von: datetime.date, bis: datetime.date, *, nur_mit_leerstand: bool = True
) -> list[Leerstand]:
    """Leerstand im Zeitraum ``von`` bis ``bis`` (beide eingeschlossen), größter Ertrag zuerst."""
    einheiten = (
        Einheit.objects.exclude(typ=Einheit.Typ.WOHNUNG)
        .filter(Q(aufgeloest_am__isnull=True) | Q(aufgeloest_am__gt=von))
        .select_related("objekt")
        .prefetch_related(
            Prefetch(
                "statusstaende",
                queryset=EinheitStatus.objects.filter(status=EinheitStatus.Status.FREI),
                to_attr="frei_staende",
            ),
            Prefetch("zielmieten", queryset=Zielmiete.objects.order_by("gueltig_ab")),
            Prefetch("vertraege", queryset=Vertrag.objects.filter(status__in=ABDECKEND)),
        )
    )
    ergebnis = []
    for einheit in einheiten:
        zielmieten = list(einheit.zielmieten.all())
        vertraege = list(einheit.vertraege.all())
        frei_seit = None
        tage_frei = 0
        ohne = 0
        summe = Decimal("0")
        for stand in einheit.frei_staende:  # type: ignore[attr-defined]
            beginn = max(stand.gueltig_ab, von)
            ende = min(stand.gueltig_bis or bis, bis)
            for tag in _tage(beginn, ende):
                if any(v.beginn <= tag and (v.ende is None or tag <= v.ende) for v in vertraege):
                    continue
                tage_frei += 1
                miete = next((z.betrag for z in zielmieten if z.gilt_am(tag)), None)
                if miete is None:
                    ohne += 1
                else:
                    summe += miete / calendar.monthrange(tag.year, tag.month)[1]
            if stand.gilt_am(bis):
                frei_seit = stand.gueltig_ab
        if nur_mit_leerstand and not tage_frei:
            continue
        aktuell = next((z.betrag for z in zielmieten if z.gilt_am(bis)), None)
        ergebnis.append(
            Leerstand(
                einheit=einheit,
                tage_frei=tage_frei,
                entgangen=summe.quantize(Decimal("0.01")),
                tage_ohne_zielmiete=ohne,
                frei_seit=frei_seit,
                zielmiete_aktuell=aktuell,
            )
        )
    ergebnis.sort(key=lambda z: (-z.entgangen, -z.tage_frei, z.einheit.nw_id))
    return ergebnis


def summe(zeilen: list[Leerstand]) -> Decimal:
    return sum((z.entgangen for z in zeilen), Decimal("0.00"))
