"""Modelle der Dokumente: Ablage, Belege (Schritt 3a, H6.1, H6.2, H6.4, H6.8, H6.9) sowie
Vorlagen und Schreiben (Schritt 3c, H8.1 bis H8.4).

Geschrieben wird nur über ``apps.dokumente.services`` (CLAUDE.md, Regel 4.1).
"""

from __future__ import annotations

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import Q

from apps.dokumente import dokumentarten, vorlagenarten
from apps.dokumente.vorlagenarten import Versandweg
from apps.kassenbuch.models import Buchung, Kategorie
from apps.kern import personenfelder
from apps.kern.models import Benutzer, Objekt
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


# --- Ablage und Belege (Schritt 3a) --------------------------------------------------


def _dokumentart_pruefen(wert: str) -> None:
    from django.core.exceptions import ValidationError

    try:
        dokumentarten.holen(wert)
    except dokumentarten.UnbekannteDokumentart as fehler:
        raise ValidationError(f"Unbekannte Dokumentart: {wert}") from fehler


def _dokument_pfad(dokument: Dokument, dateiname: str) -> str:
    # Der Dateiname kommt aus dem Service: Art, Datum und ID, nie ein Personenname.
    return f"ablage/{dokument.datum:%Y}/{dateiname}"


def _original_pfad(dokument: Dokument, dateiname: str) -> str:
    return f"ablage/{dokument.datum:%Y}/original/{dateiname}"


class Lieferant(ProtokolliertesModell):
    """Wer eine Rechnung stellt (H6.1). Kann eine Person sein: Name, IBAN und E-Mail sind
    Personenfelder. Die E-Mail erkennt den Lieferanten im Rechnungspostfach, die IBAN die
    Abbuchung im Kassenbuch (Schritt 3b)."""

    name = models.CharField("Name", max_length=200)
    iban = models.CharField("IBAN", max_length=34, blank=True)
    email = models.EmailField("E-Mail (Absender der Rechnungen)", blank=True, db_index=True)
    aktiv = models.BooleanField("aktiv", default=True)
    angelegt_am = models.DateTimeField("angelegt am", auto_now_add=True)

    class Meta:
        verbose_name = "Lieferant"
        verbose_name_plural = "Lieferanten"
        ordering = ("name", "pk")

    def __str__(self) -> str:
        return f"Lieferant #{self.pk}"


class Dokument(ProtokolliertesModell):
    """Eine Datei in der Ablage mit Art, Datum und Löschfrist (H6.1, H6.2, H8.6).

    ``datei`` ist immer ein PDF; Fotos vom Handy werden beim Ablegen umgewandelt, das Foto
    selbst bleibt als ``original``. ``sha256`` ist die Prüfsumme der eingegangenen Datei
    (Foto oder PDF): Kommt dieselbe Datei noch einmal, ist sie eine Dublette. Der Dateiname
    besteht aus Art, Datum und ID. Ausgeliefert wird nur über Django mit Anmeldung.
    """

    class Herkunft(models.TextChoices):
        POSTFACH = "postfach", "Rechnungspostfach"
        UPLOAD = "upload", "hochgeladen"
        TESTHOF = "testhof", "Testhof"

    art = models.CharField("Art", max_length=30, validators=[_dokumentart_pruefen], db_index=True)
    datum = models.DateField("Datum des Dokuments", db_index=True)
    datei = models.FileField("PDF", upload_to=_dokument_pfad, blank=True, max_length=200)
    original = models.FileField(
        "Original (Foto)", upload_to=_original_pfad, blank=True, max_length=200
    )
    sha256 = models.CharField("Prüfsumme der eingegangenen Datei", max_length=64, unique=True)
    dateityp = models.CharField("Dateityp des Originals", max_length=100)
    herkunft = models.CharField("Herkunft", max_length=20, choices=Herkunft.choices)
    eingangskennung = models.CharField(
        "Kennung des Eingangs",
        max_length=255,
        blank=True,
        help_text="Message-ID der Mail aus dem Rechnungspostfach.",
    )
    loeschfrist = models.DateField(
        "aufbewahren bis", help_text="Aus der Dokumentart (DOKUMENTE['LOESCHFRIST_JAHRE'])."
    )
    bemerkung = models.TextField("Bemerkung", blank=True)
    eingegangen_am = models.DateTimeField("eingegangen am", auto_now_add=True)

    class Meta:
        verbose_name = "Dokument"
        verbose_name_plural = "Dokumente"
        ordering = ("-datum", "-pk")

    def __str__(self) -> str:
        return f"Dokument #{self.pk} ({self.art})"

    @property
    def dokumentart(self) -> dokumentarten.Dokumentart:
        return dokumentarten.holen(self.art)

    @property
    def dateiname(self) -> str:
        name: str = self.datei.name or ""
        return name.rsplit("/", 1)[-1]


