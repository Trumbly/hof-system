"""Modelle der Stammdaten: Einheit, Partei, Vertrag und was daran hängt (Schritt 4).

Geschrieben wird nur über ``apps.stammdaten.services`` (CLAUDE.md, Regel 4.1).

Was sich mit der Zeit ändert, steht als Zeitreihe mit ``gueltig_ab`` und ``gueltig_bis``
(CLAUDE.md, Regel 4.3): Status und Zielmiete der Einheit, Kondition des Vertrags (Miete,
Pauschale, Steuersatz, Zahlweise). Alte Stände bleiben stehen. ``gueltig_bis`` leer heißt
„gilt bis auf Weiteres“.

Wohnraum führt Verwaltio. Eine Einheit vom Typ Wohnung steht hier nur mit NW-ID und
Objekt, ohne Vertrag, Status und Zielmiete.
"""

from __future__ import annotations

import datetime
import uuid
from pathlib import PurePath
from typing import Any

from django.db import models
from django.db.models import Q

from apps.kern import personenfelder
from apps.kern.models import Mandant, Objekt
from apps.kern.protokoll import ProtokolliertesModell

HINWEIS_VERWALTIO = "geführt in Verwaltio"


def _dateiname(ordner: str, endung_von: str) -> str:
    """Neutraler Dateiname ohne Teile des Originalnamens (der kann Namen enthalten)."""
    endung = PurePath(endung_von).suffix.lower()[:10]
    heute = datetime.date.today()  # zeit: echt (Ablageordner nach Speicherdatum)
    return f"stammdaten/{ordner}/{heute:%Y/%m}/{uuid.uuid4().hex}{endung}"


def _scan_pfad(instanz: Any, dateiname: str) -> str:
    return _dateiname("vertraege", dateiname)


def _foto_pfad(instanz: Any, dateiname: str) -> str:
    return _dateiname("uebergaben", dateiname)


class Zeitraum(models.Model):
    """Gemeinsame Felder eines Stands mit Gültigkeit."""

    gueltig_ab = models.DateField("gültig ab")
    gueltig_bis = models.DateField("gültig bis", null=True, blank=True)

    class Meta:
        abstract = True

    def gilt_am(self, datum: datetime.date) -> bool:
        return self.gueltig_ab <= datum and (self.gueltig_bis is None or datum <= self.gueltig_bis)


# --- Einheit -------------------------------------------------------------------------


class Einheit(ProtokolliertesModell):
    """Eine vermietbare Einheit mit NW-ID (H1.1). Die NW-ID vergibt das Hof-System.

    Format der NW-ID: ``NW-<Typ-Kürzel>-<vierstellige Nummer>``, z. B. ``NW-G-0001``
    (siehe ``apps.stammdaten.services.naechste_nw_id``).
    """

    class Typ(models.TextChoices):
        GARAGE = "garage", "Garage"
        STELLPLATZ = "stellplatz", "Stellplatz"
        LAGER = "lager", "Lager"
        HALLE = "halle", "Halle"
        GARTEN = "garten", "Garten"
        AUSSENFLAECHE = "aussenflaeche", "Außenfläche"
        WOHNUNG = "wohnung", "Wohnung"
        SONSTIGES = "sonstiges", "Sonstiges"

    class Vermarktung(models.TextChoices):
        KEINE = "keine", "nicht vermarktet"
        WEBSITE = "website", "Website"
        DIREKT = "direkt", "direkt (Aushang, Empfehlung)"

    nw_id = models.CharField("NW-ID", max_length=30, unique=True)
    typ = models.CharField("Typ", max_length=20, choices=Typ.choices)
    objekt = models.ForeignKey(
        Objekt, verbose_name="Objekt", on_delete=models.PROTECT, related_name="einheiten"
    )
    bezeichnung = models.CharField(
        "Bezeichnung",
        max_length=200,
        blank=True,
        help_text="Lage oder Nummer vor Ort, z. B. „Garage 3, Hofseite“. Keine Namen.",
    )
    flaeche_qm = models.DecimalField(
        "Fläche (m²)", max_digits=10, decimal_places=2, null=True, blank=True
    )
    vermarktung = models.CharField(
        "Vermarktung", max_length=20, choices=Vermarktung.choices, default=Vermarktung.KEINE
    )
    website_slug = models.CharField("Website-Slug", max_length=100, blank=True)
    notiz = models.TextField("Notiz", blank=True)
    aufgeloest_am = models.DateField("aufgelöst am", null=True, blank=True)
    angelegt_am = models.DateTimeField("angelegt am", auto_now_add=True)

    class Meta:
        verbose_name = "Einheit"
        verbose_name_plural = "Einheiten"
        ordering = ("nw_id",)

    def __str__(self) -> str:
        return self.nw_id

    @property
    def ist_wohnraum(self) -> bool:
        return self.typ == self.Typ.WOHNUNG

    @property
    def hinweis(self) -> str:
        return HINWEIS_VERWALTIO if self.ist_wohnraum else ""

    def status_am(self, datum: datetime.date) -> EinheitStatus | None:
        return (
            self.statusstaende.filter(gueltig_ab__lte=datum)
            .filter(Q(gueltig_bis__isnull=True) | Q(gueltig_bis__gte=datum))
            .first()
        )

    def zielmiete_am(self, datum: datetime.date) -> Zielmiete | None:
        return (
            self.zielmieten.filter(gueltig_ab__lte=datum)
            .filter(Q(gueltig_bis__isnull=True) | Q(gueltig_bis__gte=datum))
            .first()
        )


