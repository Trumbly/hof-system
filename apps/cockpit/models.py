"""Modelle des Cockpits: Aufgaben im Hof-Backlog (Notion).

Geschrieben wird nur über ``apps.cockpit.services`` (CLAUDE.md, Regel 4.1). Das Cockpit
selbst hat keine Tabellen mit Fachdaten: Die Startseite liest direkt aus den Tabellen der
anderen Apps.
"""

from __future__ import annotations

from django.db import models
from django.db.models import Q

from apps.kern import personenfelder
from apps.kern.protokoll import ProtokolliertesModell


class NotionAufgabe(ProtokolliertesModell):
    """Eine Aufgabe im Hof-Backlog für alle offenen Arbeitsposten einer Art (und eines
    Bündels; heute ist das Bündel immer leer, also eine Aufgabe je Art).

    Titel und Text entstehen nur aus Art, Anzahl, NW-IDs bzw. Objekt-Kürzeln, Fälligkeit
    und Link, nie aus Namen oder Beträgen. ``inhalt_schluessel`` ist der Fingerabdruck des
    Inhalts, der zuletzt in Notion angekommen ist: Nur wenn er sich ändert, ruft der Lauf
    ``aufgaben_abgleich`` Notion auf. Sind alle Posten erledigt, wird die Aufgabe in
    Notion auf Erledigt gesetzt und hier ``erledigt``; kommen später neue Posten der Art,
    entsteht eine neue Aufgabe.
    """

    class Status(models.TextChoices):
        OFFEN = "offen", "offen"
        ERLEDIGT = "erledigt", "erledigt"

    art = models.CharField("Art der Arbeitsposten", max_length=100, db_index=True)
    buendel = models.CharField("Bündel", max_length=100, blank=True, default="")
    status = models.CharField(
        "Status", max_length=20, choices=Status.choices, default=Status.OFFEN, db_index=True
    )
    page_id = models.CharField(
        "Notion-Page-ID", max_length=64, blank=True, help_text="Leer: noch nicht in Notion."
    )
    anzahl = models.PositiveIntegerField("offene Posten", default=0)
    titel = models.CharField("Titel", max_length=200, blank=True)
    text = models.TextField("Text", blank=True)
    faellig_am = models.DateField("fällig am", null=True, blank=True)
    inhalt_schluessel = models.CharField("Fingerabdruck in Notion", max_length=64, blank=True)
    vorgemerkt_am = models.DateTimeField("vorgemerkt am", null=True, blank=True)
    abgeglichen_am = models.DateTimeField("abgeglichen am", null=True, blank=True)
    fehler = models.CharField(
        "letzter Fehler", max_length=200, blank=True, help_text="Nur Fehlerart, keine Daten."
    )
    angelegt_am = models.DateTimeField("angelegt am", auto_now_add=True)
    erledigt_am = models.DateTimeField("erledigt am", null=True, blank=True)

    class Meta:
        verbose_name = "Aufgabe im Hof-Backlog"
        verbose_name_plural = "Aufgaben im Hof-Backlog"
        ordering = ("art", "buendel", "-angelegt_am", "-pk")
        constraints = (
            models.UniqueConstraint(
                fields=["art", "buendel"],
                condition=Q(status="offen"),
                name="notionaufgabe_offen_einmal_je_art_buendel",
            ),
        )

    def __str__(self) -> str:
        return f"Aufgabe im Hof-Backlog #{self.pk} ({self.art})"


# Titel und Text entstehen nur aus Art, Anzahl, NW-IDs, Kürzeln, Datum und Link.
personenfelder.unbedenklich(
    NotionAufgabe,
    ["art", "buendel", "status", "page_id", "titel", "text", "inhalt_schluessel", "fehler"],
)
