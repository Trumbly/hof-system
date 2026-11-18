"""Modelle des Mahnwesens (Schritt 7, H4.1 bis H4.3, H4.6): Stufen, Fälle, Schritte, Sperren.

Geschrieben wird nur über ``apps.mahnwesen.services`` (CLAUDE.md, Regel 4.1).

- ``Mahnstufe``: die Stufenfolge je Vertragsart mit Auslöser, Frist, Vorlage und Versandweg.
  Die Startdaten nach der SOP stehen in ``apps.mahnwesen.startdaten``; geändert wird in der
  Oberfläche (Seite „Stufen“).
- ``Mahnfall``: ein Rückstand eines Vertrags mit den offenen Posten, der aktuellen Stufe und
  wann sie belegt ist. Je Vertrag ist höchstens ein Fall offen.
- ``Mahnschritt``: eine vorgeschlagene Stufe eines Falls (der Stufenverlauf), mit dem
  Schreiben im Status „bereit“ oder im Probebetrieb ohne Schreiben.
- ``Mahnsperre``: hält die Mahnung eines Vertrags an (Rechtsfall, Klärfall, Ratenplan, Hand).
"""

from __future__ import annotations

import datetime

from django.db import models
from django.db.models import Q

from apps.dokumente import vorlagenarten
from apps.dokumente.models import Schreiben
from apps.dokumente.vorlagenarten import Versandweg
from apps.kern import personenfelder
from apps.kern.models import Arbeitsposten, Lauf
from apps.kern.protokoll import ProtokolliertesModell
from apps.soll.models import Sollposten
from apps.stammdaten.models import Vertrag


def _vorlagenart_pruefen(wert: str) -> None:
    from django.core.exceptions import ValidationError

    try:
        vorlagenarten.holen(wert)
    except vorlagenarten.UnbekannteVorlagenart as fehler:
        raise ValidationError(f"Unbekannte Vorlagenart: {wert}") from fehler


class Mahnstufe(ProtokolliertesModell):
    """Eine Stufe der Stufenfolge einer Vertragsart (Baustein „Stufenfolge mit Fristen“)."""

    class Ausloeser(models.TextChoices):
        TAGE_NACH_FAELLIGKEIT = "tage_nach_faelligkeit", "Tage nach Fälligkeit"
        TAGE_NACH_VORSTUFE = "tage_nach_vorstufe", "Tage nach der vorigen Stufe"
        OFFENE_MONATSMIETEN = "offene_monatsmieten", "offene Monatsmieten (Anzahl)"
        RUECKSTAND_MONATSMIETEN = "rueckstand_monatsmieten", "Rückstand in Monatsmieten"

    VERSANDWEGE = tuple(Versandweg.TITEL.items())

    vertragsart = models.CharField("Vertragsart", max_length=2, choices=Vertrag.Art.choices)
    reihenfolge = models.PositiveSmallIntegerField("Reihenfolge")
    schluessel = models.CharField(
        "Schlüssel",
        max_length=30,
        help_text="Kurz und gleich über alle Vertragsarten, z. B. „erinnerung“, „mahnung“.",
    )
    titel = models.CharField("Titel", max_length=100)
    ausloeser = models.CharField("Auslöser", max_length=30, choices=Ausloeser.choices)
    ausloeser_wert = models.PositiveSmallIntegerField(
        "Wert des Auslösers", help_text="Tage oder Anzahl der Monatsmieten."
    )
    frist_tage = models.PositiveSmallIntegerField(
        "Frist (Tage)",
        default=0,
        help_text="Frist im Schreiben ab dem Stichtag; die nächste Stufe folgt frühestens "
        "so viele Tage, nachdem diese belegt ist.",
    )
    vorlagenart = models.CharField("Vorlagenart", max_length=50, validators=[_vorlagenart_pruefen])
    vorlage_name = models.CharField(
        "Name der Vorlage",
        max_length=200,
        blank=True,
        help_text="Leer: die neueste freigegebene Vorlage der Art.",
    )
    versandweg = models.CharField("Versandweg", max_length=30, choices=VERSANDWEGE)
    einseitige_erklaerung = models.BooleanField(
        "einseitige Erklärung",
        default=False,
        help_text="Belegt erst mit Zustellnachweis, sonst mit dem Senden.",
    )
    aktiv = models.BooleanField("aktiv", default=True)

    class Meta:
        verbose_name = "Mahnstufe"
        verbose_name_plural = "Mahnstufen"
        ordering = ("vertragsart", "reihenfolge")
        constraints = (
            models.UniqueConstraint(
                fields=("vertragsart", "reihenfolge"), name="mahnstufe_reihenfolge_je_art"
            ),
            models.UniqueConstraint(
                fields=("vertragsart", "schluessel"), name="mahnstufe_schluessel_je_art"
            ),
        )

    def __str__(self) -> str:
        return f"{self.vertragsart} {self.reihenfolge}. {self.titel}"

    @property
    def ist_brief(self) -> bool:
        """Abmahnung und Kündigung entstehen als Brief (Anschrift Pflicht)."""
        return self.versandweg != Versandweg.MAIL

    @property
    def anwalt_bestaetigung(self) -> bool:
        try:
            return vorlagenarten.holen(self.vorlagenart).anwalt_bestaetigung
        except vorlagenarten.UnbekannteVorlagenart:
            return False