class EinheitStatus(Zeitraum, ProtokolliertesModell):
    """Status einer Einheit mit Gültigkeit (Zeitreihe, nichts wird überschrieben)."""

    class Status(models.TextChoices):
        FREI = "frei", "frei"
        RESERVIERT = "reserviert", "reserviert"
        VERMIETET = "vermietet", "vermietet"
        IN_VORBEREITUNG = "in_vorbereitung", "in Vorbereitung"
        GESPERRT = "gesperrt", "gesperrt"
        AUFGELOEST = "aufgeloest", "aufgelöst"

    einheit = models.ForeignKey(
        Einheit, verbose_name="Einheit", on_delete=models.PROTECT, related_name="statusstaende"
    )
    status = models.CharField("Status", max_length=20, choices=Status.choices)
    grund = models.CharField(
        "Grund", max_length=200, blank=True, help_text="Anlass, z. B. „Rückgabe“."
    )

    class Meta:
        verbose_name = "Status einer Einheit"
        verbose_name_plural = "Status der Einheiten"
        ordering = ("einheit", "gueltig_ab")
        constraints = (
            models.UniqueConstraint(
                fields=("einheit", "gueltig_ab"), name="stammdaten_status_einmal_je_tag"
            ),
            models.CheckConstraint(
                condition=Q(gueltig_bis__isnull=True) | Q(gueltig_bis__gte=models.F("gueltig_ab")),
                name="stammdaten_status_zeitraum",
            ),
        )

    def __str__(self) -> str:
        return f"Status #{self.pk} {self.status}"


class Zielmiete(Zeitraum, ProtokolliertesModell):
    """Zielmiete einer Einheit mit Datum und Begründung (H2.1), Grundlage des Leerstands."""

    einheit = models.ForeignKey(
        Einheit, verbose_name="Einheit", on_delete=models.PROTECT, related_name="zielmieten"
    )
    betrag = models.DecimalField("Zielmiete je Monat", max_digits=12, decimal_places=2)
    begruendung = models.TextField("Begründung", blank=True, help_text="z. B. Marktvergleich.")

    class Meta:
        verbose_name = "Zielmiete"
        verbose_name_plural = "Zielmieten"
        ordering = ("einheit", "gueltig_ab")
        constraints = (
            models.UniqueConstraint(
                fields=("einheit", "gueltig_ab"), name="stammdaten_zielmiete_einmal_je_tag"
            ),
            models.CheckConstraint(
                condition=Q(gueltig_bis__isnull=True) | Q(gueltig_bis__gte=models.F("gueltig_ab")),
                name="stammdaten_zielmiete_zeitraum",
            ),
            models.CheckConstraint(condition=Q(betrag__gte=0), name="stammdaten_zielmiete_positiv"),
        )

    def __str__(self) -> str:
        return f"Zielmiete #{self.pk}"


# --- Partei --------------------------------------------------------------------------


