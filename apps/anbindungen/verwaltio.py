"""Client für Verwaltio (V12): Kennzahlen je Wohneinheit und die Liste der NW-IDs.

Die API-Dokumentation von Verwaltio liegt noch nicht vor. Deshalb gibt es hier
- das Protokoll ``VerwaltioClient``, gegen das Lauf, Speichern und Anzeige gebaut sind,
- ``FakeVerwaltio`` für Entwicklung und Tests (kein Netz),
- ``VerwaltioApiClient`` als Platzhalter: **offen.** Er prüft nur, ob Adresse und Schlüssel
  eingerichtet sind, und wirft sonst ``NotImplementedError``. Die echten Aufrufe kommen,
  sobald Max die Dokumentation hat.

Austauschbar über ``HOF_VERWALTIO_CLIENT``. Der Schlüssel liegt nur auf dem Server
(``VERWALTIO_TOKEN``). Verwaltio liefert nur Zahlen und NW-IDs ins Hof-System, keine Namen.
"""

from __future__ import annotations

import datetime
import hashlib
import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from django.conf import settings
from django.utils.module_loading import import_string


class VerwaltioFehler(Exception):
    """Verwaltio antwortet nicht wie erwartet. Die Meldung nennt nur IDs."""


class VerwaltioNichtEingerichtet(VerwaltioFehler):
    """Adresse oder Schlüssel für Verwaltio fehlen in der Umgebung."""


#: Stand der Nebenkostenabrechnung, wie ``Kennzahl.NkaStand``.
NKA_STAENDE = ("unbekannt", "offen", "in_arbeit", "versendet", "abgeschlossen")


@dataclass(frozen=True)
class VerwaltioKennzahlen:
    """Was Verwaltio je Wohneinheit liefert: nur Zähler, Summen und Daten."""

    nw_id: str
    offene_posten: Decimal
    mahnfaelle: int
    nka_jahr: int | None
    nka_stand: str
    fristen_offen: int
    naechste_frist: datetime.date | None


class VerwaltioClient(Protocol):
    def wohneinheiten(self) -> list[str]:
        """Die NW-IDs aller Wohneinheiten, die Verwaltio führt (für den ID-Abgleich)."""
        ...

    def kennzahlen(self, nw_id: str, stichtag: datetime.date) -> VerwaltioKennzahlen:
        """Kennzahlen einer Wohneinheit zum Stichtag."""
        ...


class VerwaltioApiClient:
    """Echter Client. **Offen**, bis die API-Dokumentation von Verwaltio vorliegt."""

    OFFEN = (
        "Der Verwaltio-Client ist noch nicht gebaut: Die API-Dokumentation fehlt. "
        "Bis dahin liefert der Lauf keine Kennzahlen (Werte in Verwaltio nachsehen)."
    )

    def __init__(self) -> None:
        self.url = settings.VERWALTIO_URL
        self.token = settings.VERWALTIO_TOKEN
        if not self.url or not self.token:
            raise VerwaltioNichtEingerichtet(
                "Verwaltio ist nicht eingerichtet (VERWALTIO_URL und VERWALTIO_TOKEN fehlen)."
            )

    def wohneinheiten(self) -> list[str]:
        raise NotImplementedError(self.OFFEN)

    def kennzahlen(self, nw_id: str, stichtag: datetime.date) -> VerwaltioKennzahlen:
        raise NotImplementedError(self.OFFEN)


def _fake_erlaubt(name: str) -> None:
    if os.environ.get("DJANGO_ENV", "").strip().lower() == "prod":
        raise VerwaltioNichtEingerichtet(
            f"{name} ist ein Fake für Entwicklung und Tests und in der Produktion gesperrt."
        )


def _zahl(nw_id: str, salz: str, bis: int) -> int:
    """Feste Pseudozahl aus NW-ID und Salz (gleiche ID, gleiche Zahl)."""
    digest = hashlib.sha256(f"verwaltio-fake:{salz}:{nw_id}".encode()).digest()
    return int.from_bytes(digest[:4], "big") % bis


class FakeVerwaltio:
    """Verwaltio ohne Netz, für Entwicklung und Tests.

    Ohne Angaben führt der Fake genau die Wohnungen des Hof-Systems (nicht aufgelöst) und
    rechnet ihre Kennzahlen fest aus der NW-ID aus: gleiche ID, gleiche Zahlen. Tests geben
    die Liste der IDs (``ids``) und einzelne Kennzahlen (``daten``) selbst vor; ``fehler``
    nennt IDs, deren Abruf scheitert.
    """

    def __init__(
        self,
        ids: list[str] | None = None,
        daten: dict[str, VerwaltioKennzahlen] | None = None,
        fehler: set[str] | None = None,
    ) -> None:
        _fake_erlaubt("FakeVerwaltio")
        self._ids = ids
        self._daten = daten or {}
        self._fehler = fehler or set()

    def wohneinheiten(self) -> list[str]:
        if self._ids is not None:
            return list(self._ids)
        from apps.stammdaten.models import Einheit

        return list(
            Einheit.objects.filter(typ=Einheit.Typ.WOHNUNG, aufgeloest_am__isnull=True)
            .order_by("nw_id")
            .values_list("nw_id", flat=True)
        )

    def kennzahlen(self, nw_id: str, stichtag: datetime.date) -> VerwaltioKennzahlen:
        if nw_id in self._fehler:
            raise VerwaltioFehler(f"Verwaltio (Fake): Abruf für {nw_id} gescheitert.")
        if nw_id in self._daten:
            return self._daten[nw_id]
        mahnfaelle = 1 if _zahl(nw_id, "mahnung", 3) == 0 else 0
        offen = Decimal(_zahl(nw_id, "posten", 4) * 125 + mahnfaelle * 310) / 2
        fristen = _zahl(nw_id, "fristen", 3)
        jahr = stichtag.year - 1
        return VerwaltioKennzahlen(
            nw_id=nw_id,
            offene_posten=offen.quantize(Decimal("0.01")),
            mahnfaelle=mahnfaelle,
            nka_jahr=jahr,
            nka_stand=NKA_STAENDE[1 + _zahl(nw_id, "nka", 4)],
            fristen_offen=fristen,
            naechste_frist=(
                stichtag + datetime.timedelta(days=7 + _zahl(nw_id, "frist", 60))
                if fristen
                else None
            ),
        )


def verwaltio_client() -> VerwaltioClient:
    klasse = import_string(settings.HOF_VERWALTIO_CLIENT)
    client: VerwaltioClient = klasse()
    return client
