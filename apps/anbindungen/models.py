"""Modelle der Anbindungen (Schritt 10): Website und Verwaltio.

- ``Websitezugang``: Token, mit dem die Website Katalog und Bilder liest und Ereignisse meldet
  (gespeichert nur als Hash, Muster aus ``apps.bank``).
- ``Einheitbild``: Vermarktungsbild einer Einheit für den Katalog. Keine Personendaten.
- ``Buchungsanfrage``: Buchung oder Kündigung, die die Website gemeldet hat (W10). Die
  Kontaktdaten der anfragenden Person sind Personenfelder.
- ``Kennzahl``: nächtliche Zahlen aus Verwaltio je Wohneinheit (V12), nur mit NW-ID.
- ``Abweichung``: NW-ID, die in Website oder Verwaltio fehlt oder abweicht (H1.6).

Geschrieben wird nur über ``apps.anbindungen.services`` (CLAUDE.md, Regel 4.1).
"""

from __future__ import annotations

import datetime
import uuid
from pathlib import PurePath
from typing import Any

from django.db import models
from django.db.models import Q

from apps.kern import personenfelder
from apps.kern.protokoll import ProtokolliertesModell
from apps.stammdaten.models import Einheit, Kuendigung, Vertrag


def _bild_pfad(instanz: Any, dateiname: str) -> str:
    """Neutraler Dateiname ohne Teile des Originalnamens."""
    endung = PurePath(dateiname).suffix.lower()[:10]
    heute = datetime.date.today()  # zeit: echt (Ablageordner nach Speicherdatum)
    return f"anbindungen/bilder/{heute:%Y/%m}/{uuid.uuid4().hex}{endung}"


class Websitezugang(ProtokolliertesModell):
    """Ein Token der Website. Der Token selbst steht nirgends im System, nur sein Hash."""

    bezeichnung = models.CharField(
        "Bezeichnung", max_length=100, help_text="z. B. „Website-Server“."
    )
    token_hash = models.CharField(
        "Hash des Tokens",
        max_length=64,
        unique=True,
        help_text="SHA-256 des Tokens. Der Token selbst steht nirgends im System.",
    )
    aktiv = models.BooleanField("aktiv", default=True)
    angelegt_am = models.DateTimeField("angelegt am", auto_now_add=True)

    class Meta:
        verbose_name = "Website-Zugang"
        verbose_name_plural = "Website-Zugänge"
        ordering = ("-angelegt_am", "-pk")

    def __str__(self) -> str:
        return f"Website-Zugang #{self.pk}"


class Einheitbild(ProtokolliertesModell):
    """Vermarktungsbild einer Einheit für den Katalog der Website.

    Entfernt wird nicht gelöscht, sondern ``aktiv=False`` (alte Stände bleiben lesbar).
    Bilder zeigen die Einheit, keine Personen.
    """

    einheit = models.ForeignKey(
        Einheit, verbose_name="Einheit", on_delete=models.PROTECT, related_name="bilder"
    )
    bild = models.FileField("Bild", upload_to=_bild_pfad)
    beschreibung = models.CharField(
        "Beschreibung",
        max_length=200,
        blank=True,
        help_text="Alternativtext für die Website, z. B. „Tor von außen“. Keine Namen.",
    )
    reihenfolge = models.PositiveSmallIntegerField("Reihenfolge", default=0)
    aktiv = models.BooleanField("aktiv", default=True)
    angelegt_am = models.DateTimeField("angelegt am", auto_now_add=True)

    class Meta:
        verbose_name = "Bild einer Einheit"
        verbose_name_plural = "Bilder der Einheiten"
        ordering = ("einheit", "reihenfolge", "pk")

    def __str__(self) -> str:
        return f"Einheitbild #{self.pk}"