class Dokumentbezug(ProtokolliertesModell):
    """Woran ein Dokument hängt: generisch an jedem Modell (Buchung, Lieferant, Objekt, ab
    Schritt 4 Einheit, Partei, Vertrag, später Vorgang). Ein Dokument kann an mehreren
    Stellen hängen, an jeder höchstens einmal."""

    dokument = models.ForeignKey(
        Dokument, verbose_name="Dokument", on_delete=models.PROTECT, related_name="bezuege"
    )
    bezug_typ = models.ForeignKey(
        ContentType, verbose_name="Bezug (Typ)", on_delete=models.PROTECT, related_name="+"
    )
    bezug_id = models.PositiveBigIntegerField("Bezug (ID)")
    bezug = GenericForeignKey("bezug_typ", "bezug_id")
    angelegt_am = models.DateTimeField("angelegt am", auto_now_add=True)

    class Meta:
        verbose_name = "Dokumentbezug"
        verbose_name_plural = "Dokumentbezüge"
        ordering = ("dokument", "pk")
        indexes = (models.Index(fields=["bezug_typ", "bezug_id"]),)
        constraints = (
            models.UniqueConstraint(
                fields=["dokument", "bezug_typ", "bezug_id"], name="dokumentbezug_einmal"
            ),
        )

    def __str__(self) -> str:
        return f"Dokumentbezug #{self.pk}"


class Beleg(ProtokolliertesModell):
    """Ein Beleg zu einer Ausgabe (H6.1, H6.2, H6.4): Lieferant, Betrag, Objekt, Kostenart,
    Leistungsjahr, umlagefähiger Anteil und die Buchung im Kassenbuch.

    Die Kostenart ist die Kategorie des Kassenbuchs: Danach rechnen Monatsbericht und
    Nebenkosten, und so passen die Summen der Belegmappe zum Kassenbuch. Eine Sammelrechnung
    sind mehrere Belege auf demselben Dokument, einer je Objekt oder Kostenart.

    Ein Beleg entsteht „offen“ (aus dem Postfach oder vom Handy). Mit allen Feldern wird er
    „bestätigt“. Kein Beleg (etwa Werbung) wird „verworfen“, nie gelöscht.
    """

    class Status(models.TextChoices):
        OFFEN = "offen", "offen"
        BESTAETIGT = "bestaetigt", "bestätigt"
        VERWORFEN = "verworfen", "verworfen"

    dokument = models.ForeignKey(
        Dokument, verbose_name="Dokument", on_delete=models.PROTECT, related_name="belege"
    )
    lieferant = models.ForeignKey(
        Lieferant,
        verbose_name="Lieferant",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="belege",
    )
    betrag = models.DecimalField("Betrag", max_digits=12, decimal_places=2, null=True, blank=True)
    objekt = models.ForeignKey(
        Objekt,
        verbose_name="Objekt",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="belege",
    )
    kategorie = models.ForeignKey(
        Kategorie,
        verbose_name="Kostenart",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="belege",
    )
    leistungsjahr = models.PositiveSmallIntegerField("Leistungsjahr", null=True, blank=True)
    umlagefaehiger_anteil = models.DecimalField(
        "umlagefähiger Anteil (%)", max_digits=5, decimal_places=2, null=True, blank=True
    )
    buchung = models.ForeignKey(
        Buchung,
        verbose_name="Buchung",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="belege",
    )
    status = models.CharField(
        "Status", max_length=20, choices=Status.choices, default=Status.OFFEN, db_index=True
    )
    bestaetigt_am = models.DateTimeField("bestätigt am", null=True, blank=True)
    angelegt_am = models.DateTimeField("angelegt am", auto_now_add=True)

    class Meta:
        verbose_name = "Beleg"
        verbose_name_plural = "Belege"
        ordering = ("-dokument__datum", "-pk")
        constraints = (
            models.CheckConstraint(
                condition=Q(betrag__isnull=True) | Q(betrag__gt=0),
                name="beleg_betrag_positiv",
            ),
            models.CheckConstraint(
                condition=Q(umlagefaehiger_anteil__isnull=True)
                | Q(umlagefaehiger_anteil__gte=0, umlagefaehiger_anteil__lte=100),
                name="beleg_umlage_prozent",
            ),
            # Bestätigt heißt: alle Felder sind da (Abnahme Schritt 3).
            models.CheckConstraint(
                condition=~Q(status="bestaetigt")
                | (
                    Q(lieferant__isnull=False)
                    & Q(betrag__isnull=False)
                    & Q(objekt__isnull=False)
                    & Q(kategorie__isnull=False)
                    & Q(leistungsjahr__isnull=False)
                    & Q(umlagefaehiger_anteil__isnull=False)
                ),
                name="beleg_bestaetigt_vollstaendig",
            ),
        )

    def __str__(self) -> str:
        return f"Beleg #{self.pk}"

    @property
    def ist_offen(self) -> bool:
        return self.status == self.Status.OFFEN


