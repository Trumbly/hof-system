"""Die Dokumentarten der Ablage als Konfiguration (H6.1, H6.2, H6.8, H8.6).

Eine Dokumentart legt fest,
- wie sie heißt (``titel``) und mit welchem Wort der Dateiname beginnt (der Schlüssel),
- ob aus dem Dokument ein ``Beleg`` wird, der an eine Buchung gehört (``beleg``).
  Mahnungen von Lieferanten, Rücklastschriften und Vollstreckungen liegen am Lieferanten,
  ohne Buchung (H6.8),
- wie lange es aufzubewahren ist: ``DOKUMENTE["LOESCHFRIST_JAHRE"]`` in den Einstellungen,
  gerechnet ab dem Ende des Jahres, in dem das Dokument datiert ist (wie § 147 AO und
  § 257 HGB). Fehlt eine Art dort, gilt ``STANDARD_LOESCHFRIST_JAHRE``.

Den Löschlauf (H8.6) gibt es erst in v2; bis dahin steht die Frist nur am Dokument.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from django.conf import settings

STANDARD_LOESCHFRIST_JAHRE = 10


class UnbekannteDokumentart(KeyError):
    pass


@dataclass(frozen=True)
class Dokumentart:
    schluessel: str
    titel: str
    beleg: bool = False

    @property
    def loeschfrist_jahre(self) -> int:
        jahre: dict[str, int] = settings.DOKUMENTE.get("LOESCHFRIST_JAHRE", {})
        return int(jahre.get(self.schluessel, STANDARD_LOESCHFRIST_JAHRE))

    def loeschfrist(self, datum: datetime.date) -> datetime.date:
        """Bis wann ein Dokument vom ``datum`` aufzubewahren ist (31.12. des letzten Jahres)."""
        return datetime.date(datum.year + self.loeschfrist_jahre, 12, 31)


BELEG = "beleg"
MAHNUNG_LIEFERANT = "mahnung_lieferant"
RUECKLASTSCHRIFT = "ruecklastschrift"
VOLLSTRECKUNG = "vollstreckung"
SONSTIGES = "sonstiges"

DOKUMENTARTEN: dict[str, Dokumentart] = {
    art.schluessel: art
    for art in (
        Dokumentart(BELEG, "Beleg (Rechnung, Quittung)", beleg=True),
        Dokumentart(MAHNUNG_LIEFERANT, "Mahnung eines Lieferanten"),
        Dokumentart(RUECKLASTSCHRIFT, "Rücklastschrift"),
        Dokumentart(VOLLSTRECKUNG, "Vollstreckung"),
        Dokumentart(SONSTIGES, "Sonstiges"),
    )
}

#: Arten ohne Buchung, die am Lieferanten liegen (H6.8).
AM_LIEFERANTEN = (MAHNUNG_LIEFERANT, RUECKLASTSCHRIFT, VOLLSTRECKUNG)


def holen(schluessel: str) -> Dokumentart:
    try:
        return DOKUMENTARTEN[schluessel]
    except KeyError as fehler:
        raise UnbekannteDokumentart(schluessel) from fehler


def auswahl() -> list[tuple[str, str]]:
    return [(a.schluessel, a.titel) for a in DOKUMENTARTEN.values()]
