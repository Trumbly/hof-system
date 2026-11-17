"""Modelle des Kassenbuchs: Kategorien, Regeln, Buchungen, Monatsabschluss und Monatsbericht
(H6.3, H6.5, H6.6, H10.2, B9).

Geschrieben wird nur über ``apps.kassenbuch.services`` (CLAUDE.md, Regel 4.1).

Kern der Kette: Ein ``Bankumsatz`` wird zu einer oder mehreren ``Buchung``\\ en mit Kategorie,
Objekt, Mandant, Leistungsjahr und umlagefähigem Anteil. Die Summe der gültigen Buchungen
eines Umsatzes ist sein Betrag. Gebuchtes wird nie geändert oder gelöscht, sondern
storniert: Die Stornobuchung zeigt mit ``storno_von`` auf das Original; das Original selbst
bleibt unverändert (``original.storniert_durch`` liest die Stornobuchung). Eine Sperre in der
Datenbank verhindert jede Änderung an einer Buchung.
"""

from __future__ import annotations

from decimal import Decimal

import pgtrigger
from django.db import models

from apps.bank.models import Bankkonto, Bankumsatz
from apps.kern import personenfelder
from apps.kern.models import Lauf, Mandant, Objekt
from apps.kern.protokoll import ProtokolliertesModell


class Kategorie(ProtokolliertesModell):
    """Wofür Geld kommt oder geht. Entnahmen (B9) haben je Mandant eine eigene Kategorie."""

    class Art(models.TextChoices):
        EINNAHME = "einnahme", "Einnahme"
        AUSGABE = "ausgabe", "Ausgabe"
        ENTNAHME = "entnahme", "Entnahme"
        UMBUCHUNG = "umbuchung", "Umbuchung"

    # Nicht „name“: Das hieße im Log-Filter der Personenfelder auch der Name des Loggers.
    bezeichnung = models.CharField("Bezeichnung", max_length=200, unique=True)
    art = models.CharField("Art", max_length=20, choices=Art.choices)
    mandant = models.ForeignKey(
        Mandant,
        verbose_name="Mandant der Entnahme",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="entnahme_kategorien",
        help_text="Nur bei Entnahmen (Pflicht): Wessen Entnahme ist das?",
    )
    wohnraum = models.BooleanField(
        "Wohnraum",
        default=False,
        help_text="Miete für Wohnraum (führt Verwaltio): wird gebucht, aber nie gegen "
        "Sollposten abgeglichen.",
    )
    umlagefaehiger_anteil = models.DecimalField(
        "umlagefähiger Anteil (Vorgabe, %)",
        max_digits=5,
        decimal_places=2,
        default=Decimal("0"),
    )
    aktiv = models.BooleanField("aktiv", default=True)

    class Meta:
        verbose_name = "Kategorie"
        verbose_name_plural = "Kategorien"
        ordering = ("art", "bezeichnung")
        constraints = (
            models.CheckConstraint(
                condition=(
                    models.Q(art="entnahme", mandant__isnull=False)
                    | (~models.Q(art="entnahme") & models.Q(mandant__isnull=True))
                ),
                name="kassenbuch_kategorie_entnahme_mit_mandant",
            ),
            models.CheckConstraint(
                condition=models.Q(umlagefaehiger_anteil__gte=0, umlagefaehiger_anteil__lte=100),
                name="kassenbuch_kategorie_umlage_prozent",
            ),
        )

    def __str__(self) -> str:
        return f"Kategorie #{self.pk}"