class Partei(ProtokolliertesModell):
    """Mieter, Zahler oder Bürge außerhalb von Wohnraum (H1.2). Person oder Firma."""

    class Art(models.TextChoices):
        PERSON = "person", "Person"
        FIRMA = "firma", "Firma"

    class Anrede(models.TextChoices):
        """Wie ein Schreiben beginnt (Review 3, Punkt 4; ``schreiben.briefanrede``)."""

        FRAU = "frau", "Frau"
        HERR = "herr", "Herr"
        NEUTRAL = "neutral", "neutral"
        FIRMA = "firma", "Firma"

        @classmethod
        def aus_text(cls, text: str, *, firma: bool = False) -> str:
            """Anrede aus einem Import („Frau“, „Herr“, „Firma“ …); sonst neutral, bei einer
            Firma „Firma“."""
            wert = text.strip().casefold().rstrip(".")
            if wert in ("frau", "fr"):
                return str(cls.FRAU.value)
            if wert in ("herr", "herrn", "hr"):
                return str(cls.HERR.value)
            if wert == "firma" or firma:
                return str(cls.FIRMA.value)
            return str(cls.NEUTRAL.value)

    art = models.CharField("Art", max_length=10, choices=Art.choices, default=Art.PERSON)
    anrede = models.CharField(
        "Anrede",
        max_length=10,
        choices=Anrede.choices,
        default=Anrede.NEUTRAL,
        help_text="Frau: „Sehr geehrte Frau Nachname“, Herr: „Sehr geehrter Herr Nachname“, "
        "neutral: „Guten Tag Vorname Nachname“, Firma: „Sehr geehrte Damen und Herren“.",
    )
    vorname = models.CharField("Vorname", max_length=100, blank=True)
    nachname = models.CharField("Nachname", max_length=100, blank=True)
    firma = models.CharField("Firma", max_length=200, blank=True)
    adresszusatz = models.CharField("Adresszusatz", max_length=200, blank=True)
    strasse = models.CharField("Straße und Hausnummer", max_length=200, blank=True)
    plz = models.CharField("PLZ", max_length=10, blank=True)
    ort = models.CharField("Ort", max_length=100, blank=True)
    anschrift_folgt = models.BooleanField(
        "Anschrift folgt",
        default=False,
        help_text="Die Anschrift fehlt noch. Sie steht im Datenstand, und Briefe an diese "
        "Partei lassen sich erst senden, wenn sie ergänzt ist.",
    )
    notiz = models.TextField("Notiz", blank=True)
    herkunft = models.CharField(
        "Herkunft",
        max_length=100,
        blank=True,
        help_text="Schlüssel aus einem Import, z. B. „hellohousing:M-17“.",
    )
    angelegt_am = models.DateTimeField("angelegt am", auto_now_add=True)

    class Meta:
        verbose_name = "Partei"
        verbose_name_plural = "Parteien"
        ordering = ("pk",)
        constraints = (
            models.UniqueConstraint(
                fields=("herkunft",),
                condition=~Q(herkunft=""),
                name="stammdaten_partei_herkunft_einmal",
            ),
        )

    def __str__(self) -> str:
        # Nur die ID: __str__ landet in Logs und Fehlermeldungen.
        return f"Partei #{self.pk}"

    @property
    def anzeigename(self) -> str:
        """Name für die Oberfläche und Schreiben. Nie in Logs oder Berichte."""
        if self.art == self.Art.FIRMA and self.firma:
            return self.firma
        return " ".join(t for t in (self.vorname, self.nachname) if t) or self.firma

    @property
    def hat_anschrift(self) -> bool:
        return bool(self.strasse and self.plz and self.ort)

    @property
    def anschrift_offen(self) -> bool:
        """„Anschrift folgt“ ist gesetzt und die Anschrift noch nicht ergänzt."""
        return self.anschrift_folgt and not self.hat_anschrift


class Kontaktweg(ProtokolliertesModell):
    """Mail oder Telefon einer Partei."""

    class Art(models.TextChoices):
        EMAIL = "email", "E-Mail"
        TELEFON = "telefon", "Telefon"
        MOBIL = "mobil", "Mobil"

    partei = models.ForeignKey(
        Partei, verbose_name="Partei", on_delete=models.CASCADE, related_name="kontaktwege"
    )
    art = models.CharField("Art", max_length=10, choices=Art.choices)
    wert = models.CharField("Adresse oder Nummer", max_length=200)
    bevorzugt = models.BooleanField("bevorzugt", default=False)

    class Meta:
        verbose_name = "Kontaktweg"
        verbose_name_plural = "Kontaktwege"
        ordering = ("partei", "-bevorzugt", "pk")

    def __str__(self) -> str:
        return f"Kontaktweg #{self.pk} ({self.art})"


# --- Vertrag -------------------------------------------------------------------------


class VertragQuerySet(models.QuerySet["Vertrag"]):
    """Die eine Zählung der Verträge (Review 2, A11). Jede Seite zählt hierüber.

    - ``laufend()``: aktiv oder gekündigt, also unterschrieben und noch nicht zurückgegeben.
      Das ist „die“ Zahl der Verträge (Stammdaten, Vertragsliste, Cockpit).
    - ``bindend()``: dazu die Entwürfe; sie binden die Einheit schon (Katalog, Leerstand).
    - ``mit_soll()``: laufend und beendet; nur diese haben Sollposten.
    """

    def laufend(self) -> VertragQuerySet:
        return self.filter(status__in=LAUFEND)

    def bindend(self) -> VertragQuerySet:
        return self.filter(status__in=BINDEND)

    def mit_soll(self) -> VertragQuerySet:
        return self.filter(status__in=MIT_SOLL)

    def zaehlung(self) -> dict[str, int]:
        """Anzahl je Gruppe: laufend, entwurf, beendet, alle."""
        je_status = dict(
            self.order_by()
            .values_list("status")
            .annotate(n=models.Count("pk"))
            .values_list("status", "n")
        )
        return {
            "laufend": sum(je_status.get(s, 0) for s in LAUFEND),
            "entwurf": je_status.get("entwurf", 0),
            "beendet": je_status.get("beendet", 0),
            "alle": sum(je_status.values()),
        }


