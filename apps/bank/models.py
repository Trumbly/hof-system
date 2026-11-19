"""Modelle der Bank: Konten, Freigaben, Abrufe, Umsätze und Kontostände (H3.1 bis H3.3).

Geschrieben wird nur über ``apps.bank.services`` (CLAUDE.md, Regel 4.1).
"""

from __future__ import annotations

from django.db import models

from apps.kern import personenfelder, zeit
from apps.kern.models import Lauf, Mandant
from apps.kern.protokoll import ProtokolliertesModell


class Bankkonto(ProtokolliertesModell):
    """Ein eigenes Konto des Hofs. Fremde Konten stehen nur als Gegen-IBAN am Umsatz."""

    class Zugang(models.TextChoices):
        ENABLE_BANKING = "enable_banking", "Enable Banking (PSD2, auf dem Server)"
        ZULIEFERUNG = "zulieferung", "Zulieferung vom hof-mac (FinTS)"
        NUR_IMPORT = "nur_import", "nur Import"

    bank = models.CharField("Bank", max_length=100, help_text="z. B. „Vivid“ oder „VR“.")
    mandant = models.ForeignKey(
        Mandant, verbose_name="Mandant", on_delete=models.PROTECT, related_name="bankkonten"
    )
    bezeichnung = models.CharField("Bezeichnung", max_length=200)
    iban = models.CharField("IBAN", max_length=34, unique=True)
    zugang = models.CharField("Zugang", max_length=20, choices=Zugang.choices)
    waehrung = models.CharField("Währung", max_length=3, default="EUR")
    aktiv = models.BooleanField("aktiv", default=True)
    eb_bank_name = models.CharField(
        "Name der Bank bei Enable Banking",
        max_length=100,
        blank=True,
        help_text="ASPSP-Name, z. B. „Vivid Money“. Nur für Zugang Enable Banking.",
    )
    eb_konto_uid = models.CharField(
        "Konto-ID bei Enable Banking",
        max_length=100,
        blank=True,
        help_text="Wird bei jeder Freigabe aus der Sitzung übernommen.",
    )
    zulieferung_token_hash = models.CharField(
        "Hash des Zulieferungs-Tokens",
        max_length=64,
        blank=True,
        db_index=True,
        help_text="SHA-256 des Tokens. Der Token selbst steht nirgends im System.",
    )

    class Meta:
        verbose_name = "Bankkonto"
        verbose_name_plural = "Bankkonten"
        ordering = ("bezeichnung", "pk")

    def __str__(self) -> str:
        return f"Bankkonto #{self.pk}"


class Bankfreigabe(ProtokolliertesModell):
    """Die Zustimmung bei der Bank, Umsätze lesen zu dürfen (H3.2), mit Ablaufdatum."""

    konto = models.ForeignKey(
        Bankkonto, verbose_name="Konto", on_delete=models.PROTECT, related_name="freigaben"
    )
    gueltig_ab = models.DateField("gültig ab")
    gueltig_bis = models.DateField("gültig bis")
    sitzung_id = models.CharField(
        "Sitzung bei Enable Banking",
        max_length=100,
        blank=True,
        help_text="Leer bei Freigaben, die von Hand eingetragen sind (VR über FinTS).",
    )
    angelegt_am = models.DateTimeField("angelegt am", auto_now_add=True)

    class Meta:
        verbose_name = "Bankfreigabe"
        verbose_name_plural = "Bankfreigaben"
        ordering = ("konto", "-gueltig_bis", "-pk")

    def __str__(self) -> str:
        return f"Bankfreigabe #{self.pk}"


class Bankabruf(ProtokolliertesModell):
    """Ein Abruf oder eine Lieferung von Umsätzen für ein Konto und einen Zeitraum."""

    class Quelle(models.TextChoices):
        ENABLE_BANKING = "enable_banking", "Enable Banking"
        ZULIEFERUNG = "zulieferung", "Zulieferung vom hof-mac"
        IMPORT = "import", "Import"

    class Status(models.TextChoices):
        LAEUFT = "laeuft", "läuft"
        ERFOLGREICH = "erfolgreich", "erfolgreich"
        GESCHEITERT = "gescheitert", "gescheitert"

    konto = models.ForeignKey(
        Bankkonto, verbose_name="Konto", on_delete=models.PROTECT, related_name="abrufe"
    )
    quelle = models.CharField("Quelle", max_length=20, choices=Quelle.choices)
    von = models.DateField("von", null=True, blank=True)
    bis = models.DateField("bis", null=True, blank=True)
    status = models.CharField(
        "Status", max_length=20, choices=Status.choices, default=Status.LAEUFT
    )
    # Fachlicher Zeitpunkt (Review 3, Punkt 1): Der Tag des letzten Abrufs zählt für die
    # Zulieferung und den Datenstand, deshalb die eine Uhr.
    gestartet_am = models.DateTimeField("gestartet am", default=zeit.jetzt)
    beendet_am = models.DateTimeField("beendet am", null=True, blank=True)
    geliefert = models.PositiveIntegerField("geliefert", default=0)
    neu = models.PositiveIntegerField("neu", default=0)
    doppelt = models.PositiveIntegerField("schon vorhanden", default=0)
    meldung = models.TextField(
        "Meldung", blank=True, help_text="Nur IDs und Zähler, keine Personendaten."
    )
    lauf = models.ForeignKey(
        Lauf,
        verbose_name="Lauf",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="bankabrufe",
    )

    class Meta:
        verbose_name = "Bankabruf"
        verbose_name_plural = "Bankabrufe"
        ordering = ("-gestartet_am", "-pk")

    def __str__(self) -> str:
        return f"Bankabruf #{self.pk}"