class Buchungsanfrage(ProtokolliertesModell):
    """Buchung oder Kündigung von der Website (W10). Den Vertrag legst du in v1 von Hand an."""

    class Art(models.TextChoices):
        BUCHUNG = "buchung", "Buchung"
        KUENDIGUNG = "kuendigung", "Kündigung"

    class Status(models.TextChoices):
        OFFEN = "offen", "offen"
        ANGENOMMEN = "angenommen", "angenommen"
        ABGELEHNT = "abgelehnt", "abgelehnt"
        VERTRAG_ANGELEGT = "vertrag_angelegt", "Vertrag angelegt"
        ERLEDIGT = "erledigt", "erledigt"

    ereignis_id = models.CharField(
        "Ereignis-ID der Website",
        max_length=100,
        unique=True,
        help_text="Macht die Meldung idempotent: Dieselbe ID kommt nur einmal ins System.",
    )
    pruefsumme = models.CharField(
        "Prüfsumme der Meldung",
        max_length=64,
        help_text="SHA-256 der Meldung; dieselbe ID mit anderem Inhalt wird abgewiesen.",
    )
    art = models.CharField("Art", max_length=20, choices=Art.choices)
    einheit = models.ForeignKey(
        Einheit,
        verbose_name="Einheit",
        on_delete=models.PROTECT,
        related_name="buchungsanfragen",
    )
    status = models.CharField(
        "Status", max_length=20, choices=Status.choices, default=Status.OFFEN, db_index=True
    )
    gemeldet_am = models.DateTimeField("gemeldet am (Website)")
    wunsch_ab = models.DateField("gewünscht ab", null=True, blank=True)
    vertragsnummer = models.CharField(
        "Vertragsnummer (gemeldet)",
        max_length=20,
        blank=True,
        help_text="Bei einer Kündigung: die Nummer, die die Person angegeben hat.",
    )
    kontakt_name = models.CharField("Name", max_length=200, blank=True)
    kontakt_email = models.EmailField("E-Mail", max_length=200, blank=True)
    kontakt_telefon = models.CharField("Telefon", max_length=50, blank=True)
    nachricht = models.TextField("Nachricht", blank=True)
    vertrag = models.ForeignKey(
        Vertrag,
        verbose_name="Vertrag",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="buchungsanfragen",
    )
    kuendigung = models.ForeignKey(
        Kuendigung,
        verbose_name="Kündigung",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="buchungsanfragen",
        help_text="Bei einer Kündigung von der Website: die daraus erfasste Kündigung.",
    )
    entschieden_am = models.DateTimeField("entschieden am", null=True, blank=True)
    empfangen_am = models.DateTimeField("empfangen am", auto_now_add=True)

    class Meta:
        verbose_name = "Buchungsanfrage"
        verbose_name_plural = "Buchungsanfragen"
        ordering = ("-gemeldet_am", "-pk")

    def __str__(self) -> str:
        # Nur die ID: __str__ landet in Logs und Fehlermeldungen.
        return f"Buchungsanfrage #{self.pk}"

    @property
    def ist_offen(self) -> bool:
        return self.status == self.Status.OFFEN


class Kennzahl(ProtokolliertesModell):
    """Zahlen aus Verwaltio je Wohneinheit und Tag (V12). Nur NW-ID, keine Namen."""

    class NkaStand(models.TextChoices):
        UNBEKANNT = "unbekannt", "unbekannt"
        OFFEN = "offen", "noch nicht begonnen"
        IN_ARBEIT = "in_arbeit", "in Arbeit"
        VERSENDET = "versendet", "versendet"
        ABGESCHLOSSEN = "abgeschlossen", "abgeschlossen"

    datum = models.DateField("Datum")
    nw_id = models.CharField("NW-ID", max_length=30, db_index=True)
    einheit = models.ForeignKey(
        Einheit,
        verbose_name="Einheit",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="kennzahlen",
    )
    offene_posten = models.DecimalField("offene Posten (Summe)", max_digits=12, decimal_places=2)
    mahnfaelle = models.PositiveIntegerField("Mahnfälle")
    nka_jahr = models.PositiveSmallIntegerField("NKA-Jahr", null=True, blank=True)
    nka_stand = models.CharField(
        "Stand der NKA", max_length=20, choices=NkaStand.choices, default=NkaStand.UNBEKANNT
    )
    fristen_offen = models.PositiveIntegerField("offene Fristen")
    naechste_frist = models.DateField("nächste Frist", null=True, blank=True)
    abgerufen_am = models.DateTimeField("abgerufen am", auto_now=True)

    class Meta:
        verbose_name = "Kennzahl Wohnraum"
        verbose_name_plural = "Kennzahlen Wohnraum"
        ordering = ("-datum", "nw_id")
        constraints = (
            models.UniqueConstraint(fields=("nw_id", "datum"), name="anbindungen_kennzahl_je_tag"),
            models.CheckConstraint(
                condition=Q(offene_posten__gte=0), name="anbindungen_kennzahl_positiv"
            ),
        )

    def __str__(self) -> str:
        return f"Kennzahl {self.nw_id} {self.datum:%Y-%m-%d}"


