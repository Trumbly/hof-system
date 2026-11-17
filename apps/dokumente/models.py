"""Modelle für Vorlagen und Schreiben (Schritt 3c, H8.1 bis H8.4).

Geschrieben wird nur über ``apps.dokumente.services`` (CLAUDE.md, Regel 4.1).
Ablage, Dokumentbezug und Belege (Schritt 3a) kommen in dieselbe App.
"""

from __future__ import annotations

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import Q

from apps.dokumente import vorlagenarten
from apps.dokumente.vorlagenarten import Versandweg
from apps.kern import personenfelder
from apps.kern.models import Benutzer
from apps.kern.protokoll import ProtokolliertesModell


def _art_pruefen(wert: str) -> None:
    from django.core.exceptions import ValidationError

    try:
        vorlagenarten.holen(wert)
    except vorlagenarten.UnbekannteVorlagenart as fehler:
        raise ValidationError(f"Unbekannte Vorlagenart: {wert}") from fehler


class Vorlage(ProtokolliertesModell):
    """Ein Text mit Platzhaltern in einer Version (H8.1).

    Jede Version ist eine eigene Zeile: Eine freigegebene Version ändert sich nicht mehr,
    Änderungen entstehen als neue Version im Entwurf. Gibt der Benutzer eine neue Version
    frei, wird die vorige „abgelöst“; Schreiben zeigen weiter auf ihre Version.

    Der Text enthält nur Platzhalter, nie Namen, Anschriften oder Beträge (C2). Darum
    sind ``name``, ``betreff`` und ``text`` keine Personenfelder; ein Test prüft, dass
    keine Vorlage einen Namen aus dem Testhof enthält.
    """

    class Status(models.TextChoices):
        ENTWURF = "entwurf", "Entwurf"
        FREIGEGEBEN = "freigegeben", "freigegeben"
        ABGELOEST = "abgeloest", "abgelöst"

    name = models.CharField("Name", max_length=200)
    art = models.CharField("Art", max_length=50, validators=[_art_pruefen], db_index=True)
    version = models.PositiveIntegerField("Version", default=1)
    betreff = models.CharField("Betreff", max_length=300, blank=True)
    text = models.TextField("Text (HTML mit Platzhaltern)")
    status = models.CharField(
        "Status", max_length=20, choices=Status.choices, default=Status.ENTWURF, db_index=True
    )
    freigegeben_von = models.ForeignKey(
        Benutzer,
        verbose_name="freigegeben von",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    freigegeben_am = models.DateTimeField("freigegeben am", null=True, blank=True)
    herkunft = models.CharField(
        "Herkunft",
        max_length=50,
        blank=True,
        help_text="Woher der Text stammt, z. B. „notion“ oder „testhof“.",
    )
    angelegt_am = models.DateTimeField("angelegt am", auto_now_add=True)

    class Meta:
        verbose_name = "Vorlage"
        verbose_name_plural = "Vorlagen"
        ordering = ("art", "name", "-version")
        constraints = (
            models.UniqueConstraint(fields=("name", "version"), name="vorlage_name_version"),
            models.CheckConstraint(
                condition=~Q(status="freigegeben")
                | (Q(freigegeben_von__isnull=False) & Q(freigegeben_am__isnull=False)),
                name="vorlage_freigabe_mit_wem_und_wann",
            ),
        )

    def __str__(self) -> str:
        return f"{self.name} v{self.version}"

    @property
    def vorlagenart(self) -> vorlagenarten.Vorlagenart:
        return vorlagenarten.holen(self.art)

    @property
    def ist_freigegeben(self) -> bool:
        return self.status == self.Status.FREIGEGEBEN


def _pdf_pfad(schreiben: Schreiben, dateiname: str) -> str:
    # Der Dateiname kommt aus dem Service: Art, Datum und ID, nie ein Personenname.
    return f"schreiben/{schreiben.erzeugt_am:%Y/%m}/{dateiname}"


def _nachweis_pfad(schreiben: Schreiben, dateiname: str) -> str:
    endung = dateiname.rsplit(".", 1)[-1].lower() if "." in dateiname else "bin"
    return f"schreiben/{schreiben.erzeugt_am:%Y/%m}/zustellnachweis_{schreiben.pk}.{endung}"


class Schreiben(ProtokolliertesModell):
    """Ein erzeugtes Schreiben mit PDF, Versandweg und Versandstand (H8.2, H8.3).

    Es entsteht im Status „bereit“ und geht erst hinaus, wenn ein angemeldeter Benutzer
    nach der Vorschau des PDFs auf „Senden“ klickt (CLAUDE.md, Regel 8).
    """

    class Status(models.TextChoices):
        BEREIT = "bereit", "bereit"
        GESENDET = "gesendet", "gesendet"
        ZUGESTELLT = "zugestellt", "zugestellt"
        VERWORFEN = "verworfen", "verworfen"

    VERSANDWEGE = tuple(Versandweg.TITEL.items())

    vorlage = models.ForeignKey(
        Vorlage, verbose_name="Vorlage", on_delete=models.PROTECT, related_name="schreiben"
    )
    vorlage_version = models.PositiveIntegerField("Version der Vorlage")
    art = models.CharField("Art", max_length=50, db_index=True)
    einseitige_erklaerung = models.BooleanField(
        "einseitige Erklärung",
        default=False,
        help_text="Erst mit Zustellnachweis erledigt (aus der Vorlagenart).",
    )
    bezug_typ = models.ForeignKey(
        ContentType, verbose_name="Bezug (Typ)", on_delete=models.PROTECT, related_name="+"
    )
    bezug_id = models.PositiveBigIntegerField("Bezug (ID)")
    bezug = GenericForeignKey("bezug_typ", "bezug_id")
    serie = models.UUIDField("Serie", null=True, blank=True, db_index=True)

    betreff = models.CharField("Betreff", max_length=300, blank=True)
    inhalt_html = models.TextField("Inhalt (gerendert)", blank=True)
    empfaenger_email = models.EmailField("E-Mail des Empfängers", blank=True)
    pdf = models.FileField("PDF", upload_to=_pdf_pfad, blank=True)
    pdf_sha256 = models.CharField("Prüfsumme des PDFs", max_length=64, blank=True)

    versandweg = models.CharField("Versandweg", max_length=30, choices=VERSANDWEGE)
    status = models.CharField(
        "Status", max_length=20, choices=Status.choices, default=Status.BEREIT, db_index=True
    )
    erzeugt_am = models.DateTimeField("erzeugt am", auto_now_add=True)
    gesendet_am = models.DateTimeField("gesendet am", null=True, blank=True)
    gesendet_von = models.ForeignKey(
        Benutzer,
        verbose_name="gesendet von",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    dienst_auftrag = models.CharField(
        "Auftrag beim Versanddienst",
        max_length=255,
        blank=True,
        help_text="Auftragsnummer bei LetterXpress oder Message-ID der Mail.",
    )
    sendungsnummer = models.CharField("Sendungsnummer", max_length=100, blank=True)
    einlieferungsdatum = models.DateField("Einlieferungsdatum", null=True, blank=True)
    zugestellt_am = models.DateField("zugestellt am", null=True, blank=True)
    zustellnachweis = models.FileField("Zustellnachweis", upload_to=_nachweis_pfad, blank=True)
    zustellnachweis_vermerk = models.TextField(
        "Vermerk zum Zustellnachweis",
        blank=True,
        help_text="z. B. Stand der Sendungsverfolgung oder Einwurf durch Boten.",
    )

    class Meta:
        verbose_name = "Schreiben"
        verbose_name_plural = "Schreiben"
        ordering = ("-erzeugt_am", "-pk")
        indexes = (models.Index(fields=["bezug_typ", "bezug_id"]),)
        constraints = (
            # H8.3: Eine einseitige Erklärung ist ohne Zustellnachweis nicht erledigt.
            models.CheckConstraint(
                condition=~(Q(einseitige_erklaerung=True) & Q(status="zugestellt"))
                | ~Q(zustellnachweis="")
                | ~Q(zustellnachweis_vermerk=""),
                name="schreiben_einseitig_nur_mit_nachweis",
            ),
        )

    def __str__(self) -> str:
        return f"Schreiben #{self.pk} ({self.art})"

    @property
    def hat_zustellnachweis(self) -> bool:
        return bool(self.zustellnachweis) or bool(self.zustellnachweis_vermerk.strip())

    @property
    def ist_erledigt(self) -> bool:
        return self.status == self.Status.ZUGESTELLT and (
            not self.einseitige_erklaerung or self.hat_zustellnachweis
        )

    @property
    def ist_brief(self) -> bool:
        return self.versandweg in (Versandweg.BRIEF, Versandweg.EINWURF_EINSCHREIBEN)


# --- Personenfelder (CLAUDE.md, Regel 4.6) ------------------------------------------

# Vorlagen enthalten nur Platzhalter (Test: keine Namen aus dem Testhof).
personenfelder.unbedenklich(Vorlage, ["name", "art", "betreff", "text", "status", "herkunft"])
personenfelder.register(Schreiben, ["betreff", "inhalt_html", "empfaenger_email"])
personenfelder.register(Schreiben, ["zustellnachweis_vermerk"])
personenfelder.unbedenklich(
    Schreiben,
    ["art", "pdf_sha256", "versandweg", "status", "dienst_auftrag", "sendungsnummer"],
)
