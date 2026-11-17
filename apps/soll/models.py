"""Modelle der Sollstellung (Schritt 5, H3.4): Sollposten und Regeln je Vertrag.

Geschrieben wird nur über ``apps.soll.services`` (CLAUDE.md, Regel 4.1).

Ein ``Sollposten`` ist eine Forderung an einen Vertrag für einen Zeitraum: Miete oder
Pauschale aus den Konditionen (``quelle = vertrag``), Kaution oder Sonstiges von Hand
(``quelle = hand``). Beträge sind positiv; ``betrag`` ist der Zahlbetrag aus ``netto`` und
``steuer``. Ein Posten wird nie geändert oder gelöscht, sondern storniert
(CLAUDE.md, Regel 4.4); ein Ersatzposten zeigt über ``ersetzt`` zurück auf den alten.

``bezahlt`` und die Status „teilweise“ und „bezahlt“ setzt ab Schritt 6 der Abgleich über
``services.zahlungsstand_setzen``.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import models
from django.db.models import F, Q

from apps.kern import personenfelder
from apps.kern.protokoll import ProtokolliertesModell
from apps.stammdaten.models import Vertrag


class Sollposten(ProtokolliertesModell):
    class Art(models.TextChoices):
        MIETE = "miete", "Miete"
        PAUSCHALE = "pauschale", "Pauschale"
        KAUTION = "kaution", "Kaution"
        SONSTIGES = "sonstiges", "Sonstiges"

    class Status(models.TextChoices):
        OFFEN = "offen", "offen"
        TEILWEISE = "teilweise", "teilweise bezahlt"
        BEZAHLT = "bezahlt", "bezahlt"
        STORNIERT = "storniert", "storniert"

    class Quelle(models.TextChoices):
        VERTRAG = "vertrag", "aus dem Vertrag"
        HAND = "hand", "von Hand"

    vertrag = models.ForeignKey(
        Vertrag, verbose_name="Vertrag", on_delete=models.PROTECT, related_name="sollposten"
    )
    art = models.CharField("Art", max_length=20, choices=Art.choices)
    quelle = models.CharField(
        "Quelle", max_length=10, choices=Quelle.choices, default=Quelle.VERTRAG
    )
    von = models.DateField("Zeitraum von")
    bis = models.DateField("Zeitraum bis")
    netto = models.DecimalField("netto", max_digits=12, decimal_places=2)
    steuersatz = models.DecimalField(
        "Umsatzsteuer (%)", max_digits=5, decimal_places=2, default=Decimal("0")
    )
    steuer = models.DecimalField(
        "Umsatzsteuer", max_digits=12, decimal_places=2, default=Decimal("0")
    )
    betrag = models.DecimalField("Betrag", max_digits=12, decimal_places=2)
    faellig_am = models.DateField("fällig am", db_index=True)
    status = models.CharField(
        "Status", max_length=20, choices=Status.choices, default=Status.OFFEN, db_index=True
    )
    bezahlt = models.DecimalField("bezahlt", max_digits=12, decimal_places=2, default=Decimal("0"))
    bezeichnung = models.CharField(
        "Bezeichnung",
        max_length=200,
        blank=True,
        help_text="Nur bei Posten von Hand, z. B. „Ersatz Handsender“. Keine Namen.",
    )
    storniert_am = models.DateField("storniert am", null=True, blank=True)
    storno_grund = models.CharField("Grund des Stornos", max_length=200, blank=True)
    ersetzt = models.ForeignKey(
        "self",
        verbose_name="ersetzt Posten",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ersatzposten",
        help_text="Der stornierte Posten, an dessen Stelle dieser getreten ist.",
    )
    angelegt_am = models.DateTimeField("angelegt am", auto_now_add=True)

    class Meta:
        verbose_name = "Sollposten"
        verbose_name_plural = "Sollposten"
        ordering = ("vertrag", "von", "art", "pk")
        indexes = (models.Index(fields=["vertrag", "status"]),)
        constraints = (
            # Idempotenz: je Vertrag, Art und Zeitraum höchstens ein Posten, der gilt.
            models.UniqueConstraint(
                fields=("vertrag", "art", "von", "bis"),
                condition=~Q(status="storniert"),
                name="soll_einmal_je_zeitraum",
            ),
            models.CheckConstraint(condition=Q(bis__gte=F("von")), name="soll_zeitraum"),
            models.CheckConstraint(
                condition=Q(betrag__gt=0) & Q(netto__gt=0) & Q(steuer__gte=0),
                name="soll_betrag_positiv",
            ),
            models.CheckConstraint(
                condition=Q(betrag=F("netto") + F("steuer")), name="soll_betrag_netto_steuer"
            ),
            models.CheckConstraint(
                condition=Q(bezahlt__gte=0) & Q(bezahlt__lte=F("betrag")),
                name="soll_bezahlt_im_betrag",
            ),
            models.CheckConstraint(
                condition=~Q(status="storniert") | Q(storniert_am__isnull=False),
                name="soll_storno_mit_datum",
            ),
        )

    def __str__(self) -> str:
        return f"Sollposten #{self.pk}"

    @property
    def offen(self) -> Decimal:
        """Was noch zu zahlen ist (0 bei stornierten Posten)."""
        if self.status == self.Status.STORNIERT:
            return Decimal("0.00")
        return self.betrag - self.bezahlt

    @property
    def zeitraum_text(self) -> str:
        if self.von == self.bis:
            return f"{self.von:%d.%m.%Y}"
        return f"{self.von:%d.%m.%Y}–{self.bis:%d.%m.%Y}"


class Sollregel(ProtokolliertesModell):
    """Was je Vertrag von der Standardregel der Sollstellung abweicht.

    Fehlt die Zeile, gelten ``konfiguration.toml`` (Fälligkeit) und ``HOF_SOLL_BEGINN``.
    """

    class Faelligkeit(models.TextChoices):
        STANDARD = "", "Standard (konfiguration.toml)"
        WERKTAG = "werktag", "n-ter Werktag des Monats"
        KALENDERTAG = "kalendertag", "fester Tag des Monats"

    vertrag = models.OneToOneField(
        Vertrag, verbose_name="Vertrag", on_delete=models.PROTECT, related_name="sollregel"
    )
    faelligkeit = models.CharField(
        "Fälligkeit", max_length=20, choices=Faelligkeit.choices, blank=True, default=""
    )
    faelligkeit_tag = models.PositiveSmallIntegerField(
        "Tag", null=True, blank=True, help_text="Werktag (1–10) oder Tag im Monat (1–31)."
    )
    soll_ab = models.DateField(
        "Sollstellung ab",
        null=True,
        blank=True,
        help_text="Erster Tag, ab dem das Hof-System das Soll führt (vorher das alte System). "
        "Leer: HOF_SOLL_BEGINN bzw. Vertragsbeginn.",
    )

    class Meta:
        verbose_name = "Regel der Sollstellung"
        verbose_name_plural = "Regeln der Sollstellung"

    def __str__(self) -> str:
        return f"Sollregel #{self.pk}"


# --- Personenfelder (CLAUDE.md, Regel 4.6) ------------------------------------------

personenfelder.unbedenklich(Sollposten, ["art", "quelle", "status", "bezeichnung"])
personenfelder.register(Sollposten, ["storno_grund"])
personenfelder.unbedenklich(Sollregel, ["faelligkeit"])
