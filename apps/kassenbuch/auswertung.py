"""Auswertungen des Kassenbuchs (nur lesend): Monatsbericht je Objekt (H10.2) und Entnahmen je
Mandant (B9).

Beträge mit Vorzeichen: Eingang plus, Ausgang minus. Eine Stornobuchung hat die Gegenrichtung
ihres Originals und hebt es damit in jeder Summe auf.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from django.db import models
from django.db.models import Case, F, Sum, When

from apps.bank.models import Bankumsatz
from apps.kassenbuch.models import Buchung, Kategorie, Monatsabschluss
from apps.kern.models import Mandant, Objekt

NULL = Decimal("0.00")


def monatsanfang(datum: datetime.date) -> datetime.date:
    return datum.replace(day=1)


def naechster_monat(datum: datetime.date) -> datetime.date:
    return (datum.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)


def vormonat(datum: datetime.date) -> datetime.date:
    return (datum.replace(day=1) - datetime.timedelta(days=1)).replace(day=1)


def monatsende(datum: datetime.date) -> datetime.date:
    return naechster_monat(datum) - datetime.timedelta(days=1)


def vorjahresmonat(datum: datetime.date) -> datetime.date:
    return datetime.date(datum.year - 1, datum.month, 1)


def vorzeichen_summe(feld: str = "betrag", richtung: str = "richtung") -> Sum:
    """Summe mit Vorzeichen: Eingang plus, Ausgang minus."""
    return Sum(
        Case(
            When(**{richtung: Bankumsatz.Richtung.EINGANG}, then=F(feld)),
            default=-F(feld),
            output_field=models.DecimalField(max_digits=14, decimal_places=2),
        )
    )


def _summen_je_kategorie(
    buchungen: models.QuerySet[Buchung],
) -> dict[int, Decimal]:
    zeilen = buchungen.values("kategorie_id").annotate(summe=vorzeichen_summe())
    return {z["kategorie_id"]: z["summe"] or NULL for z in zeilen}


@dataclass(frozen=True)
class Berichtszeile:
    kategorie_id: int
    name: str
    betrag: Decimal
    vorjahr: Decimal

    @property
    def differenz(self) -> Decimal:
        return self.betrag - self.vorjahr


@dataclass(frozen=True)
class Entnahmezeile:
    mandant: Mandant
    betrag: Decimal
    vorjahr: Decimal


@dataclass
class Bericht:
    """Zahlen eines Monatsberichts je Objekt, mit Vergleich zum Vorjahresmonat."""

    objekt: Objekt
    monat: datetime.date
    einnahmen: list[Berichtszeile] = field(default_factory=list)
    kosten: list[Berichtszeile] = field(default_factory=list)
    entnahmen: list[Entnahmezeile] = field(default_factory=list)
    umbuchungen: list[Berichtszeile] = field(default_factory=list)
    anzahl_buchungen: int = 0
    abgeschlossen: bool = False

    @property
    def vorjahresmonat(self) -> datetime.date:
        return vorjahresmonat(self.monat)

    @property
    def summe_einnahmen(self) -> Decimal:
        return sum((z.betrag for z in self.einnahmen), NULL)

    @property
    def summe_kosten(self) -> Decimal:
        return sum((z.betrag for z in self.kosten), NULL)

    @property
    def ergebnis(self) -> Decimal:
        return self.summe_einnahmen - self.summe_kosten

    @property
    def vorjahr_einnahmen(self) -> Decimal:
        return sum((z.vorjahr for z in self.einnahmen), NULL)

    @property
    def vorjahr_kosten(self) -> Decimal:
        return sum((z.vorjahr for z in self.kosten), NULL)

    @property
    def vorjahr_ergebnis(self) -> Decimal:
        return self.vorjahr_einnahmen - self.vorjahr_kosten

    @property
    def summe_entnahmen(self) -> Decimal:
        return sum((z.betrag for z in self.entnahmen), NULL)

    @property
    def dateiname(self) -> str:
        """Objektkürzel und Monat, ohne Personennamen."""
        return f"monatsbericht-{self.objekt.kuerzel}-{self.monat:%Y-%m}.pdf"


def _buchungen_im_monat(objekt: Objekt, monat: datetime.date) -> models.QuerySet[Buchung]:
    return Buchung.objects.filter(objekt=objekt, datum__gte=monat, datum__lte=monatsende(monat))


def monat_abgeschlossen(monat: datetime.date) -> bool:
    """Ist der Monat für jeden Mandanten mit Umsätzen in diesem Monat abgeschlossen?"""
    mandanten = set(
        Bankumsatz.objects.filter(
            buchungsdatum__gte=monat, buchungsdatum__lte=monatsende(monat)
        ).values_list("konto__mandant_id", flat=True)
    )
    if not mandanten:
        return False
    abgeschlossen = set(
        Monatsabschluss.objects.filter(
            monat=monat, mandant_id__in=mandanten, abgeschlossen_am__isnull=False
        ).values_list("mandant_id", flat=True)
    )
    return mandanten <= abgeschlossen


def bericht(objekt: Objekt, monat: datetime.date) -> Bericht:
    """Einnahmen, Kosten, Ergebnis und Entnahmen eines Objekts im Monat, mit Vorjahresmonat."""
    monat = monatsanfang(monat)
    jetzt = _buchungen_im_monat(objekt, monat)
    summen = _summen_je_kategorie(jetzt)
    vorjahr = _summen_je_kategorie(_buchungen_im_monat(objekt, vorjahresmonat(monat)))
    kategorien = {k.pk: k for k in Kategorie.objects.filter(pk__in=set(summen) | set(vorjahr))}

    ergebnis = Bericht(objekt=objekt, monat=monat, anzahl_buchungen=jetzt.count())
    entnahmen: dict[int, tuple[Mandant, Decimal, Decimal]] = {}
    for kategorie in sorted(kategorien.values(), key=lambda k: k.bezeichnung):
        betrag = summen.get(kategorie.pk, NULL)
        alt = vorjahr.get(kategorie.pk, NULL)
        if kategorie.art == Kategorie.Art.EINNAHME:
            ergebnis.einnahmen.append(
                Berichtszeile(kategorie.pk, kategorie.bezeichnung, betrag, alt)
            )
        elif kategorie.art == Kategorie.Art.AUSGABE:
            ergebnis.kosten.append(
                Berichtszeile(kategorie.pk, kategorie.bezeichnung, -betrag, -alt)
            )
        elif kategorie.art == Kategorie.Art.ENTNAHME and kategorie.mandant is not None:
            mandant, summe, summe_alt = entnahmen.get(
                kategorie.mandant_id or 0, (kategorie.mandant, NULL, NULL)
            )
            entnahmen[mandant.pk] = (mandant, summe - betrag, summe_alt - alt)
        else:
            ergebnis.umbuchungen.append(
                Berichtszeile(kategorie.pk, kategorie.bezeichnung, betrag, alt)
            )
    ergebnis.entnahmen = [
        Entnahmezeile(mandant, betrag, alt)
        for mandant, betrag, alt in sorted(entnahmen.values(), key=lambda e: e[0].kuerzel)
    ]
    ergebnis.abgeschlossen = monat_abgeschlossen(monat)
    return ergebnis


def entnahmen_je_mandant(
    von: datetime.date,
    bis: datetime.date,
    *,
    objekt: Objekt | None = None,
) -> list[tuple[Mandant, Decimal]]:
    """Entnahmen (B9) im Zeitraum, getrennt je Mandant der Entnahme-Kategorie (positiv)."""
    buchungen = Buchung.objects.filter(
        kategorie__art=Kategorie.Art.ENTNAHME, datum__gte=von, datum__lte=bis
    )
    if objekt is not None:
        buchungen = buchungen.filter(objekt=objekt)
    zeilen = buchungen.values("kategorie__mandant_id").annotate(summe=vorzeichen_summe())
    summen = {z["kategorie__mandant_id"]: -(z["summe"] or NULL) for z in zeilen}
    mandanten = Mandant.objects.filter(pk__in=summen).order_by("kuerzel")
    return [(m, summen[m.pk]) for m in mandanten]
