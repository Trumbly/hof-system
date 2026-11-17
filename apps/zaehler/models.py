"""Modelle der Zähler (Schritt 8, H7.1, H7.2, O2): Zähler und Zählerstände.

Geschrieben wird nur über ``apps.zaehler.services`` (CLAUDE.md, Regel 4.1).

Ein Zähler hängt an genau einer Einheit oder, als Hauptzähler, an einem Objekt. Ein
Zählerwechsel baut den alten Zähler aus (``ausgebaut_am``, Stand der Art „Ausbau“) und legt
den neuen als ``nachfolger`` an (Stand der Art „Einbau“). Die Stände eines Zählers steigen
nie; ein kleinerer Stand gehört an den Nachfolger.

Geführt werden Zähler für alles außerhalb von Wohnraum; abgerechnet wird ab v2 (E2).
"""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal
from pathlib import PurePath
from typing import Any

from django.db import models
from django.db.models import Q

from apps.kern import personenfelder
from apps.kern.models import Objekt
from apps.kern.protokoll import ProtokolliertesModell
from apps.stammdaten.models import Einheit, Uebergabe


def _foto_pfad(instanz: Any, dateiname: str) -> str:
    """Neutraler Dateiname ohne Teile des Originalnamens (der kann Namen enthalten)."""
    endung = PurePath(dateiname).suffix.lower()[:10]
    heute = datetime.date.today()
    return f"zaehler/fotos/{heute:%Y/%m}/{uuid.uuid4().hex}{endung}"


class Zaehler(ProtokolliertesModell):
    """Ein Zähler an einer Einheit oder einem Objekt (H7.1)."""

    class Art(models.TextChoices):
        STROM = "strom", "Strom"
        WASSER = "wasser", "Wasser"
        GAS = "gas", "Gas"
        WAERME = "waerme", "Wärme"

    nummer = models.CharField(
        "Zählernummer",
        max_length=50,
        unique=True,
        help_text="Nummer auf dem Zähler (Herstellernummer). Keine Personendaten.",
    )
    art = models.CharField("Art", max_length=10, choices=Art.choices)
    einheit = models.ForeignKey(
        Einheit,
        verbose_name="Einheit",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="zaehler",
    )
    objekt = models.ForeignKey(
        Objekt,
        verbose_name="Objekt (Hauptzähler)",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="zaehler",
        help_text="Nur für Zähler, die keiner Einheit gehören (z. B. Hauptzähler).",
    )
    bezeichnung = models.CharField(
        "Lage", max_length=200, blank=True, help_text="z. B. „Hauptzähler Keller“. Keine Namen."
    )
    einbaudatum = models.DateField("Einbaudatum", null=True, blank=True)
    eichfrist_bis = models.DateField("Eichfrist bis", null=True, blank=True)
    ausgebaut_am = models.DateField("ausgebaut am", null=True, blank=True, db_index=True)
    nachfolger = models.OneToOneField(
        "self",
        verbose_name="Nachfolger",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="vorgaenger",
    )
    angelegt_am = models.DateTimeField("angelegt am", auto_now_add=True)

    class Meta:
        verbose_name = "Zähler"
        verbose_name_plural = "Zähler"
        ordering = ("nummer",)
        constraints = (
            # Genau eins von Einheit und Objekt.
            models.CheckConstraint(
                condition=(Q(einheit__isnull=False) & Q(objekt__isnull=True))
                | (Q(einheit__isnull=True) & Q(objekt__isnull=False)),
                name="zaehler_einheit_oder_objekt",
            ),
            models.CheckConstraint(
                condition=Q(ausgebaut_am__isnull=True)
                | Q(einbaudatum__isnull=True)
                | Q(ausgebaut_am__gte=models.F("einbaudatum")),
                name="zaehler_ausbau_nach_einbau",
            ),
            models.CheckConstraint(
                condition=Q(nachfolger__isnull=True) | Q(ausgebaut_am__isnull=False),
                name="zaehler_nachfolger_nur_nach_ausbau",
            ),
        )

    def __str__(self) -> str:
        return f"Zähler {self.nummer}"

    @property
    def standort_objekt(self) -> Objekt:
        """Das Objekt, in dem der Zähler hängt (über die Einheit oder direkt)."""
        if self.einheit_id is not None:
            return self.einheit.objekt  # type: ignore[union-attr]
        return self.objekt  # type: ignore[return-value]

    @property
    def ort(self) -> str:
        """NW-ID der Einheit oder „Objekt HOF“: wo der Zähler hängt, ohne Namen."""
        if self.einheit_id is not None:
            return self.einheit.nw_id  # type: ignore[union-attr]
        return f"Objekt {self.objekt.kuerzel}"  # type: ignore[union-attr]

    @property
    def ist_eingebaut(self) -> bool:
        return self.ausgebaut_am is None

    def eingebaut_am(self, datum: datetime.date) -> bool:
        if self.einbaudatum is not None and datum < self.einbaudatum:
            return False
        return self.ausgebaut_am is None or datum <= self.ausgebaut_am

    def letzter_stand(self, bis: datetime.date | None = None) -> Zaehlerstand | None:
        staende = self.staende.all()
        if bis is not None:
            staende = staende.filter(datum__lte=bis)
        return staende.order_by("-datum", "-pk").first()


