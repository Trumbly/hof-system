"""Gerüst für Läufe (CLAUDE.md, Regel 4.7).

Ein Lauf ist ein Management-Befehl, der von ``LaufBefehl`` erbt und ``ausfuehren()``
schreibt. Die Basisklasse
- öffnet einen ``kern.Lauf`` und schließt ihn mit Ergebnis und Zählern,
- führt die Arbeit in einer Transaktion aus, mit dem Lauf als Kontext im Protokoll,
- hält eine Ausnahme als Fehler fest (der Lauf gilt dann als abgebrochen, seine
  Änderungen werden zurückgerollt) und meldet sie mit Exit-Code ungleich 0,
- zählt die drei Körbe: gebucht, Arbeitsposten, Fehler.

Beispiel::

    class Command(LaufBefehl):
        help = "…"

        def ausfuehren(self, **optionen):
            for umsatz in offene_umsaetze():
                if eindeutig(umsatz):
                    buchen(umsatz)
                    self.gebucht()
                else:
                    self.arbeitsposten_anlegen("Umsätze einordnen", bezug=umsatz)
"""

from __future__ import annotations

import contextlib
import datetime
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, ClassVar

import pghistory
from django.core.management.base import BaseCommand, CommandError
from django.db import models, transaction

from apps.kern import services
from apps.kern.models import Arbeitsposten, Lauf

logger = logging.getLogger(__name__)


@dataclass
class Koerbe:
    gebucht: int = 0
    arbeitsposten: int = 0
    fehler: int = 0


class LaufBefehl(BaseCommand):
    """Basisklasse für alle Läufe."""

    #: Name des Laufs im Laufprotokoll. Standard: Name des Befehls (Dateiname).
    lauf_name: ClassVar[str] = ""
    #: Die Arbeit läuft in einer Transaktion (Abbruch rollt alles zurück). Ein Lauf, der
    #: andere Läufe aufruft (``tageslauf``), schaltet das ab: Jeder Teil schreibt dann
    #: selbst fest, und ein Abbruch am Ende rollt die fertigen Teile nicht zurück.
    in_transaktion: ClassVar[bool] = True

    lauf: Lauf
    koerbe: Koerbe

    def ausfuehren(self, **optionen: Any) -> str | None:
        """Die eigentliche Arbeit. Rückgabe: optionale Meldung (nur IDs und Zähler)."""
        raise NotImplementedError

    # --- Körbe --------------------------------------------------------------------

    def gebucht(self, anzahl: int = 1) -> None:
        self.koerbe.gebucht += anzahl

    def fehler(self, anzahl: int = 1) -> None:
        """Zählt einen Fehler, ohne den Lauf abzubrechen (z. B. ein einzelner Posten)."""
        self.koerbe.fehler += anzahl

    def arbeitsposten_anlegen(
        self,
        art: str,
        *,
        bezug: models.Model | None = None,
        aktionen: Iterable[str] = services.STANDARD_AKTIONEN,
        faellig_am: datetime.date | None = None,
        hinweis: str = "",
    ) -> Arbeitsposten:
        posten = services.arbeitsposten_anlegen(
            art,
            bezug=bezug,
            aktionen=aktionen,
            faellig_am=faellig_am,
            hinweis=hinweis,
            lauf=self.lauf,
        )
        self.koerbe.arbeitsposten += 1
        return posten

    # --- Ablauf -------------------------------------------------------------------

    def get_lauf_name(self) -> str:
        return self.lauf_name or type(self).__module__.rsplit(".", 1)[-1]

    def handle(self, *args: Any, **optionen: Any) -> None:
        name = self.get_lauf_name()
        self.koerbe = Koerbe()
        self.lauf = services.lauf_starten(name)
        try:
            klammer = transaction.atomic() if self.in_transaktion else contextlib.nullcontext()
            with klammer, pghistory.context(lauf=self.lauf.pk, lauf_name=name):
                meldung = self.ausfuehren(**optionen) or ""
        except Exception as fehler:
            logger.exception("Lauf #%s (%s) abgebrochen", self.lauf.pk, name)
            # Die Arbeit ist zurückgerollt: Gebucht und angelegt ist nichts.
            services.lauf_beenden(
                self.lauf,
                gebucht=0,
                arbeitsposten=0,
                fehler=self.koerbe.fehler + 1,
                meldung=(
                    f"Abgebrochen mit {type(fehler).__name__}. "
                    + (
                        "Alle Änderungen dieses Laufs sind zurückgerollt."
                        if self.in_transaktion
                        else "Fertige Teile bleiben bestehen."
                    )
                ),
                abgebrochen=True,
            )
            raise CommandError(
                f"Lauf #{self.lauf.pk} ({name}) abgebrochen: {type(fehler).__name__}"
            ) from fehler

        services.lauf_beenden(
            self.lauf,
            gebucht=self.koerbe.gebucht,
            arbeitsposten=self.koerbe.arbeitsposten,
            fehler=self.koerbe.fehler,
            meldung=meldung,
        )
        self.stdout.write(
            f"Lauf #{self.lauf.pk} ({name}): {self.lauf.get_status_display()}, "
            f"gebucht {self.koerbe.gebucht}, Arbeitsposten {self.koerbe.arbeitsposten}, "
            f"Fehler {self.koerbe.fehler}"
        )