class Vertrag(ProtokolliertesModell):
    """Mietvertrag außerhalb von Wohnraum (H1.3) mit Mandant, Einheiten und Parteien."""

    class Art(models.TextChoices):
        G = "G", "Garage"
        S = "S", "Stellplatz"
        L = "L", "Lager"
        H = "H", "Halle"
        GA = "GA", "Garten"
        A = "A", "Außenfläche"
        X = "X", "sonstige"

    class Status(models.TextChoices):
        ENTWURF = "entwurf", "Entwurf"
        AKTIV = "aktiv", "aktiv"
        GEKUENDIGT = "gekuendigt", "gekündigt"
        BEENDET = "beendet", "beendet"

    nummer = models.CharField("Nummer", max_length=20, unique=True)
    vertragsart = models.CharField("Vertragsart", max_length=2, choices=Art.choices)
    mandant = models.ForeignKey(
        Mandant, verbose_name="Mandant", on_delete=models.PROTECT, related_name="vertraege"
    )
    einheiten: models.ManyToManyField[Einheit, Vertragseinheit] = models.ManyToManyField(
        Einheit, verbose_name="Einheiten", through="Vertragseinheit", related_name="vertraege"
    )
    beginn = models.DateField("Beginn")
    ende = models.DateField("Ende", null=True, blank=True)
    kuendigungsregel = models.CharField(
        "Kündigungsfrist",
        max_length=50,
        blank=True,
        help_text="Schlüssel einer Regel aus konfiguration.toml; leer heißt „noch nicht geprüft“.",
    )
    status = models.CharField(
        "Status", max_length=20, choices=Status.choices, default=Status.ENTWURF, db_index=True
    )
    unterschrieben_am = models.DateField("unterschrieben am", null=True, blank=True)
    unterschriebener_scan = models.FileField(
        "unterschriebener Scan", upload_to=_scan_pfad, blank=True
    )
    sonderabreden = models.TextField("Sonderabreden", blank=True)
    herkunft = models.CharField(
        "Herkunft",
        max_length=100,
        blank=True,
        help_text="Schlüssel aus einem Import, z. B. „hellohousing:MV-17“.",
    )
    angelegt_am = models.DateTimeField("angelegt am", auto_now_add=True)

    objects = VertragQuerySet.as_manager()

    class Meta:
        verbose_name = "Vertrag"
        verbose_name_plural = "Verträge"
        ordering = ("nummer",)
        constraints = (
            models.UniqueConstraint(
                fields=("herkunft",),
                condition=~Q(herkunft=""),
                name="stammdaten_vertrag_herkunft_einmal",
            ),
            models.CheckConstraint(
                condition=Q(ende__isnull=True) | Q(ende__gte=models.F("beginn")),
                name="stammdaten_vertrag_zeitraum",
            ),
        )

    def __str__(self) -> str:
        return self.nummer

    def kondition_am(self, datum: datetime.date) -> Kondition | None:
        return (
            self.konditionen.filter(gueltig_ab__lte=datum)
            .filter(Q(gueltig_bis__isnull=True) | Q(gueltig_bis__gte=datum))
            .first()
        )

    def parteien_mit_rolle(self, rolle: str) -> list[Partei]:
        return [
            vp.partei for vp in self.vertragsparteien.filter(rolle=rolle).select_related("partei")
        ]


#: Status-Gruppen der Verträge (``VertragQuerySet``); die einzige Stelle dafür.
LAUFEND = (Vertrag.Status.AKTIV, Vertrag.Status.GEKUENDIGT)
BINDEND = (Vertrag.Status.ENTWURF, *LAUFEND)
MIT_SOLL = (*LAUFEND, Vertrag.Status.BEENDET)


class Vertragseinheit(ProtokolliertesModell):
    """Welche Einheiten ein Vertrag umfasst."""

    vertrag = models.ForeignKey(
        Vertrag, verbose_name="Vertrag", on_delete=models.CASCADE, related_name="vertragseinheiten"
    )
    einheit = models.ForeignKey(
        Einheit, verbose_name="Einheit", on_delete=models.PROTECT, related_name="vertragseinheiten"
    )

    class Meta:
        verbose_name = "Einheit eines Vertrags"
        verbose_name_plural = "Einheiten der Verträge"
        constraints = (
            models.UniqueConstraint(
                fields=("vertrag", "einheit"), name="stammdaten_vertragseinheit_einmal"
            ),
        )

    def __str__(self) -> str:
        return f"Vertragseinheit #{self.pk}"


