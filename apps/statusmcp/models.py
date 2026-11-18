"""Modell des Status-MCP: Kennzahlen ohne Personendaten (Schritt 11, H11.5, C3).

Der Status-MCP liest nur Sichten ``sm_*`` (Migration ``0002_sichten``). Was sich in SQL
nicht sauber abbilden lässt, weil die Logik in Python steckt (Rückstände aus
``abgleich.services.rueckstaende``, Lücken aus ``stammdaten.datenstand``, Leerstand aus
``stammdaten.leerstand``, NW-IDs zu Fristen aus ``cockpit.services.kennungen_zu_bezug``),
rechnet der Lauf ``statusmcp_aktualisieren`` im Tageslauf aus und legt es hier ab. So gibt
es für jede Zahl nur eine Wahrheit: den Python-Code, den auch das Hof-Cockpit nutzt.

Eine Zeile ist eine Kennzahl eines Bereichs. Welche Spalten ein Bereich nutzt, steht an
``Bereich``. Text steht nur in ``schluessel`` (feste Schlüssel aus dem Code), ``objekt``
(Objekt-Kürzel) und ``kennung`` (NW-ID oder Objekt-Kürzel); Namen, Beträge je Vertrag oder
Verwendungszwecke gibt es hier nicht.

Nicht im Protokoll: Die Tabelle ist eine Momentaufnahme, die jeder Lauf ganz neu schreibt.
Geschrieben wird nur über ``apps.statusmcp.services`` (CLAUDE.md, Regel 4.1).
"""

from __future__ import annotations

from django.db import models

from apps.kern import personenfelder


class Kennzahl(models.Model):
    """Eine Kennzahl der Momentaufnahme für den Status-MCP."""

    class Bereich(models.TextChoices):
        #: Rückstände je Objekt. ``objekt`` (leer: alle Objekte), ``schluessel`` =
        #: Altersklasse („0_30“, „31_60“, „ueber_60“), „summe“, „nicht_pruefbar“ oder
        #: „in_klaerung“; ``anzahl`` = Verträge, ``betrag`` = Summe; ``datum`` =
        #: Datenstand der Bank.
        RUECKSTAND = "rueckstand", "Rückstand je Objekt"
        #: Einheiten mit Rückstand: ``kennung`` = NW-ID, ``objekt``, ``tage`` = Alter der
        #: ältesten offenen Fälligkeit, ``schluessel`` = Altersklasse. Kein Betrag.
        RUECKSTAND_EINHEIT = "rueckstand_einheit", "Einheit mit Rückstand"
        #: Lücken je Bereich des Datenstands: ``schluessel`` = Bereich, ``anzahl`` = fehlende
        #: Pflichtfelder, ``gesamt`` = geprüfte Einträge.
        DATENSTAND = "datenstand", "Datenstand je Bereich"
        #: NW-IDs bzw. Objekt-Kürzel mit Lücken: ``schluessel`` = Bereich, ``kennung``,
        #: ``anzahl`` = fehlende Pflichtfelder.
        DATENSTAND_KENNUNG = "datenstand_kennung", "Kennung mit Lücken"
        #: Freie Einheiten: ``kennung`` = NW-ID, ``objekt``, ``schluessel`` = Typ, ``datum`` =
        #: frei seit, ``tage`` = Tage frei bis zum Stichtag.
        LEERSTAND = "leerstand", "freie Einheit"
        #: NW-IDs bzw. Objekt-Kürzel einer offenen Frist: ``frist_id``, ``kennung``.
        FRIST = "frist", "Kennung einer Frist"

    bereich = models.CharField("Bereich", max_length=30, choices=Bereich.choices, db_index=True)
    schluessel = models.CharField("Schlüssel", max_length=50, blank=True)
    objekt = models.CharField("Objekt-Kürzel", max_length=20, blank=True)
    kennung = models.CharField("NW-ID oder Objekt-Kürzel", max_length=60, blank=True)
    frist_id = models.PositiveBigIntegerField("Frist (ID)", null=True, blank=True, db_index=True)
    anzahl = models.IntegerField("Anzahl", null=True, blank=True)
    gesamt = models.IntegerField("Grundmenge", null=True, blank=True)
    betrag = models.DecimalField("Betrag", max_digits=14, decimal_places=2, null=True, blank=True)
    tage = models.IntegerField("Tage", null=True, blank=True)
    datum = models.DateField("Datum", null=True, blank=True)
    stichtag = models.DateField("Stichtag")
    erstellt_am = models.DateTimeField("erstellt am")

    class Meta:
        verbose_name = "Kennzahl des Status-MCP"
        verbose_name_plural = "Kennzahlen des Status-MCP"
        ordering = ("bereich", "objekt", "schluessel", "kennung", "pk")

    def __str__(self) -> str:
        return f"Kennzahl #{self.pk} ({self.bereich})"


# --- Personenfelder (CLAUDE.md, Regel 4.6) ------------------------------------------
# Nur Schlüssel aus dem Code, Objekt-Kürzel und NW-IDs.

personenfelder.unbedenklich(Kennzahl, ["bereich", "schluessel", "objekt", "kennung"])