class Zuordnungsregel(ProtokolliertesModell):
    """Ordnet Umsätze ein: Bedingungen, Ergebnis und Priorität (H6.3).

    Eine Regel passt, wenn *alle* gesetzten Bedingungen passen. Eindeutig ist ein Umsatz,
    wenn genau eine Regel mit der höchsten Priorität passt. Ab Schritt 6 nennt die Regel
    zusätzlich den Vertrag (H1.4); das Feld kommt dann dazu.
    """

    bezeichnung = models.CharField("Bezeichnung", max_length=200)
    # Bedingungen
    gegenpartei = models.CharField(
        "Gegenpartei enthält",
        max_length=200,
        blank=True,
        help_text="Teil des Namens, ohne Groß- und Kleinschreibung.",
    )
    gegen_iban = models.CharField("Gegen-IBAN", max_length=34, blank=True)
    zweck = models.CharField(
        "Verwendungszweck enthält",
        max_length=200,
        blank=True,
        help_text="Teil des Zwecks oder, mit „Regex“, ein regulärer Ausdruck.",
    )
    zweck_regex = models.BooleanField("Zweck als Regex", default=False)
    betrag_von = models.DecimalField(
        "Betrag ab", max_digits=12, decimal_places=2, null=True, blank=True
    )
    betrag_bis = models.DecimalField(
        "Betrag bis", max_digits=12, decimal_places=2, null=True, blank=True
    )
    richtung = models.CharField(
        "Richtung", max_length=10, choices=Bankumsatz.Richtung.choices, blank=True
    )
    konto = models.ForeignKey(
        Bankkonto,
        verbose_name="nur Konto",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="zuordnungsregeln",
    )
    # Ergebnis
    kategorie = models.ForeignKey(
        Kategorie, verbose_name="Kategorie", on_delete=models.PROTECT, related_name="regeln"
    )
    objekt = models.ForeignKey(
        Objekt, verbose_name="Objekt", on_delete=models.PROTECT, related_name="zuordnungsregeln"
    )
    mandant = models.ForeignKey(
        Mandant, verbose_name="Mandant", on_delete=models.PROTECT, related_name="zuordnungsregeln"
    )
    prioritaet = models.IntegerField("Priorität", default=10, help_text="Die höhere Zahl gewinnt.")
    aktiv = models.BooleanField("aktiv", default=True)
    angelegt_am = models.DateTimeField("angelegt am", auto_now_add=True)

    class Meta:
        verbose_name = "Zuordnungsregel"
        verbose_name_plural = "Zuordnungsregeln"
        ordering = ("-prioritaet", "pk")

    def __str__(self) -> str:
        return f"Zuordnungsregel #{self.pk}"


class Buchung(ProtokolliertesModell):
    """Ein Teil eines Bankumsatzes im Kassenbuch. Wird nie geändert, nur storniert."""

    umsatz = models.ForeignKey(
        Bankumsatz, verbose_name="Umsatz", on_delete=models.PROTECT, related_name="buchungen"
    )
    datum = models.DateField(
        "Datum im Kassenbuch",
        db_index=True,
        help_text="Meist der Buchungstag des Umsatzes; liegt der Monat schon abgeschlossen, "
        "ein Tag im ersten offenen Monat.",
    )
    betrag = models.DecimalField("Betrag", max_digits=12, decimal_places=2)
    richtung = models.CharField("Richtung", max_length=10, choices=Bankumsatz.Richtung.choices)
    kategorie = models.ForeignKey(
        Kategorie, verbose_name="Kategorie", on_delete=models.PROTECT, related_name="buchungen"
    )
    objekt = models.ForeignKey(
        Objekt, verbose_name="Objekt", on_delete=models.PROTECT, related_name="buchungen"
    )
    mandant = models.ForeignKey(
        Mandant, verbose_name="Mandant", on_delete=models.PROTECT, related_name="buchungen"
    )
    leistungsjahr = models.PositiveSmallIntegerField("Leistungsjahr")
    umlagefaehiger_anteil = models.DecimalField(
        "umlagefähiger Anteil (%)", max_digits=5, decimal_places=2, default=Decimal("0")
    )
    wohnraum = models.BooleanField(
        "Wohnraum", default=False, help_text="Kassenbuch ja, Abgleich gegen Sollposten nie."
    )
    regel = models.ForeignKey(
        Zuordnungsregel,
        verbose_name="gebucht nach Regel",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="buchungen",
    )
    lauf = models.ForeignKey(
        Lauf,
        verbose_name="gebucht von Lauf",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="kassenbuchungen",
    )
    storno_von = models.OneToOneField(
        "self",
        verbose_name="storniert die Buchung",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="storniert_durch",
    )
    angelegt_am = models.DateTimeField("angelegt am", auto_now_add=True)

    class Meta:
        verbose_name = "Buchung"
        verbose_name_plural = "Buchungen"
        ordering = ("-datum", "-pk")
        constraints = (
            models.CheckConstraint(
                condition=models.Q(betrag__gt=0), name="kassenbuch_buchung_betrag_positiv"
            ),
            models.CheckConstraint(
                condition=models.Q(umlagefaehiger_anteil__gte=0, umlagefaehiger_anteil__lte=100),
                name="kassenbuch_buchung_umlage_prozent",
            ),
        )
        indexes = (models.Index(fields=["objekt", "datum"]),)
        triggers = (
            # Gebuchtes wird nie geändert (CLAUDE.md, Regel 4.4), auch nicht am Service vorbei.
            pgtrigger.Protect(
                name="kassenbuch_buchung_unveraenderlich", operation=pgtrigger.Update
            ),
        )

    def __str__(self) -> str:
        return f"Buchung #{self.pk}"

    @property
    def ist_storno(self) -> bool:
        return self.storno_von_id is not None


