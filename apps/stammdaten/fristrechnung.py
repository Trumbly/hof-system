"""Rechnen mit Daten: Werktage, Monats- und Quartalsenden, Fristende einer Kündigung.

Reine Funktionen ohne Datenbank. Die Regeln kommen aus ``konfiguration.toml``.
"""

from __future__ import annotations

import calendar
import datetime

from apps.stammdaten.konfiguration import Konfiguration, Kuendigungsregel, laden

EIN_TAG = datetime.timedelta(days=1)


def ostersonntag(jahr: int) -> datetime.date:
    """Ostersonntag nach der Gaußschen Osterformel (gregorianisch, anonyme Form)."""
    a = jahr % 19
    b, c = divmod(jahr, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    monat, tag = divmod(h + m - 7 * n + 114, 31)
    return datetime.date(jahr, monat, tag + 1)


def bundesweite_feiertage(jahr: int) -> frozenset[datetime.date]:
    ostern = ostersonntag(jahr)
    return frozenset(
        {
            datetime.date(jahr, 1, 1),  # Neujahr
            ostern - datetime.timedelta(days=2),  # Karfreitag
            ostern + datetime.timedelta(days=1),  # Ostermontag
            datetime.date(jahr, 5, 1),  # Tag der Arbeit
            ostern + datetime.timedelta(days=39),  # Christi Himmelfahrt
            ostern + datetime.timedelta(days=50),  # Pfingstmontag
            datetime.date(jahr, 10, 3),  # Tag der Deutschen Einheit
            datetime.date(jahr, 12, 25),
            datetime.date(jahr, 12, 26),
        }
    )


def ist_werktag(datum: datetime.date, konfiguration: Konfiguration | None = None) -> bool:
    konfiguration = konfiguration or laden()
    letzter_werktag = 5 if konfiguration.samstag_ist_werktag else 4  # 0 = Montag
    if datum.weekday() > letzter_werktag:
        return False
    if datum in bundesweite_feiertage(datum.year):
        return False
    return datum not in konfiguration.zusaetzliche_feiertage


def nter_werktag(
    beginn: datetime.date, n: int, konfiguration: Konfiguration | None = None
) -> datetime.date:
    """Der n-te Werktag ab ``beginn`` (``beginn`` mitgezählt, wenn Werktag)."""
    tag = beginn
    gezaehlt = 0
    while True:
        if ist_werktag(tag, konfiguration):
            gezaehlt += 1
            if gezaehlt == n:
                return tag
        tag += EIN_TAG


def monate_addieren(datum: datetime.date, monate: int) -> datetime.date:
    """Datum plus Monate; der Tag wird auf das Monatsende gekürzt (31.01. + 1 = 28./29.02.)."""
    index = datum.year * 12 + datum.month - 1 + monate
    jahr, monat = divmod(index, 12)
    monat += 1
    tag = min(datum.day, calendar.monthrange(jahr, monat)[1])
    return datetime.date(jahr, monat, tag)


def monatsende(datum: datetime.date) -> datetime.date:
    return datum.replace(day=calendar.monthrange(datum.year, datum.month)[1])


def periodenbeginn(datum: datetime.date, periode: str) -> datetime.date:
    if periode == "quartal":
        return datetime.date(datum.year, 3 * ((datum.month - 1) // 3) + 1, 1)
    return datum.replace(day=1)


def periodenlaenge_monate(periode: str) -> int:
    return 3 if periode == "quartal" else 1


def periodenende(datum: datetime.date, periode: str) -> datetime.date:
    beginn = periodenbeginn(datum, periode)
    return monatsende(monate_addieren(beginn, periodenlaenge_monate(periode) - 1))


def fristende(
    zugang: datetime.date, regel: Kuendigungsregel, konfiguration: Konfiguration | None = None
) -> datetime.date:
    """Letzter Tag des Vertrags bei Zugang der Kündigung am Tag ``zugang``.

    - Mit Karenz (§ 580a BGB): Zugang bis zum n-ten Werktag der Periode zählt diese mit.
      Ende ist der Ablauf der ``perioden``-ten Periode nach der zählenden.
    - Ohne Karenz: Die Frist endet nach §§ 187, 188 BGB am Tag, der im letzten Monat dem
      Zugangstag entspricht. Ende ist das Periodenende, in das dieser Tag fällt.
    """
    laenge = periodenlaenge_monate(regel.periode)
    if regel.karenz_werktage > 0:
        beginn = periodenbeginn(zugang, regel.periode)
        stichtag = nter_werktag(beginn, regel.karenz_werktage, konfiguration)
        zaehlende = beginn if zugang <= stichtag else monate_addieren(beginn, laenge)
        return periodenende(monate_addieren(zaehlende, laenge * regel.perioden), regel.periode)
    fristablauf = monate_addieren(zugang, laenge * regel.perioden)
    return periodenende(fristablauf, regel.periode)