class Bankumsatz(ProtokolliertesModell):
    """Ein gebuchter Umsatz, wie die Bank ihn liefert. Wird nie geändert.

    Der Fingerabdruck (``apps.bank.fingerabdruck``) verhindert Doppelte: Ein zweiter
    Abruf desselben Zeitraums liefert dieselben Fingerabdrücke, und die Datenbank nimmt
    jeden nur einmal an.
    """

    class Richtung(models.TextChoices):
        EINGANG = "eingang", "Eingang"
        AUSGANG = "ausgang", "Ausgang"

    konto = models.ForeignKey(
        Bankkonto, verbose_name="Konto", on_delete=models.PROTECT, related_name="umsaetze"
    )
    abruf = models.ForeignKey(
        Bankabruf,
        verbose_name="erster Abruf",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="umsaetze",
    )
    betrag = models.DecimalField("Betrag", max_digits=12, decimal_places=2)
    richtung = models.CharField("Richtung", max_length=10, choices=Richtung.choices)
    waehrung = models.CharField("Währung", max_length=3, default="EUR")
    buchungsdatum = models.DateField("Buchungsdatum", db_index=True)
    valuta = models.DateField("Valuta", null=True, blank=True)
    gegenpartei = models.CharField("Gegenpartei", max_length=200, blank=True)
    gegen_iban = models.CharField("Gegen-IBAN", max_length=34, blank=True)
    verwendungszweck = models.TextField("Verwendungszweck", blank=True)
    bank_referenz = models.CharField(
        "Referenz der Bank", max_length=100, blank=True, help_text="z. B. entry_reference"
    )
    rohdaten = models.JSONField("Rohdaten", default=dict, blank=True)
    laufende_nummer = models.PositiveSmallIntegerField(
        "laufende Nummer am Tag",
        default=1,
        help_text="Zählt gleiche Umsätze am selben Tag (zwei gleiche Zahlungen am Tag).",
    )
    fingerabdruck = models.CharField("Fingerabdruck", max_length=64)
    angelegt_am = models.DateTimeField("angelegt am", auto_now_add=True)

    class Meta:
        verbose_name = "Bankumsatz"
        verbose_name_plural = "Bankumsätze"
        ordering = ("-buchungsdatum", "-pk")
        constraints = (
            models.UniqueConstraint(fields=["fingerabdruck"], name="bank_umsatz_fingerabdruck"),
            models.CheckConstraint(
                condition=models.Q(betrag__gte=0), name="bank_umsatz_betrag_positiv"
            ),
        )
        indexes = (models.Index(fields=["konto", "buchungsdatum"]),)

    def __str__(self) -> str:
        return f"Bankumsatz #{self.pk}"


class Kontostand(ProtokolliertesModell):
    """Saldo eines Kontos zum Ende eines Tages, wie ihn die Bank meldet.

    Der Saldo trägt sein Vorzeichen (ein Konto kann im Minus stehen). Das ist kein Betrag
    einer Buchung, deshalb gilt Regel 4.2 („Beträge sind positiv“) hier nicht.
    """

    konto = models.ForeignKey(
        Bankkonto, verbose_name="Konto", on_delete=models.PROTECT, related_name="kontostaende"
    )
    datum = models.DateField("Stand zum Ende von")
    saldo = models.DecimalField("Saldo", max_digits=12, decimal_places=2)
    abruf = models.ForeignKey(
        Bankabruf,
        verbose_name="Abruf",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="kontostaende",
    )

    class Meta:
        verbose_name = "Kontostand"
        verbose_name_plural = "Kontostände"
        ordering = ("konto", "-datum")
        constraints = (
            models.UniqueConstraint(fields=["konto", "datum"], name="bank_kontostand_je_tag"),
        )

    def __str__(self) -> str:
        return f"Kontostand #{self.pk}"


# --- Personenfelder (CLAUDE.md, Regel 4.6) ------------------------------------------
# Eigene Konten sind unbedenklich (IBAN und Bezeichnung des Hofs). Personendaten stehen
# nur am Umsatz: Gegenpartei, Gegen-IBAN, Zweck und die Rohdaten der Bank.

personenfelder.unbedenklich(
    Bankkonto,
    [
        "bank",
        "bezeichnung",
        "iban",
        "zugang",
        "waehrung",
        "eb_bank_name",
        "eb_konto_uid",
        "zulieferung_token_hash",
    ],
)
personenfelder.unbedenklich(Bankfreigabe, ["sitzung_id"])
personenfelder.unbedenklich(Bankabruf, ["quelle", "status", "meldung"])
personenfelder.register(Bankumsatz, ["gegenpartei", "gegen_iban", "verwendungszweck", "rohdaten"])
personenfelder.unbedenklich(Bankumsatz, ["richtung", "waehrung", "bank_referenz", "fingerabdruck"])