class Abweichung(ProtokolliertesModell):
    """Eine NW-ID, die in Website oder Verwaltio fehlt oder abweicht (H1.6).

    Offen, solange ``behoben_am`` leer ist. Der Lauf ``id_abgleich`` setzt es, sobald die
    Abweichung bei einem Lauf nicht mehr auftritt.
    """

    class Quelle(models.TextChoices):
        WEBSITE = "website", "Website"
        VERWALTIO = "verwaltio", "Verwaltio"

    class Art(models.TextChoices):
        FEHLT = "fehlt", "fehlt in der Quelle"
        UNBEKANNT = "unbekannt", "im Hof-System unbekannt"
        SCHREIBWEISE = "schreibweise", "andere Schreibweise"
        NICHT_ERWARTET = "nicht_erwartet", "dort nicht erwartet"
        SLUG = "slug", "Website-Slug weicht ab"

    quelle = models.CharField("Quelle", max_length=20, choices=Quelle.choices)
    art = models.CharField("Art", max_length=20, choices=Art.choices)
    nw_id = models.CharField(
        "NW-ID",
        max_length=100,
        help_text="Die NW-ID im Hof-System oder, wenn unbekannt, die ID aus der Quelle.",
    )
    gemeldet = models.CharField(
        "Wert in der Quelle",
        max_length=100,
        blank=True,
        help_text="ID oder Slug, wie die Quelle ihn führt (keine Namen).",
    )
    erkannt_am = models.DateField("erkannt am")
    zuletzt_gesehen_am = models.DateField("zuletzt gesehen am")
    behoben_am = models.DateField("behoben am", null=True, blank=True)

    class Meta:
        verbose_name = "ID-Abweichung"
        verbose_name_plural = "ID-Abweichungen"
        ordering = ("behoben_am", "quelle", "nw_id", "pk")
        constraints = (
            models.UniqueConstraint(
                fields=("quelle", "art", "nw_id"),
                condition=Q(behoben_am__isnull=True),
                name="anbindungen_abweichung_einmal_offen",
            ),
        )

    def __str__(self) -> str:
        return f"Abweichung #{self.pk} {self.quelle} {self.art} {self.nw_id}"

    @property
    def ist_offen(self) -> bool:
        return self.behoben_am is None


# --- Personenfelder (CLAUDE.md, Regel 4.6) ------------------------------------------

personenfelder.unbedenklich(Websitezugang, ["bezeichnung", "token_hash"])
personenfelder.unbedenklich(Einheitbild, ["beschreibung"])
personenfelder.register(
    Buchungsanfrage, ["kontakt_name", "kontakt_email", "kontakt_telefon", "nachricht"]
)
personenfelder.unbedenklich(
    Buchungsanfrage, ["ereignis_id", "pruefsumme", "art", "status", "vertragsnummer"]
)
personenfelder.unbedenklich(Kennzahl, ["nw_id", "nka_stand"])
personenfelder.unbedenklich(Abweichung, ["quelle", "art", "nw_id", "gemeldet"])