class Mahnfall(ProtokolliertesModell):
    """Ein Rückstand eines Vertrags auf seinem Weg durch die Stufen."""

    class Status(models.TextChoices):
        OFFEN = "offen", "offen"
        GESPERRT = "gesperrt", "gesperrt"
        ERLEDIGT = "erledigt", "erledigt (bezahlt)"

    vertrag = models.ForeignKey(
        Vertrag, verbose_name="Vertrag", on_delete=models.PROTECT, related_name="mahnfaelle"
    )
    offene_posten = models.ManyToManyField(
        Sollposten, verbose_name="offene Posten", related_name="mahnfaelle", blank=True
    )
    stufe = models.ForeignKey(
        Mahnstufe,
        verbose_name="aktuelle Stufe",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    stufe_belegt_am = models.DateField("Stufe belegt am", null=True, blank=True)
    naechste_stufe_fruehestens = models.DateField("nächste Stufe frühestens", null=True, blank=True)
    status = models.CharField(
        "Status", max_length=20, choices=Status.choices, default=Status.OFFEN, db_index=True
    )
    sperrgrund = models.CharField(
        "gesperrt, weil",
        max_length=300,
        blank=True,
        help_text="Grund und IDs der Sperren, keine Namen.",
    )
    eroeffnet_am = models.DateField("eröffnet am")
    geschlossen_am = models.DateField("geschlossen am", null=True, blank=True)
    angelegt_am = models.DateTimeField("angelegt am", auto_now_add=True)

    class Meta:
        verbose_name = "Mahnfall"
        verbose_name_plural = "Mahnfälle"
        ordering = ("-eroeffnet_am", "-pk")
        constraints = (
            models.UniqueConstraint(
                fields=("vertrag",),
                condition=~Q(status="erledigt"),
                name="mahnfall_einer_je_vertrag",
            ),
            models.CheckConstraint(
                condition=Q(status="erledigt") | Q(geschlossen_am__isnull=True),
                name="mahnfall_geschlossen_nur_erledigt",
            ),
        )

    def __str__(self) -> str:
        return f"Mahnfall #{self.pk}"

    @property
    def ist_offen(self) -> bool:
        return self.status != self.Status.ERLEDIGT


class Mahnschritt(ProtokolliertesModell):
    """Eine vorgeschlagene Stufe eines Falls: der Stufenverlauf."""

    fall = models.ForeignKey(
        Mahnfall, verbose_name="Mahnfall", on_delete=models.PROTECT, related_name="schritte"
    )
    stufe = models.ForeignKey(
        Mahnstufe, verbose_name="Stufe", on_delete=models.PROTECT, related_name="schritte"
    )
    stichtag = models.DateField("Stichtag des Laufs", db_index=True)
    schreiben = models.ForeignKey(
        Schreiben,
        verbose_name="Schreiben",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    probebetrieb = models.BooleanField(
        "Probebetrieb", default=False, help_text="Keine freigegebene Vorlage: kein Schreiben."
    )
    hinweis = models.CharField(
        "Hinweis", max_length=300, blank=True, help_text="Nur Arten, IDs und Daten."
    )
    belegt_am = models.DateField("belegt am", null=True, blank=True)
    abgebrochen_am = models.DateField(
        "abgebrochen am",
        null=True,
        blank=True,
        help_text="Fall bezahlt oder gesperrt, bevor die Stufe belegt war.",
    )
    vermerk = models.TextField(
        "Vermerk", blank=True, help_text="Beleg von Hand, z. B. Einwurf durch Boten."
    )
    arbeitsposten = models.ForeignKey(
        Arbeitsposten,
        verbose_name="Arbeitsposten",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    lauf = models.ForeignKey(
        Lauf, verbose_name="Lauf", on_delete=models.PROTECT, null=True, blank=True
    )
    angelegt_am = models.DateTimeField("angelegt am", auto_now_add=True)

    class Meta:
        verbose_name = "Mahnschritt"
        verbose_name_plural = "Mahnschritte"
        ordering = ("fall", "stichtag", "pk")

    def __str__(self) -> str:
        return f"Mahnschritt #{self.pk}"

    @property
    def verworfen(self) -> bool:
        return self.schreiben is not None and self.schreiben.status == Schreiben.Status.VERWORFEN

    @property
    def offen(self) -> bool:
        """Weder belegt noch abgebrochen noch verworfen: Der Fall wartet auf diesen Schritt."""
        return self.belegt_am is None and self.abgebrochen_am is None and not self.verworfen


class Mahnsperre(ProtokolliertesModell):
    """Hält die Mahnung eines Vertrags an (Standardablauf 3, Schritt 2)."""

    class Grund(models.TextChoices):
        RECHTSFALL = "rechtsfall", "Rechtsfall"
        KLAERFALL = "klaerfall", "Klärfall"
        RATENPLAN = "ratenplan", "Ratenplan"
        HAND = "hand", "von Hand"

    vertrag = models.ForeignKey(
        Vertrag, verbose_name="Vertrag", on_delete=models.PROTECT, related_name="mahnsperren"
    )
    grund = models.CharField("Grund", max_length=20, choices=Grund.choices)
    von = models.DateField("gilt ab")
    bis = models.DateField("bis", null=True, blank=True, help_text="Leer: bis auf Weiteres.")
    automatisch = models.BooleanField(
        "automatisch", default=False, help_text="Vom Mahnlauf gesetzt (offener Klärfall)."
    )
    hinweis = models.TextField("Hinweis", blank=True)
    aufgehoben_am = models.DateField("aufgehoben am", null=True, blank=True)
    angelegt_am = models.DateTimeField("angelegt am", auto_now_add=True)

    class Meta:
        verbose_name = "Mahnsperre"
        verbose_name_plural = "Mahnsperren"
        ordering = ("-von", "-pk")
        constraints = (
            models.CheckConstraint(
                condition=Q(bis__isnull=True) | Q(bis__gte=models.F("von")),
                name="mahnsperre_zeitraum",
            ),
        )

    def __str__(self) -> str:
        return f"Mahnsperre #{self.pk} ({self.grund})"

    def gilt_am(self, tag: datetime.date) -> bool:
        if self.von > tag or (self.bis is not None and self.bis < tag):
            return False
        return self.aufgehoben_am is None or self.aufgehoben_am > tag


# --- Personenfelder (CLAUDE.md, Regel 4.6) ------------------------------------------

personenfelder.unbedenklich(
    Mahnstufe,
    [
        "vertragsart",
        "schluessel",
        "titel",
        "ausloeser",
        "vorlagenart",
        "vorlage_name",
        "versandweg",
    ],
)
personenfelder.unbedenklich(Mahnfall, ["status", "sperrgrund"])
personenfelder.unbedenklich(Mahnschritt, ["hinweis"])
personenfelder.register(Mahnschritt, ["vermerk"])
personenfelder.unbedenklich(Mahnsperre, ["grund"])
personenfelder.register(Mahnsperre, ["hinweis"])
