"""Modelle der Fristen (H9.1): Frist und ihre Verschiebungen.

Geschrieben wird nur über ``apps.fristen.services`` (CLAUDE.md, Regel 4.1). Eine Frist
wird nie gelöscht: Sie verschwindet aus den Listen erst mit einem Erledigt-Datum.
"""

from __future__ import annotations

import datetime

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import Q

from apps.kern import personenfelder
from apps.kern.protokoll import ProtokolliertesModell


class FristArt(models.TextChoices):
    """Arten von Fristen (H9.1)."""

    KUENDIGUNG = "kuendigung", "Kündigungsfrist"
    VERTRAGSENDE = "vertragsende", "Vertragsende"
    SPERRFRIST = "sperrfrist", "Sperrfrist"
    KAUTIONSABRECHNUNG = "kautionsabrechnung", "Kautionsabrechnung"
    ABRECHNUNGSFRIST = "abrechnungsfrist", "Abrechnungsfrist"
    BANKFREIGABE = "bankfreigabe", "Bankfreigabe erneuern"
    ZAHLUNGSZIEL = "zahlungsziel", "Zahlungsziel"
    EICHFRIST = "eichfrist", "Eichfrist"
    ABLESUNG = "ablesung", "Ablesung"
    SONSTIGE = "sonstige", "Sonstige"


class FristQuelle(models.TextChoices):
    """Wer eine Frist angelegt hat."""

    VERTRAG = "vertrag", "Vertrag"
    KAUTION = "kaution", "Kaution"
    BANKFREIGABE = "bankfreigabe", "Bankfreigabe"
    EINGANGSRECHNUNG = "eingangsrechnung", "Eingangsrechnung"
    ZAEHLER = "zaehler", "Zähler"
    HAND = "hand", "von Hand"


class Frist(ProtokolliertesModell):
    """Ein Datum, bis zu dem etwas zu tun ist, mit Quelle und Bezug.

    ``bezug`` zeigt generisch auf das Objekt, um das es geht (Vertrag, Kaution,
    Bankzugang, Eingangsrechnung, Zähler oder im Kern ein Objekt). ``quelle`` sagt, wer
    die Frist angelegt hat. Ab ``faellig_ab`` (Datum minus Vorlauf) wird aus der Frist
    ein Arbeitsposten „Frist fällig“ (Lauf ``fristen``).
    """

    Art = FristArt
    Quelle = FristQuelle

    datum = models.DateField("Datum", db_index=True)
    art = models.CharField("Art", max_length=30, choices=Art.choices, db_index=True)
    quelle = models.CharField("Quelle", max_length=30, choices=Quelle.choices, db_index=True)
    bezug_typ = models.ForeignKey(ContentType, verbose_name="Bezug (Typ)", on_delete=models.PROTECT)
    bezug_id = models.PositiveBigIntegerField("Bezug (ID)")
    bezug = GenericForeignKey("bezug_typ", "bezug_id")
    vorlauf_tage = models.PositiveSmallIntegerField(
        "Vorlauf (Tage)",
        default=0,
        help_text="So viele Tage vor dem Datum wird die Frist zum Arbeitsposten.",
    )
    erledigt_am = models.DateField("erledigt am", null=True, blank=True, db_index=True)
    hinweis = models.TextField("Hinweis", blank=True)
    angelegt_am = models.DateTimeField("angelegt am", auto_now_add=True)

    class Meta:
        verbose_name = "Frist"
        verbose_name_plural = "Fristen"
        ordering = ("datum", "pk")
        indexes = (models.Index(fields=["bezug_typ", "bezug_id"]),)
        constraints = (
            # Je Quelle, Art und Bezug höchstens eine offene Frist (frist_anlegen ist
            # idempotent). Erledigte bleiben stehen; danach darf eine neue entstehen,
            # z. B. die nächste Bankfreigabe.
            models.UniqueConstraint(
                fields=["quelle", "art", "bezug_typ", "bezug_id"],
                condition=Q(erledigt_am__isnull=True),
                name="frist_offen_einmal_je_quelle_art_bezug",
            ),
            # Jede Frist nennt eine gültige Quelle und Art, auch an den Services vorbei.
            models.CheckConstraint(
                condition=Q(quelle__in=FristQuelle.values), name="frist_quelle_gueltig"
            ),
            models.CheckConstraint(condition=Q(art__in=FristArt.values), name="frist_art_gueltig"),
        )

    def __str__(self) -> str:
        return f"Frist #{self.pk} {self.get_art_display()}"

    @property
    def faellig_ab(self) -> datetime.date:
        """Ab diesem Tag gilt die Frist als fällig (Datum minus Vorlauf)."""
        return self.datum - datetime.timedelta(days=self.vorlauf_tage)

    @property
    def ist_erledigt(self) -> bool:
        return self.erledigt_am is not None

    def ist_ueberfaellig(self, heute: datetime.date) -> bool:
        return not self.ist_erledigt and self.datum < heute


class Fristverschiebung(ProtokolliertesModell):
    """Eine Verschiebung einer Frist: von welchem auf welches Datum und warum."""

    frist = models.ForeignKey(
        Frist, verbose_name="Frist", on_delete=models.PROTECT, related_name="verschiebungen"
    )
    von_datum = models.DateField("von")
    nach_datum = models.DateField("nach")
    grund = models.TextField("Grund", blank=True)
    angelegt_am = models.DateTimeField("angelegt am", auto_now_add=True)

    class Meta:
        verbose_name = "Fristverschiebung"
        verbose_name_plural = "Fristverschiebungen"
        ordering = ("angelegt_am", "pk")

    def __str__(self) -> str:
        return f"Fristverschiebung #{self.pk} (Frist #{self.frist_id})"


# --- Personenfelder (CLAUDE.md, Regel 4.6) ------------------------------------------

personenfelder.register(Frist, ["hinweis"])
personenfelder.unbedenklich(Frist, ["art", "quelle"])
personenfelder.register(Fristverschiebung, ["grund"])