class Vertragspartei(ProtokolliertesModell):
    """Wer an einem Vertrag beteiligt ist: Mieter, Zahler oder Bürge."""

    class Rolle(models.TextChoices):
        MIETER = "mieter", "Mieter"
        ZAHLER = "zahler", "Zahler"
        BUERGE = "buerge", "Bürge"

    vertrag = models.ForeignKey(
        Vertrag, verbose_name="Vertrag", on_delete=models.CASCADE, related_name="vertragsparteien"
    )
    partei = models.ForeignKey(
        Partei, verbose_name="Partei", on_delete=models.PROTECT, related_name="vertragsparteien"
    )
    rolle = models.CharField("Rolle", max_length=10, choices=Rolle.choices)

    class Meta:
        verbose_name = "Vertragspartei"
        verbose_name_plural = "Vertragsparteien"
        ordering = ("vertrag", "rolle", "pk")
        constraints = (
            models.UniqueConstraint(
                fields=("vertrag", "partei", "rolle"), name="stammdaten_vertragspartei_einmal"
            ),
        )

    def __str__(self) -> str:
        return f"Vertragspartei #{self.pk} ({self.rolle})"


class Kondition(Zeitraum, ProtokolliertesModell):
    """Miete, Pauschale, Steuersatz und Zahlweise mit Gültigkeit. Eine Staffel sind mehrere
    Zeilen mit aufeinanderfolgenden Zeiträumen."""

    class Zahlweise(models.TextChoices):
        UNBEKANNT = "", "noch nicht erfasst"
        MONATLICH = "monatlich", "monatlich"
        VIERTELJAEHRLICH = "vierteljaehrlich", "vierteljährlich"
        HALBJAEHRLICH = "halbjaehrlich", "halbjährlich"
        JAEHRLICH = "jaehrlich", "jährlich"

    vertrag = models.ForeignKey(
        Vertrag, verbose_name="Vertrag", on_delete=models.PROTECT, related_name="konditionen"
    )
    miete = models.DecimalField("Miete je Monat (netto)", max_digits=12, decimal_places=2)
    pauschale = models.DecimalField(
        "Pauschale je Monat", max_digits=12, decimal_places=2, default=0
    )
    steuersatz = models.DecimalField("Umsatzsteuer (%)", max_digits=5, decimal_places=2, default=0)
    zahlweise = models.CharField(
        "Zahlweise", max_length=20, choices=Zahlweise.choices, blank=True, default=""
    )
    grund = models.CharField("Grund", max_length=200, blank=True, help_text="z. B. „Staffel 2“.")

    class Meta:
        verbose_name = "Kondition"
        verbose_name_plural = "Konditionen"
        ordering = ("vertrag", "gueltig_ab")
        constraints = (
            models.UniqueConstraint(
                fields=("vertrag", "gueltig_ab"), name="stammdaten_kondition_einmal_je_tag"
            ),
            models.CheckConstraint(
                condition=Q(gueltig_bis__isnull=True) | Q(gueltig_bis__gte=models.F("gueltig_ab")),
                name="stammdaten_kondition_zeitraum",
            ),
            models.CheckConstraint(
                condition=Q(miete__gte=0) & Q(pauschale__gte=0) & Q(steuersatz__gte=0),
                name="stammdaten_kondition_positiv",
            ),
        )

    def __str__(self) -> str:
        return f"Kondition #{self.pk}"


class Kaution(ProtokolliertesModell):
    """Kaution als eigener Posten am Vertrag (H2.10). Betrag 0 heißt: keine vereinbart."""

    class Art(models.TextChoices):
        UNBEKANNT = "", "noch nicht erfasst"
        UEBERWEISUNG = "ueberweisung", "Überweisung"
        BAR = "bar", "bar"
        BUERGSCHAFT = "buergschaft", "Bürgschaft"
        SPARBUCH = "sparbuch", "verpfändetes Sparbuch"
        SONSTIGE = "sonstige", "sonstige"

    vertrag = models.OneToOneField(
        Vertrag, verbose_name="Vertrag", on_delete=models.PROTECT, related_name="kaution"
    )
    betrag = models.DecimalField("Betrag", max_digits=12, decimal_places=2, null=True, blank=True)
    art = models.CharField(
        "Art", max_length=20, choices=Art.choices, blank=True, default=Art.UNBEKANNT
    )
    eingang_am = models.DateField("eingegangen am", null=True, blank=True)
    konto = models.CharField(
        "Konto",
        max_length=100,
        blank=True,
        help_text="Bezeichnung des Kontos, z. B. „Vivid Kautionen“. Keine IBAN.",
    )
    abrechnung_faellig_am = models.DateField("Abrechnung fällig am", null=True, blank=True)
    abgerechnet_am = models.DateField("abgerechnet am", null=True, blank=True)
    einbehalt = models.DecimalField(
        "Einbehalt", max_digits=12, decimal_places=2, null=True, blank=True
    )
    ausgezahlt = models.DecimalField(
        "ausgezahlt", max_digits=12, decimal_places=2, null=True, blank=True
    )
    abrechnung_notiz = models.TextField("Notiz zur Abrechnung", blank=True)

    class Meta:
        verbose_name = "Kaution"
        verbose_name_plural = "Kautionen"
        ordering = ("vertrag",)
        constraints = (
            models.CheckConstraint(
                condition=Q(betrag__isnull=True) | Q(betrag__gte=0),
                name="stammdaten_kaution_positiv",
            ),
        )

    def __str__(self) -> str:
        return f"Kaution #{self.pk}"


