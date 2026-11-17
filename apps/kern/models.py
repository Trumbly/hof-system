"""Modelle des Kerns: Benutzer, Mandant, Objekt, Lauf, Arbeitsposten.

Geschrieben wird nur über ``apps.kern.services`` (CLAUDE.md, Regel 4.1).
"""

from __future__ import annotations

from django.contrib.auth.models import AbstractUser
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres.fields import ArrayField
from django.db import models

from apps.kern import personenfelder
from apps.kern.protokoll import ProtokolliertesModell


class Benutzer(AbstractUser):
    """Der Benutzer des Hof-Systems. Eine Rolle; Anmeldung mit E-Mail und TOTP.

    Nicht im Protokoll: Passwort-Hash und letzte Anmeldung gehören nicht in die Historie.
    """

    email = models.EmailField("E-Mail", unique=True)

    class Meta:
        verbose_name = "Benutzer"
        verbose_name_plural = "Benutzer"

    def __str__(self) -> str:
        return f"Benutzer #{self.pk}"


class Mandant(ProtokolliertesModell):
    """Wem eine Buchung, ein Objekt oder ein Vertrag gehört (H6.5)."""

    class Art(models.TextChoices):
        GBR = "gbr", "GbR"
        NACHLASS = "nachlass", "Nachlass"
        KG_LINIE = "kg_linie", "KG-Linie"
        PRIVAT = "privat", "privat"

    art = models.CharField("Art", max_length=20, choices=Art.choices)
    kuerzel = models.CharField(
        "Kürzel", max_length=20, unique=True, help_text="Kurz und ohne Namen, z. B. „GBR“."
    )
    bezeichnung = models.CharField("Bezeichnung", max_length=200)
    aktiv = models.BooleanField("aktiv", default=True)

    class Meta:
        verbose_name = "Mandant"
        verbose_name_plural = "Mandanten"
        ordering = ("kuerzel",)

    def __str__(self) -> str:
        return self.kuerzel


class Objekt(ProtokolliertesModell):
    """Ein Grundstück oder Gebäude mit Einheiten, immer genau einem Mandanten zugeordnet."""

    mandant = models.ForeignKey(
        Mandant, verbose_name="Mandant", on_delete=models.PROTECT, related_name="objekte"
    )
    kuerzel = models.CharField(
        "Kürzel", max_length=20, unique=True, help_text="Kurz und ohne Namen, z. B. „HOF“."
    )
    bezeichnung = models.CharField("Bezeichnung", max_length=200)
    anschrift = models.TextField("Anschrift", blank=True)
    aktiv = models.BooleanField("aktiv", default=True)

    class Meta:
        verbose_name = "Objekt"
        verbose_name_plural = "Objekte"
        ordering = ("kuerzel",)

    def __str__(self) -> str:
        return self.kuerzel


class Lauf(models.Model):
    """Ein Durchgang eines Management-Befehls mit Ergebnis und drei Körben.

    Nicht im Protokoll: Der Lauf ist selbst ein Protokoll. Änderungen, die ein Lauf
    macht, tragen ihn als Kontext.
    """

    class Status(models.TextChoices):
        LAEUFT = "laeuft", "läuft"
        ERFOLGREICH = "erfolgreich", "erfolgreich"
        MIT_FEHLERN = "mit_fehlern", "mit Fehlern"
        ABGEBROCHEN = "abgebrochen", "abgebrochen"

    name = models.CharField("Name", max_length=100, db_index=True)
    gestartet_am = models.DateTimeField("gestartet am")
    beendet_am = models.DateTimeField("beendet am", null=True, blank=True)
    status = models.CharField(
        "Status", max_length=20, choices=Status.choices, default=Status.LAEUFT
    )
    gebucht = models.PositiveIntegerField("gebucht", default=0)
    arbeitsposten = models.PositiveIntegerField("Arbeitsposten", default=0)
    fehler = models.PositiveIntegerField("Fehler", default=0)
    meldung = models.TextField(
        "Meldung", blank=True, help_text="Nur IDs und Zähler, keine Personendaten."
    )

    class Meta:
        verbose_name = "Lauf"
        verbose_name_plural = "Läufe"
        ordering = ("-gestartet_am", "-pk")

    def __str__(self) -> str:
        return f"Lauf #{self.pk} {self.name}"

    @property
    def dauer_sekunden(self) -> float | None:
        if self.beendet_am is None:
            return None
        return (self.beendet_am - self.gestartet_am).total_seconds()


class Arbeitsposten(ProtokolliertesModell):
    """Etwas, das auf eine Entscheidung des Benutzers wartet."""

    class Status(models.TextChoices):
        OFFEN = "offen", "offen"
        ERLEDIGT = "erledigt", "erledigt"
        ZURUECKGESTELLT = "zurueckgestellt", "zurückgestellt"

    art = models.CharField("Art", max_length=100, db_index=True)
    bezug_typ = models.ForeignKey(
        ContentType,
        verbose_name="Bezug (Typ)",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    bezug_id = models.PositiveBigIntegerField("Bezug (ID)", null=True, blank=True)
    bezug = GenericForeignKey("bezug_typ", "bezug_id")
    status = models.CharField(
        "Status", max_length=20, choices=Status.choices, default=Status.OFFEN, db_index=True
    )
    aktionen = ArrayField(
        models.CharField(max_length=50),
        verbose_name="Aktionen",
        default=list,
        blank=True,
        help_text="Schlüssel der Aktionen aus apps.kern.aktionen.",
    )
    hinweis = models.TextField("Hinweis", blank=True)
    faellig_am = models.DateField("fällig am", null=True, blank=True)
    erledigt_am = models.DateTimeField("erledigt am", null=True, blank=True)
    angelegt_am = models.DateTimeField("angelegt am", auto_now_add=True)
    lauf = models.ForeignKey(
        Lauf,
        verbose_name="angelegt von Lauf",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="arbeitsposten_angelegt",
    )

    class Meta:
        verbose_name = "Arbeitsposten"
        verbose_name_plural = "Arbeitsposten"
        ordering = ("faellig_am", "angelegt_am", "pk")
        indexes = (models.Index(fields=["bezug_typ", "bezug_id"]),)

    def __str__(self) -> str:
        return f"Arbeitsposten #{self.pk} {self.art}"


# --- Personenfelder (CLAUDE.md, Regel 4.6) ------------------------------------------

personenfelder.register(Benutzer, ["username", "first_name", "last_name", "email", "password"])
personenfelder.register(Mandant, ["bezeichnung"])
personenfelder.unbedenklich(Mandant, ["art", "kuerzel"])
personenfelder.register(Objekt, ["anschrift"])
personenfelder.unbedenklich(Objekt, ["kuerzel", "bezeichnung"])
personenfelder.unbedenklich(Lauf, ["name", "status", "meldung"])
personenfelder.register(Arbeitsposten, ["hinweis"])
personenfelder.unbedenklich(Arbeitsposten, ["art", "status"])
