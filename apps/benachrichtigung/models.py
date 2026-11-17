"""Gesendete Benachrichtigungen (H11.1).

Ein Eintrag je Meldung. Er dient als Gedächtnis gegen Wiederholungen: Dieselbe Lage
wird an einem Tag nur einmal gemeldet. Geschrieben wird nur über
``apps.benachrichtigung.services``.
"""

from __future__ import annotations

from django.contrib.postgres.fields import ArrayField
from django.db import models

from apps.kern import personenfelder
from apps.kern.models import Lauf


class Benachrichtigung(models.Model):
    """Eine gesendete Meldung mit der Lage, die sie gemeldet hat.

    Nicht im Protokoll: Der Eintrag ist selbst ein Protokoll, wie ``kern.Lauf``.
    ``lage`` hat die Form ``{"gescheitert": {lauf_name: anzahl}, "freigaben": {art: anzahl}}``.
    """

    gesendet_am = models.DateTimeField("gesendet am", db_index=True)
    schluessel = models.CharField("Schlüssel der Lage", max_length=64)
    lage = models.JSONField("Lage", default=dict)
    titel = models.CharField("Titel", max_length=200)
    text = models.TextField("Text", help_text="Nur Zähler, Lauf-Namen, Arten und Links.")
    kanaele = ArrayField(
        models.CharField(max_length=20), verbose_name="Kanäle", default=list, blank=True
    )
    lauf = models.ForeignKey(
        Lauf,
        verbose_name="Lauf",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="benachrichtigungen",
    )

    class Meta:
        verbose_name = "Benachrichtigung"
        verbose_name_plural = "Benachrichtigungen"
        ordering = ("-gesendet_am", "-pk")

    def __str__(self) -> str:
        return f"Benachrichtigung #{self.pk}"


# Titel und Text entstehen nur aus Zählern, Lauf-Namen und Arten (keine Personendaten).
personenfelder.unbedenklich(Benachrichtigung, ["schluessel", "titel", "text"])