# --- Schlüssel -----------------------------------------------------------------------


class Schluessel(ProtokolliertesModell):
    """Schlüssel, Handsender oder Zugänge je Einheit und wer sie hat (H1.5)."""

    class Art(models.TextChoices):
        SCHLUESSEL = "schluessel", "Schlüssel"
        HANDSENDER = "handsender", "Handsender"
        TRANSPONDER = "transponder", "Transponder"
        SONSTIGES = "sonstiges", "Sonstiges"

    class Inhaber(models.TextChoices):
        UNBEKANNT = "unbekannt", "unbekannt"
        HOF = "hof", "Hof"
        PARTEI = "partei", "Partei"
        SONSTIGE = "sonstige", "sonstige"

    einheit = models.ForeignKey(
        Einheit, verbose_name="Einheit", on_delete=models.PROTECT, related_name="schluessel"
    )
    art = models.CharField("Art", max_length=20, choices=Art.choices, default=Art.SCHLUESSEL)
    bezeichnung = models.CharField(
        "Bezeichnung", max_length=100, blank=True, help_text="z. B. „Tor“."
    )
    anzahl = models.PositiveSmallIntegerField("Anzahl", default=1)
    inhaber = models.CharField(
        "bei wem", max_length=20, choices=Inhaber.choices, default=Inhaber.UNBEKANNT
    )
    partei = models.ForeignKey(
        Partei,
        verbose_name="Partei",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="schluessel",
    )
    inhaber_text = models.CharField("bei wem (sonstige)", max_length=200, blank=True)

    class Meta:
        verbose_name = "Schlüssel"
        verbose_name_plural = "Schlüssel"
        ordering = ("einheit", "art", "pk")
        constraints = (
            models.CheckConstraint(
                condition=~Q(inhaber="partei") | Q(partei__isnull=False),
                name="stammdaten_schluessel_partei_bei_partei",
            ),
        )

    def __str__(self) -> str:
        return f"Schlüssel #{self.pk}"


# --- Checklisten ---------------------------------------------------------------------


class Checkliste(ProtokolliertesModell):
    """Checkliste einer Phase der SOP an einer Einheit oder einem Vertrag (H2.1)."""

    class Phase(models.TextChoices):
        VORBEREITUNG = "vorbereitung", "Vorbereitung"
        VERTRAG = "vertrag", "Vertrag"
        UEBERGABE = "uebergabe", "Übergabe"
        RUECKGABE = "rueckgabe", "Rückgabe"

    phase = models.CharField("Phase", max_length=20, choices=Phase.choices)
    titel = models.CharField("Titel", max_length=200)
    einheit = models.ForeignKey(
        Einheit,
        verbose_name="Einheit",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="checklisten",
    )
    vertrag = models.ForeignKey(
        Vertrag,
        verbose_name="Vertrag",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="checklisten",
    )
    angelegt_am = models.DateTimeField("angelegt am", auto_now_add=True)
    abgeschlossen_am = models.DateField("abgeschlossen am", null=True, blank=True)

    class Meta:
        verbose_name = "Checkliste"
        verbose_name_plural = "Checklisten"
        ordering = ("pk",)
        constraints = (
            models.CheckConstraint(
                condition=Q(einheit__isnull=False) | Q(vertrag__isnull=False),
                name="stammdaten_checkliste_mit_bezug",
            ),
        )

    def __str__(self) -> str:
        return f"Checkliste #{self.pk} {self.phase}"


class Checkpunkt(ProtokolliertesModell):
    checkliste = models.ForeignKey(
        Checkliste, verbose_name="Checkliste", on_delete=models.CASCADE, related_name="punkte"
    )
    reihenfolge = models.PositiveSmallIntegerField("Reihenfolge")
    text = models.CharField("Punkt", max_length=300)
    erledigt_am = models.DateField("erledigt am", null=True, blank=True)
    notiz = models.TextField("Notiz", blank=True)

    class Meta:
        verbose_name = "Checkpunkt"
        verbose_name_plural = "Checkpunkte"
        ordering = ("checkliste", "reihenfolge")

    def __str__(self) -> str:
        return f"Checkpunkt #{self.pk}"