class Monatsabschluss(ProtokolliertesModell):
    """Abschluss eines Monats für einen Mandanten und dessen Bankkonten (H6.6).

    Ohne ``abgeschlossen_am`` ist der Abschluss versucht, aber offen (Lücken stehen in der
    Arbeitsliste). Mit ``abgeschlossen_am`` ist der Monat gesperrt: Buchungen mit Datum im
    Monat lassen sich weder anlegen noch ändern. Die Salden tragen ihr Vorzeichen.
    """

    mandant = models.ForeignKey(
        Mandant, verbose_name="Mandant", on_delete=models.PROTECT, related_name="monatsabschluesse"
    )
    monat = models.DateField("Monat", help_text="Erster Tag des Monats.")
    saldo_bank = models.DecimalField(
        "Saldo Bank", max_digits=14, decimal_places=2, null=True, blank=True
    )
    saldo_buch = models.DecimalField(
        "Saldo Buch", max_digits=14, decimal_places=2, null=True, blank=True
    )
    abgeschlossen_am = models.DateTimeField("abgeschlossen am", null=True, blank=True)
    lauf = models.ForeignKey(
        Lauf,
        verbose_name="Lauf",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="monatsabschluesse",
    )

    class Meta:
        verbose_name = "Monatsabschluss"
        verbose_name_plural = "Monatsabschlüsse"
        ordering = ("-monat", "mandant")
        constraints = (
            models.UniqueConstraint(fields=["mandant", "monat"], name="kassenbuch_abschluss_monat"),
            models.CheckConstraint(
                condition=models.Q(monat__day=1), name="kassenbuch_abschluss_monatserster"
            ),
        )
        triggers = (
            # Ein abgeschlossener Monat bleibt abgeschlossen.
            pgtrigger.Protect(
                name="kassenbuch_abschluss_endgueltig",
                operation=pgtrigger.Update,
                condition=pgtrigger.Q(old__abgeschlossen_am__isnull=False),
            ),
        )

    def __str__(self) -> str:
        return f"Monatsabschluss #{self.pk}"

    @property
    def abgeschlossen(self) -> bool:
        return self.abgeschlossen_am is not None


def _bericht_pfad(bericht: Monatsbericht, dateiname: str) -> str:
    return f"kassenbuch/monatsberichte/{bericht.monat:%Y}/{dateiname}"


class Monatsbericht(ProtokolliertesModell):
    """Bericht je Objekt und Monat (H10.2), entsteht nach dem Abschluss als PDF.

    Der Dateiname besteht aus Objektkürzel und Monat, ohne Personennamen.
    """

    objekt = models.ForeignKey(
        Objekt, verbose_name="Objekt", on_delete=models.PROTECT, related_name="monatsberichte"
    )
    monat = models.DateField("Monat", help_text="Erster Tag des Monats.")
    einnahmen = models.DecimalField("Einnahmen", max_digits=14, decimal_places=2)
    kosten = models.DecimalField("Kosten", max_digits=14, decimal_places=2)
    ergebnis = models.DecimalField("Ergebnis", max_digits=14, decimal_places=2)
    entnahmen = models.DecimalField("Entnahmen", max_digits=14, decimal_places=2)
    datei = models.FileField("PDF", upload_to=_bericht_pfad, blank=True, max_length=200)
    erstellt_am = models.DateTimeField("erstellt am", auto_now=True)
    lauf = models.ForeignKey(
        Lauf,
        verbose_name="Lauf",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="monatsberichte",
    )

    class Meta:
        verbose_name = "Monatsbericht"
        verbose_name_plural = "Monatsberichte"
        ordering = ("-monat", "objekt")
        constraints = (
            models.UniqueConstraint(fields=["objekt", "monat"], name="kassenbuch_bericht_monat"),
        )

    def __str__(self) -> str:
        return f"Monatsbericht #{self.pk}"


# --- Personenfelder (CLAUDE.md, Regel 4.6) ------------------------------------------
# Kategorienamen kommen aus einer Liste von Max und können Namen tragen („Entnahme …“),
# Regeln nennen Zahler, IBANs und Zwecke: beides gilt als Personendaten.

personenfelder.register(Kategorie, ["bezeichnung"])
personenfelder.unbedenklich(Kategorie, ["art"])
personenfelder.register(Zuordnungsregel, ["bezeichnung", "gegenpartei", "gegen_iban", "zweck"])
personenfelder.unbedenklich(Zuordnungsregel, ["richtung"])
personenfelder.unbedenklich(Buchung, ["richtung"])
personenfelder.unbedenklich(Monatsbericht, ["datei"])