class Zaehlerstand(ProtokolliertesModell):
    """Ein Stand eines Zählers an einem Tag (H7.2), höchstens einer je Zähler und Tag.

    Ein geschätzter Stand trägt die Art „geschätzt“ als Kennzeichen. Fotos liefert nur
    die Seite ``zaehler:foto`` mit Anmeldung aus.
    """

    class Art(models.TextChoices):
        ABGELESEN = "abgelesen", "abgelesen"
        GESCHAETZT = "geschaetzt", "geschätzt"
        EINBAU = "einbau", "Einbau"
        AUSBAU = "ausbau", "Ausbau"

    zaehler = models.ForeignKey(
        Zaehler, verbose_name="Zähler", on_delete=models.PROTECT, related_name="staende"
    )
    datum = models.DateField("Datum")
    stand = models.DecimalField("Stand", max_digits=12, decimal_places=3)
    art = models.CharField("Art", max_length=12, choices=Art.choices, default=Art.ABGELESEN)
    foto = models.FileField("Foto", upload_to=_foto_pfad, blank=True)
    vermerk = models.TextField(
        "Vermerk", blank=True, help_text="z. B. wie geschätzt wurde. Keine Namen."
    )
    bei_uebergabe = models.BooleanField("erfasst bei Übergabe oder Rückgabe", default=False)
    uebergabe = models.ForeignKey(
        Uebergabe,
        verbose_name="Übergabe oder Rückgabe",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="zaehlerstaende",
    )
    angelegt_am = models.DateTimeField("angelegt am", auto_now_add=True)

    class Meta:
        verbose_name = "Zählerstand"
        verbose_name_plural = "Zählerstände"
        ordering = ("zaehler", "datum", "pk")
        constraints = (
            models.UniqueConstraint(fields=("zaehler", "datum"), name="zaehlerstand_einmal_je_tag"),
            models.CheckConstraint(condition=Q(stand__gte=0), name="zaehlerstand_nicht_negativ"),
            models.CheckConstraint(
                condition=Q(uebergabe__isnull=True) | Q(bei_uebergabe=True),
                name="zaehlerstand_uebergabe_nur_bei_uebergabe",
            ),
        )

    def __str__(self) -> str:
        return f"Zählerstand #{self.pk}"

    @property
    def ist_geschaetzt(self) -> bool:
        return self.art == self.Art.GESCHAETZT

    @property
    def stand_text(self) -> str:
        return stand_text(self.stand)


def stand_text(stand: Decimal) -> str:
    """Stand ohne überflüssige Nullen, z. B. ``1520.4`` statt ``1520.400``."""
    text = f"{stand.normalize():f}"
    return text


# --- Personenfelder (CLAUDE.md, Regel 4.6) ------------------------------------------

# Die Zählernummer steht auf dem Gerät, nicht bei einer Person: unbedenklich.
personenfelder.unbedenklich(Zaehler, ["nummer", "art", "bezeichnung"])
personenfelder.unbedenklich(Zaehlerstand, ["art"])
personenfelder.register(Zaehlerstand, ["vermerk"])
