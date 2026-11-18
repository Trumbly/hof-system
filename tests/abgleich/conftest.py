"""Fixtures des Abgleichs: Beginn wie im Testhof, fester Stichtag, Läufe als Befehl."""

from __future__ import annotations

import datetime
import io
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pytest
from django.core.management import call_command

from apps.abgleich import services
from apps.abgleich.models import Klaerfall
from apps.kassenbuch import services as kassenbuch
from apps.kassenbuch.models import Buchung, Kategorie, Zuordnungsregel
from apps.kern.models import Lauf
from apps.soll import services as soll
from apps.soll.models import Sollposten
from apps.stammdaten import services as stammdaten
from apps.stammdaten.models import Partei, Vertrag
from testhof import abgleich as testhof_abgleich
from testhof.aufbau import STICHTAG
from tests.kassenbuch.conftest import KleinerHof, klein, neue_iban, umsatz
from tests.stammdaten.conftest import garage_anlegen, mieter_anlegen

__all__ = ["klein", "neue_iban"]


@pytest.fixture(autouse=True)
def abgleich_beginn(settings: Any) -> datetime.date:
    """Der Abgleich beginnt wie im Testhof am 01.07.2026 (HOF_ABGLEICH_BEGINN)."""
    settings.ABGLEICH_BEGINN = testhof_abgleich.BEGINN
    return testhof_abgleich.BEGINN


@pytest.fixture
def stichtag(monkeypatch: pytest.MonkeyPatch) -> datetime.date:
    """„Heute“ ist der Stichtag des Testhofs (01.11.2026)."""
    monkeypatch.setattr(services, "heute", lambda: STICHTAG)
    return STICHTAG


def laeufe() -> tuple[Lauf, Lauf]:
    """Einordnen im Kassenbuch, dann Abgleich (wie die Timer um 6:30 und 6:45)."""
    call_command("kassenbuch_einordnen", stdout=io.StringIO())
    call_command("abgleich", stdout=io.StringIO())
    einordnen = Lauf.objects.filter(name="kassenbuch_einordnen").first()
    abgleich = Lauf.objects.filter(name="abgleich").first()
    assert einordnen is not None and abgleich is not None
    return einordnen, abgleich


# --- Kleiner Hof für Einzeltests -----------------------------------------------------

D = datetime.date
#: „Heute“ in den Einzeltests: Posten Juli bis November 2026 stehen.
HEUTE = D(2026, 11, 20)
JULI = D(2026, 7, 1)


@dataclass
class Abgleichhof:
    """Ein Konto, Kategorien und Verträge über die Services; Zahlungen über die Bank."""

    klein: KleinerHof
    mieter: Partei

    def vertrag(self, miete: str = "85.00", beginn: datetime.date = JULI, **kw: Any) -> Vertrag:
        einheit = garage_anlegen(self.klein.hof, frei_ab=D(2019, 1, 1))
        kw.setdefault("kaution", Decimal("0.00"))
        return stammdaten.vertrag_anlegen(
            einheiten=[einheit],
            mieter=[self.mieter],
            beginn=beginn,
            miete=Decimal(miete),
            status=kw.pop("status", Vertrag.Status.AKTIV),
            **kw,
        )

    def regel(self, iban: str, **zahlerregel: Any) -> Zuordnungsregel:
        """Eine Regel im Kassenbuch nach IBAN und, wenn angegeben, ihre Zahlerregel."""
        regel = kassenbuch.regel_anlegen(
            f"Zahler {iban[-4:]}",
            gegen_iban=iban,
            richtung="eingang",
            kategorie=self.klein.miete,
            objekt=self.klein.hof,
        )
        if zahlerregel:
            services.zahlerregel_setzen(regel, **zahlerregel)
        return regel

    def zahlung(
        self,
        datum: datetime.date,
        betrag: str,
        *,
        iban: str,
        zweck: str = "Miete",
        kategorie: Kategorie | None = None,
    ) -> Buchung:
        """Ein Eingang, im Kassenbuch nach Regel gebucht (sonst von Hand auf Miete)."""
        eintrag = umsatz(self.klein.konto, datum, betrag, iban=iban, zweck=zweck)
        treffer = kassenbuch.einordnen(eintrag, kassenbuch.aktive_regeln())
        if treffer.regel is not None and kategorie is None:
            kassenbuch.nach_regel_buchen(eintrag, treffer.regel)
        else:
            kassenbuch.umsatz_zuordnen(
                eintrag, kategorie=kategorie or self.klein.miete, objekt=self.klein.hof
            )
        return Buchung.objects.get(umsatz=eintrag)


@pytest.fixture
def hof(klein: KleinerHof, monkeypatch: pytest.MonkeyPatch) -> Abgleichhof:
    monkeypatch.setattr(soll, "heute", lambda: HEUTE)
    monkeypatch.setattr(services, "heute", lambda: HEUTE)
    return Abgleichhof(klein=klein, mieter=mieter_anlegen())


def posten(vertrag: Vertrag) -> list[Sollposten]:
    return list(
        Sollposten.objects.filter(vertrag=vertrag)
        .exclude(status=Sollposten.Status.STORNIERT)
        .order_by("faellig_am", "von", "art")
    )


def klaerfaelle(buchung: Buchung | None = None) -> list[Klaerfall]:
    qs = Klaerfall.objects.order_by("pk")
    if buchung is not None:
        qs = qs.filter(buchung=buchung)
    return list(qs)