class Eingangsrechnung(ProtokolliertesModell):
    """Ein offener Posten aus einem Beleg mit Fälligkeit (H6.1, Standardablauf 6).

    Entsteht beim Bestätigen eines Belegs mit Fälligkeit, zusammen mit dem Arbeitsposten
    „Rechnung zahlen“ und einer Frist (Zahlungsziel, drei Tage Vorlauf). Bezahlt ist sie, wenn
    der Lauf ``kassenbuch_einordnen`` die Abbuchung erkennt (oder Max sie zuordnet):
    ``bezahlt_durch`` zeigt dann auf die Buchung. Bei einer Sammelrechnung zeigt ``beleg`` auf
    den ersten Teil; ``betrag`` ist der Betrag der ganzen Rechnung.
    """

    class Status(models.TextChoices):
        OFFEN = "offen", "offen"
        BEZAHLT = "bezahlt", "bezahlt"
        STRITTIG = "strittig", "strittig"

    beleg = models.OneToOneField(
        Beleg, verbose_name="Beleg", on_delete=models.PROTECT, related_name="eingangsrechnung"
    )
    betrag = models.DecimalField("Betrag", max_digits=12, decimal_places=2)
    rechnungsnummer = models.CharField("Rechnungsnummer", max_length=100, blank=True)
    faellig_am = models.DateField("fällig am", db_index=True)
    status = models.CharField(
        "Status", max_length=20, choices=Status.choices, default=Status.OFFEN, db_index=True
    )
    bezahlt_durch = models.ForeignKey(
        Buchung,
        verbose_name="bezahlt durch Buchung",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="eingangsrechnungen",
    )
    bezahlt_am = models.DateField("bezahlt am", null=True, blank=True)
    ueberfaellig_seit = models.DateField(
        "als überfällig markiert am",
        null=True,
        blank=True,
        help_text="Setzt der Lauf fristen am Tag nach der Fälligkeit.",
    )
    angelegt_am = models.DateTimeField("angelegt am", auto_now_add=True)

    class Meta:
        verbose_name = "Eingangsrechnung"
        verbose_name_plural = "Eingangsrechnungen"
        ordering = ("faellig_am", "pk")
        constraints = (
            models.CheckConstraint(
                condition=Q(betrag__gt=0), name="eingangsrechnung_betrag_positiv"
            ),
            models.CheckConstraint(
                condition=~Q(status="bezahlt") | Q(bezahlt_durch__isnull=False),
                name="eingangsrechnung_bezahlt_mit_buchung",
            ),
        )

    def __str__(self) -> str:
        return f"Eingangsrechnung #{self.pk}"

    @property
    def ist_offen(self) -> bool:
        return self.status == self.Status.OFFEN


# --- Personenfelder (CLAUDE.md, Regel 4.6) ------------------------------------------

# Vorlagen enthalten nur Platzhalter (Test: keine Namen aus dem Testhof).
personenfelder.unbedenklich(Vorlage, ["name", "art", "betreff", "text", "status", "herkunft"])
personenfelder.register(Schreiben, ["betreff", "inhalt_html", "empfaenger_email"])
personenfelder.register(Schreiben, ["zustellnachweis_vermerk"])
personenfelder.unbedenklich(
    Schreiben,
    ["art", "pdf_sha256", "versandweg", "status", "dienst_auftrag", "sendungsnummer"],
)

personenfelder.register(Lieferant, ["name", "iban", "email"])
personenfelder.unbedenklich(Dokument, ["art", "datei", "original", "sha256", "dateityp"])
personenfelder.unbedenklich(Dokument, ["herkunft"])
personenfelder.register(Dokument, ["eingangskennung", "bemerkung"])
personenfelder.unbedenklich(Beleg, ["status"])
personenfelder.register(Eingangsrechnung, ["rechnungsnummer"])
personenfelder.unbedenklich(Eingangsrechnung, ["status"])