# --- Übergabe und Rückgabe -----------------------------------------------------------


class Uebergabe(ProtokolliertesModell):
    """Übergabe oder Rückgabe vor Ort (H2.4, H2.9, O1), erfasst am Handy in Schritten.

    Die Zählerstände stehen seit Schritt 8 als ``zaehler.Zaehlerstand`` mit Bezug auf die
    Übergabe (``uebergabe.zaehlerstaende``), erfasst über ``zaehler.services``.
    ``schluessel`` hält während der Erfassung die gewählten Schlüssel als
    ``[{"schluessel": <id>, "anzahl": 2}, …]``; mit dem Abschluss wird daraus das Protokoll
    ``[{"einheit": "NW-G-0001", "art": "schluessel", "bezeichnung": "", "anzahl": 2}, …]``
    (bei der Rückgabe mit ``"fehlend"``), weil sich IDs beim Teilen und Zusammenlegen ändern.
    """

    class Art(models.TextChoices):
        UEBERGABE = "uebergabe", "Übergabe"
        RUECKGABE = "rueckgabe", "Rückgabe"

    class Status(models.TextChoices):
        BEGONNEN = "begonnen", "begonnen"
        ABGESCHLOSSEN = "abgeschlossen", "abgeschlossen"

    art = models.CharField("Art", max_length=20, choices=Art.choices)
    vertrag = models.ForeignKey(
        Vertrag, verbose_name="Vertrag", on_delete=models.PROTECT, related_name="uebergaben"
    )
    datum = models.DateField("Datum")
    status = models.CharField(
        "Status", max_length=20, choices=Status.choices, default=Status.BEGONNEN
    )
    schluessel = models.JSONField("Schlüssel", default=list, blank=True)
    zustand = models.TextField("Zustand", blank=True)
    zurueckgelassen = models.TextField("zurückgelassene Sachen", blank=True)
    unterschrift = models.FileField("Unterschrift (Foto)", upload_to=_foto_pfad, blank=True)
    uebersprungen = models.JSONField(
        "übersprungene Schritte",
        default=dict,
        blank=True,
        help_text='Schritt → Begründung, z. B. {"zaehler": "Einheit ohne Strom"}.',
    )
    angelegt_am = models.DateTimeField("angelegt am", auto_now_add=True)

    class Meta:
        verbose_name = "Übergabe"
        verbose_name_plural = "Übergaben"
        ordering = ("-datum", "-pk")

    def __str__(self) -> str:
        return f"{self.get_art_display()} #{self.pk}"


class UebergabeFoto(ProtokolliertesModell):
    uebergabe = models.ForeignKey(
        Uebergabe, verbose_name="Übergabe", on_delete=models.CASCADE, related_name="fotos"
    )
    bild = models.FileField("Foto", upload_to=_foto_pfad)
    beschreibung = models.CharField("Beschreibung", max_length=200, blank=True)
    angelegt_am = models.DateTimeField("angelegt am", auto_now_add=True)

    class Meta:
        verbose_name = "Foto einer Übergabe"
        verbose_name_plural = "Fotos der Übergaben"
        ordering = ("uebergabe", "pk")

    def __str__(self) -> str:
        return f"Foto #{self.pk}"


# --- Kündigung -----------------------------------------------------------------------


