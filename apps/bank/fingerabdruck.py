"""Fingerabdruck eines Bankumsatzes gegen Doppelte (H3.1, H3.3).

Der Fingerabdruck ist ein SHA-256 über
    Konto (seine IBAN), Buchungsdatum, Betrag, Richtung, Gegen-IBAN, Verwendungszweck und die
    laufende Nummer am Tag.

Die laufende Nummer zählt innerhalb einer Lieferung die Umsätze, die in allen anderen
Teilen gleich sind (etwa zwei gleiche Zahlungen desselben Zahlers am selben Tag): Der
erste bekommt 1, der zweite 2. Ein erneuter Abruf desselben Zeitraums liefert dieselben
Umsätze in derselben Reihenfolge und damit dieselben Fingerabdrücke; die Datenbank nimmt
jeden nur einmal an (Unique-Constraint). Kommt der zweite von zwei gleichen Umsätzen erst
mit einem späteren Abruf, hat er dort die Nummer 2 und ist neu.

Voraussetzung: Eine Lieferung enthält immer ganze Tage. Abruf, Zulieferung und Import
liefern Zeiträume von Tag zu Tag.

Normalisiert wird so, dass kleine Unterschiede der Darstellung keinen neuen Umsatz
ergeben: IBAN ohne Leerzeichen in Großbuchstaben, Zweck mit einfachen Leerzeichen,
Betrag mit zwei Nachkommastellen.
"""

from __future__ import annotations

import datetime
import hashlib
import re
from collections.abc import Iterable
from decimal import Decimal
from typing import Protocol

TRENNER = "\x1f"  # Unit Separator: kommt in keinem der Felder vor


class Umsatzkern(Protocol):
    """Die Felder, aus denen der Fingerabdruck entsteht."""

    @property
    def buchungsdatum(self) -> datetime.date: ...
    @property
    def betrag(self) -> Decimal: ...
    @property
    def richtung(self) -> str: ...
    @property
    def gegen_iban(self) -> str: ...
    @property
    def verwendungszweck(self) -> str: ...


def iban_normal(iban: str) -> str:
    return re.sub(r"\s+", "", iban or "").upper()


def zweck_normal(zweck: str) -> str:
    return re.sub(r"\s+", " ", zweck or "").strip()


def betrag_normal(betrag: Decimal) -> str:
    return f"{Decimal(betrag).quantize(Decimal('0.01')):.2f}"


def schluessel(konto: str, umsatz: Umsatzkern) -> tuple[str, ...]:
    """Alles außer der laufenden Nummer: gleiche Schlüssel am selben Tag werden gezählt."""
    return (
        iban_normal(konto),
        umsatz.buchungsdatum.isoformat(),
        betrag_normal(umsatz.betrag),
        umsatz.richtung,
        iban_normal(umsatz.gegen_iban),
        zweck_normal(umsatz.verwendungszweck),
    )


def fingerabdruck(teile: tuple[str, ...], laufende_nummer: int) -> str:
    text = TRENNER.join((*teile, str(laufende_nummer)))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def laufende_nummern(konto: str, umsaetze: Iterable[Umsatzkern]) -> list[tuple[int, str]]:
    """Laufende Nummer und Fingerabdruck je Umsatz, in der Reihenfolge der Lieferung."""
    gezaehlt: dict[tuple[str, ...], int] = {}
    ergebnis = []
    for umsatz in umsaetze:
        teile = schluessel(konto, umsatz)
        nummer = gezaehlt.get(teile, 0) + 1
        gezaehlt[teile] = nummer
        ergebnis.append((nummer, fingerabdruck(teile, nummer)))
    return ergebnis