class Kuendigung(ProtokolliertesModell):
    """Kündigung eines Vertrags mit Zugang, Fristende und Rückgabetermin (H2.8)."""

    class Von(models.TextChoices):
        MIETER = "mieter", "Mieter"
        VERMIETER = "vermieter", "Vermieter"

    class Zugangsweg(models.TextChoices):
        """Wie die Kündigung zuging (Review 2, A3). Website und Mail sind Textform: Die
        Bestätigung erkennt die Form dann nicht ausdrücklich an (offene Frage an den
        Anwalt)."""

        UNBEKANNT = "", "nicht angegeben"
        BRIEF = "brief", "Brief"
        EINSCHREIBEN = "einschreiben", "Einschreiben"
        PERSOENLICH = "persoenlich", "persönlich übergeben"
        MAIL = "mail", "E-Mail"
        WEBSITE = "website", "Website-Formular"

    #: Zugangswege in Textform (ohne eigenhändige Unterschrift).
    TEXTFORM = (Zugangsweg.MAIL, Zugangsweg.WEBSITE)

    class Absender(models.TextChoices):
        """Warum eine Kündigung gilt, deren Absender nicht als Mieter am Vertrag steht
        (Review 3, Punkt 9; offene Frage an den Anwalt, ``docs/anwalt-fragen.md``)."""

        PASST = "", "Absender ist Mieter laut Vertrag"
        VOLLMACHT = "vollmacht", "Vollmacht liegt vor"
        VERTRAGSPARTEI = "vertragspartei", "Absender ist Vertragspartei (anderer Name/E-Mail)"

    vertrag = models.ForeignKey(
        Vertrag, verbose_name="Vertrag", on_delete=models.PROTECT, related_name="kuendigungen"
    )
    von = models.CharField("gekündigt von", max_length=20, choices=Von.choices, default=Von.MIETER)
    zugang_am = models.DateField("Zugang am")
    zugangsweg = models.CharField(
        "Zugangsweg", max_length=20, choices=Zugangsweg.choices, blank=True, default=""
    )
    aufhebung = models.BooleanField(
        "Aufhebung zum Wunschtermin",
        default=False,
        help_text="Das Vertragsende ist der Wunschtermin vor dem Fristende; wirksam erst mit "
        "dem unterschriebenen Aufhebungsvertrag (Vorlage „Aufhebungsvertrag“).",
    )
    absender = models.CharField(
        "Absender",
        max_length=20,
        choices=Absender.choices,
        blank=True,
        default=Absender.PASST,
        help_text="Bei abweichendem Absender: Vollmacht oder Vertragspartei unter anderem "
        "Namen bzw. anderer E-Mail-Adresse.",
    )
    kuendigungsregel = models.CharField("angewandte Kündigungsfrist", max_length=50)
    zum_wunsch = models.DateField("gewünschtes Ende", null=True, blank=True)
    fristende = models.DateField("Fristende")
    rueckgabetermin = models.DateField("Rückgabetermin")
    angelegt_am = models.DateTimeField("angelegt am", auto_now_add=True)

    class Meta:
        verbose_name = "Kündigung"
        verbose_name_plural = "Kündigungen"
        ordering = ("-zugang_am", "-pk")

    def __str__(self) -> str:
        return f"Kündigung #{self.pk}"

    @property
    def in_textform(self) -> bool:
        return self.zugangsweg in self.TEXTFORM

    @property
    def vorgang_text(self) -> str:
        """Für Titel im Protokoll, nur Nummern und Daten: „Kündigung V-0001“ oder
        „Aufhebung V-0001 zum 31.12.2026“. Bei abweichendem Absender steht die Wahl dabei
        (Review 3, Punkt 9): „Kündigung V-0001 (Vollmacht liegt vor)“."""
        if self.aufhebung:
            text = f"Aufhebung {self.vertrag.nummer} zum {self.fristende:%d.%m.%Y}"
        else:
            text = f"Kündigung {self.vertrag.nummer}"
        if self.absender:
            text += f" ({self.get_absender_display()})"
        return text


# --- Personenfelder (CLAUDE.md, Regel 4.6) ------------------------------------------

personenfelder.register(Einheit, ["notiz"])
personenfelder.unbedenklich(Einheit, ["nw_id", "typ", "bezeichnung", "vermarktung", "website_slug"])
personenfelder.unbedenklich(EinheitStatus, ["status", "grund"])
personenfelder.unbedenklich(Zielmiete, ["begruendung"])
personenfelder.register(
    Partei,
    ["anrede", "vorname", "nachname", "firma", "adresszusatz", "strasse", "plz", "ort", "notiz"],
)
personenfelder.unbedenklich(Partei, ["art", "herkunft"])
personenfelder.register(Kontaktweg, ["wert"])
personenfelder.unbedenklich(Kontaktweg, ["art"])
personenfelder.register(Vertrag, ["sonderabreden"])
personenfelder.unbedenklich(Vertrag, ["nummer", "vertragsart", "kuendigungsregel", "status"])
personenfelder.unbedenklich(Vertrag, ["herkunft"])
personenfelder.unbedenklich(Vertragspartei, ["rolle"])
personenfelder.unbedenklich(Kondition, ["zahlweise", "grund"])
personenfelder.register(Kaution, ["abrechnung_notiz"])
personenfelder.unbedenklich(Kaution, ["konto", "art"])
personenfelder.register(Schluessel, ["inhaber_text"])
personenfelder.unbedenklich(Schluessel, ["art", "bezeichnung", "inhaber"])
personenfelder.unbedenklich(Checkliste, ["phase", "titel"])
personenfelder.register(Checkpunkt, ["notiz"])
personenfelder.unbedenklich(Checkpunkt, ["text"])
personenfelder.register(Uebergabe, ["zustand", "zurueckgelassen", "uebersprungen"])
personenfelder.unbedenklich(Uebergabe, ["art", "status"])
personenfelder.register(UebergabeFoto, ["beschreibung"])
personenfelder.unbedenklich(Kuendigung, ["von", "kuendigungsregel", "zugangsweg", "absender"])
