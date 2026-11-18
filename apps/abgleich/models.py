"""Modelle des Abgleichs (Schritt 6; H1.4, H3.5 bis H3.7, H3.11).

Geschrieben wird nur über ``apps.abgleich.services`` (CLAUDE.md, Regel 4.1).

Kern der Kette: Vertrag → Sollposten → **Zuordnung** → Buchung → Bankumsatz.

- ``Zahlerregel`` erweitert eine ``kassenbuch.Zuordnungsregel`` um den Vertrag, für den jemand
  zahlt (H1.4): ein Vertrag, eine Vertragsliste (Sammelzahler) oder „nicht vertragsbezogen“,
  dazu optional der erwartete Betrag. Eigenes Modell statt neuer Felder im Kassenbuch: Das
  Kassenbuch bleibt ohne Migration und ohne Wissen über Verträge (kleiner Konflikt mit den
  Schritten 3a/3b, die das Kassenbuch ebenfalls erweitern).
- ``Zuordnung`` verbindet eine Buchung (oder eine ``Handzahlung``) mit einem Sollposten, ganz
  oder in Teilen. Mehrere je Buchung und je Posten. Die Summe je Buchung ist höchstens ihr
  Betrag, die Summe je Posten höchstens sein Betrag (Trigger in der Datenbank, dazu die
  Prüfung im Service unter ``select_for_update``).
- ``Guthaben`` ist der Teil einer Zahlung, der über die offenen Posten hinausgeht. Der Lauf
  verrechnet es mit dem nächsten fälligen Posten (dann entsteht eine Zuordnung mit Verweis auf
  das Guthaben); ein Rest bleibt als neues Guthaben stehen.
- ``Handzahlung`` trägt Zahlungen ohne Bankumsatz (H3.7): Barzahlung, Zahlung auf ein fremdes
  Konto, Erstattung eines Guthabens. Grund und Beleg sind Pflicht; der Beleg liegt als
  ``dokumente.Dokument`` in der Ablage und hängt über einen Dokumentbezug an der Zahlung.
- ``Klaerfall`` ist der fachliche Inhalt eines Arbeitspostens „Zahlungen klären“: Art,
  Vorschlag und Konfidenz.

Stornieren statt löschen: Zuordnungen, Guthaben und Handzahlungen bekommen ``storniert_am``;
Trigger verhindern jede andere Änderung an Gebuchtem.
"""

from __future__ import annotations

from decimal import Decimal

import pgtrigger
from django.db import models
from django.db.models import Q

from apps.bank.models import Bankumsatz
from apps.dokumente.models import Dokument
from apps.kassenbuch.models import Buchung, Zuordnungsregel
from apps.kern import personenfelder
from apps.kern.models import Lauf
from apps.kern.protokoll import ProtokolliertesModell
from apps.soll.models import Sollposten
from apps.stammdaten.models import Vertrag


class Zahlerregel(ProtokolliertesModell):
    """Wer für welchen Vertrag zahlt (H1.4), als Zusatz zu einer Zuordnungsregel.

    Genau eins von: ``vertrag`` (ein Vertrag), ``sammelvertraege`` (Sammelzahler, mindestens
    zwei Verträge) oder ``nicht_vertragsbezogen`` (Zahlungen nach dieser Regel gehören zu
    keinem Vertrag, etwa Pacht ohne Vertrag im Hof-System oder Erstattungen).
    """

    regel = models.OneToOneField(
        Zuordnungsregel,
        verbose_name="Zuordnungsregel",
        on_delete=models.PROTECT,
        related_name="zahlerregel",
    )
    vertrag = models.ForeignKey(
        Vertrag,
        verbose_name="Vertrag",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="zahlerregeln",
    )
    sammelvertraege: models.ManyToManyField[Vertrag, Sammelvertrag] = models.ManyToManyField(
        Vertrag,
        verbose_name="Verträge (Sammelzahler)",
        through="Sammelvertrag",
        related_name="sammelzahlerregeln",
        blank=True,
    )
    betrag = models.DecimalField(
        "erwarteter Betrag",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Leer: wie das Soll. Gesetzt: Eine Zahlung genau in dieser Höhe gilt als "
        "klar, auch wenn sie den Posten nur teilweise deckt (z. B. vereinbarte Rate).",
    )
    nicht_vertragsbezogen = models.BooleanField("nicht vertragsbezogen", default=False)
    herkunft = models.CharField(
        "Herkunft",
        max_length=100,
        blank=True,
        help_text="Schlüssel aus einem Import, z. B. „hof-fints:17“.",
    )
    angelegt_am = models.DateTimeField("angelegt am", auto_now_add=True)

    class Meta:
        verbose_name = "Zahlerregel"
        verbose_name_plural = "Zahlerregeln"
        ordering = ("regel",)
        constraints = (
            models.CheckConstraint(
                condition=Q(betrag__isnull=True) | Q(betrag__gt=0),
                name="abgleich_zahlerregel_betrag_positiv",
            ),
            models.CheckConstraint(
                condition=~(Q(vertrag__isnull=False) & Q(nicht_vertragsbezogen=True)),
                name="abgleich_zahlerregel_vertrag_oder_nicht",
            ),
        )

    def __str__(self) -> str:
        return f"Zahlerregel #{self.pk}"

    def vertraege(self) -> list[Vertrag]:
        """Die Verträge der Regel: einer, die Sammelliste oder keiner."""
        if self.vertrag_id is not None:
            return [self.vertrag]  # type: ignore[list-item]
        return list(self.sammelvertraege.order_by("nummer"))


class Sammelvertrag(ProtokolliertesModell):
    """Ein Vertrag in der Liste eines Sammelzahlers."""

    zahlerregel = models.ForeignKey(
        Zahlerregel,
        verbose_name="Zahlerregel",
        on_delete=models.CASCADE,
        related_name="sammelzeilen",
    )
    vertrag = models.ForeignKey(
        Vertrag, verbose_name="Vertrag", on_delete=models.PROTECT, related_name="sammelzeilen"
    )

    class Meta:
        verbose_name = "Vertrag eines Sammelzahlers"
        verbose_name_plural = "Verträge der Sammelzahler"
        constraints = (
            models.UniqueConstraint(
                fields=("zahlerregel", "vertrag"), name="abgleich_sammelvertrag_einmal"
            ),
        )

    def __str__(self) -> str:
        return f"Sammelvertrag #{self.pk}"


class Handzahlung(ProtokolliertesModell):
    """Zahlung ohne Bankumsatz (H3.7). Grund und Beleg sind Pflicht."""

    class Art(models.TextChoices):
        BAR = "bar", "Barzahlung"
        FREMDES_KONTO = "fremdes_konto", "Zahlung auf ein fremdes Konto"
        ERSTATTUNG = "erstattung", "Erstattung eines Guthabens"

    art = models.CharField("Art", max_length=20, choices=Art.choices)
    vertrag = models.ForeignKey(
        Vertrag, verbose_name="Vertrag", on_delete=models.PROTECT, related_name="handzahlungen"
    )
    datum = models.DateField("Datum")
    betrag = models.DecimalField("Betrag", max_digits=12, decimal_places=2)
    grund = models.TextField("Grund")
    beleg = models.ForeignKey(
        Dokument,
        verbose_name="Beleg",
        on_delete=models.PROTECT,
        related_name="handzahlungen",
        help_text="Quittung oder Kontoauszug in der Ablage (Dokumentart „Beleg“).",
    )
    storniert_am = models.DateField("storniert am", null=True, blank=True)
    storno_grund = models.CharField("Grund des Stornos", max_length=200, blank=True)
    angelegt_am = models.DateTimeField("angelegt am", auto_now_add=True)

    class Meta:
        verbose_name = "Handzahlung"
        verbose_name_plural = "Handzahlungen"
        ordering = ("-datum", "-pk")
        constraints = (
            models.CheckConstraint(
                condition=Q(betrag__gt=0), name="abgleich_handzahlung_betrag_positiv"
            ),
        )
        triggers = (
            # Gebuchtes wird nie geändert, nur storniert (CLAUDE.md, Regel 4.4).
            pgtrigger.ReadOnly(
                name="abgleich_handzahlung_nur_storno", exclude=["storniert_am", "storno_grund"]
            ),
        )

    def __str__(self) -> str:
        return f"Handzahlung #{self.pk}"


class Klaerfall(ProtokolliertesModell):
    """Inhalt eines Arbeitspostens „Zahlungen klären“ (H3.6): was unklar ist, was das System
    vorschlägt und wie sicher es ist. Gebucht wird nur über eine Aktion des Benutzers."""

    class Art(models.TextChoices):
        UNBEKANNT = "unbekannt", "unbekannter Zahler"
        ZWECK = "zweck", "Zweck widerspricht der Regel"
        BETRAG = "betrag", "Betrag weicht ab"
        MEHRDEUTIG = "mehrdeutig", "mehrere gleich gute Treffer"
        KEIN_POSTEN = "kein_posten", "kein offener Posten"
        RUECKBUCHUNG = "rueckbuchung", "Rückbuchung"
        ERNEUT = "erneut", "wieder offen"
        LIEFERANT = "lieferant", "Eingang eines Lieferanten"

    class Ergebnis(models.TextChoices):
        OFFEN = "", "offen"
        ZUGEORDNET = "zugeordnet", "zugeordnet"
        NICHT_VERTRAGSBEZOGEN = "nicht_vertragsbezogen", "nicht vertragsbezogen"
        RUECKBUCHUNG = "rueckbuchung", "Rückbuchung bestätigt"
        KEINE_RUECKBUCHUNG = "keine_rueckbuchung", "keine Rückbuchung"

    buchung = models.ForeignKey(
        Buchung, verbose_name="Buchung", on_delete=models.PROTECT, related_name="klaerfaelle"
    )
    art = models.CharField("Art", max_length=20, choices=Art.choices)
    ergebnis = models.CharField(
        "Ergebnis", max_length=30, choices=Ergebnis.choices, blank=True, default=""
    )
    vorschlag = models.JSONField(
        "Vorschlag",
        default=dict,
        blank=True,
        help_text="IDs und Beträge: vertrag, posten [[ID, Betrag]], guthaben.",
    )
    konfidenz = models.DecimalField(
        "Konfidenz", max_digits=3, decimal_places=2, default=Decimal("0.00")
    )
    gegen_umsatz = models.ForeignKey(
        Bankumsatz,
        verbose_name="Rückbuchung (Umsatz)",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="rueckbuchung_klaerfaelle",
    )
    lauf = models.ForeignKey(
        Lauf, verbose_name="Lauf", on_delete=models.PROTECT, null=True, blank=True
    )
    erledigt_am = models.DateTimeField("geklärt am", null=True, blank=True)
    angelegt_am = models.DateTimeField("angelegt am", auto_now_add=True)

    class Meta:
        verbose_name = "Klärfall"
        verbose_name_plural = "Klärfälle"
        ordering = ("-angelegt_am", "-pk")
        constraints = (
            models.CheckConstraint(
                condition=Q(konfidenz__gte=0) & Q(konfidenz__lte=1),
                name="abgleich_klaerfall_konfidenz",
            ),
        )

    def __str__(self) -> str:
        return f"Klärfall #{self.pk}"

    @property
    def offen(self) -> bool:
        return self.ergebnis == self.Ergebnis.OFFEN


class Guthaben(ProtokolliertesModell):
    """Was eine Zahlung über die offenen Posten hinaus gebracht hat (aus Buchung oder
    Handzahlung). Aufgelöst wird es durch Verrechnung (``verrechnet_mit``), Erstattung
    (``erstattet_durch``) oder Storno; ein nicht verbrauchter Rest wird ein neues Guthaben
    (``rest_von``)."""

    vertrag = models.ForeignKey(
        Vertrag, verbose_name="Vertrag", on_delete=models.PROTECT, related_name="guthaben"
    )
    betrag = models.DecimalField("Betrag", max_digits=12, decimal_places=2)
    buchung = models.ForeignKey(
        Buchung,
        verbose_name="aus Buchung",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="guthaben",
    )
    handzahlung = models.ForeignKey(
        Handzahlung,
        verbose_name="aus Handzahlung",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="guthaben",
    )
    rest_von = models.ForeignKey(
        "self",
        verbose_name="Rest von Guthaben",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reste",
    )
    verrechnet_mit = models.ForeignKey(
        Sollposten,
        verbose_name="verrechnet mit",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="verrechnete_guthaben",
    )
    erstattet_durch = models.ForeignKey(
        Handzahlung,
        verbose_name="erstattet durch",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="erstattete_guthaben",
    )
    aufgeloest_am = models.DateField("aufgelöst am", null=True, blank=True)
    storniert_am = models.DateField("storniert am", null=True, blank=True)
    storno_grund = models.CharField("Grund des Stornos", max_length=200, blank=True)
    angelegt_am = models.DateTimeField("angelegt am", auto_now_add=True)

    class Meta:
        verbose_name = "Guthaben"
        verbose_name_plural = "Guthaben"
        ordering = ("vertrag", "pk")
        constraints = (
            models.CheckConstraint(condition=Q(betrag__gt=0), name="abgleich_guthaben_positiv"),
            models.CheckConstraint(
                condition=Q(buchung__isnull=False, handzahlung__isnull=True)
                | Q(buchung__isnull=True, handzahlung__isnull=False),
                name="abgleich_guthaben_eine_quelle",
            ),
        )
        triggers = (
            pgtrigger.ReadOnly(
                name="abgleich_guthaben_nur_aufloesen",
                exclude=[
                    "verrechnet_mit",
                    "erstattet_durch",
                    "aufgeloest_am",
                    "storniert_am",
                    "storno_grund",
                ],
            ),
            # Zuordnungen und offene Guthaben einer Buchung ergeben höchstens ihren Betrag.
            pgtrigger.Trigger(
                name="abgleich_guthaben_summe",
                operation=pgtrigger.Insert | pgtrigger.Update,
                when=pgtrigger.After,
                func="""
                IF NEW.buchung_id IS NOT NULL AND (
                    COALESCE((SELECT SUM(z.betrag) FROM abgleich_zuordnung z
                              WHERE z.buchung_id = NEW.buchung_id
                                AND z.storniert_am IS NULL), 0)
                  + COALESCE((SELECT SUM(g.betrag) FROM abgleich_guthaben g
                              WHERE g.buchung_id = NEW.buchung_id AND g.storniert_am IS NULL
                                AND g.aufgeloest_am IS NULL), 0)
                ) > (SELECT b.betrag FROM kassenbuch_buchung b WHERE b.id = NEW.buchung_id) THEN
                    RAISE EXCEPTION 'abgleich: Buchung % ist mehr als voll zugeordnet',
                        NEW.buchung_id USING ERRCODE = 'check_violation';
                END IF;
                RETURN NULL;
                """,
            ),
        )

    def __str__(self) -> str:
        return f"Guthaben #{self.pk}"

    @property
    def offen(self) -> bool:
        return self.storniert_am is None and self.aufgeloest_am is None


class Zuordnung(ProtokolliertesModell):
    """Ein Teil einer Zahlung auf einen Sollposten. Wird nie geändert, nur storniert."""

    buchung = models.ForeignKey(
        Buchung,
        verbose_name="Buchung",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="zuordnungen",
    )
    handzahlung = models.ForeignKey(
        Handzahlung,
        verbose_name="Handzahlung",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="zuordnungen",
    )
    sollposten = models.ForeignKey(
        Sollposten, verbose_name="Sollposten", on_delete=models.PROTECT, related_name="zuordnungen"
    )
    betrag = models.DecimalField("Betrag", max_digits=12, decimal_places=2)
    datum = models.DateField("Zahlungsdatum", help_text="Tag der Zahlung (Bank oder Hand).")
    guthaben = models.ForeignKey(
        Guthaben,
        verbose_name="aus Guthaben",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="zuordnungen",
    )
    zahlerregel = models.ForeignKey(
        Zahlerregel,
        verbose_name="nach Zahlerregel",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="zuordnungen",
    )
    klaerfall = models.ForeignKey(
        Klaerfall,
        verbose_name="aus Klärfall",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="zuordnungen",
    )
    lauf = models.ForeignKey(
        Lauf,
        verbose_name="gebucht von Lauf",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="zuordnungen",
    )
    storniert_am = models.DateField("storniert am", null=True, blank=True)
    storno_grund = models.CharField("Grund des Stornos", max_length=200, blank=True)
    angelegt_am = models.DateTimeField("angelegt am", auto_now_add=True)

    class Meta:
        verbose_name = "Zuordnung"
        verbose_name_plural = "Zuordnungen"
        ordering = ("datum", "pk")
        indexes = (
            models.Index(fields=["buchung", "storniert_am"]),
            models.Index(fields=["sollposten", "storniert_am"]),
        )
        constraints = (
            models.CheckConstraint(condition=Q(betrag__gt=0), name="abgleich_zuordnung_positiv"),
            models.CheckConstraint(
                condition=Q(buchung__isnull=False, handzahlung__isnull=True)
                | Q(buchung__isnull=True, handzahlung__isnull=False),
                name="abgleich_zuordnung_eine_quelle",
            ),
        )
        triggers = (
            pgtrigger.ReadOnly(
                name="abgleich_zuordnung_nur_storno", exclude=["storniert_am", "storno_grund"]
            ),
            pgtrigger.Protect(
                name="abgleich_zuordnung_storno_endgueltig",
                operation=pgtrigger.Update,
                condition=pgtrigger.Q(old__storniert_am__isnull=False),
            ),
            # Summe je Buchung, je Handzahlung und je Posten höchstens deren Betrag.
            pgtrigger.Trigger(
                name="abgleich_zuordnung_summen",
                operation=pgtrigger.Insert | pgtrigger.Update,
                when=pgtrigger.After,
                func="""
                IF NEW.buchung_id IS NOT NULL AND
                    (SELECT SUM(z.betrag) FROM abgleich_zuordnung z
                     WHERE z.buchung_id = NEW.buchung_id AND z.storniert_am IS NULL)
                  > (SELECT b.betrag FROM kassenbuch_buchung b WHERE b.id = NEW.buchung_id) THEN
                    RAISE EXCEPTION 'abgleich: Zuordnungen übersteigen Buchung %',
                        NEW.buchung_id USING ERRCODE = 'check_violation';
                END IF;
                IF NEW.handzahlung_id IS NOT NULL AND
                    (SELECT SUM(z.betrag) FROM abgleich_zuordnung z
                     WHERE z.handzahlung_id = NEW.handzahlung_id AND z.storniert_am IS NULL)
                  > (SELECT h.betrag FROM abgleich_handzahlung h
                     WHERE h.id = NEW.handzahlung_id) THEN
                    RAISE EXCEPTION 'abgleich: Zuordnungen übersteigen Handzahlung %',
                        NEW.handzahlung_id USING ERRCODE = 'check_violation';
                END IF;
                IF (SELECT SUM(z.betrag) FROM abgleich_zuordnung z
                    WHERE z.sollposten_id = NEW.sollposten_id AND z.storniert_am IS NULL)
                  > (SELECT p.betrag FROM soll_sollposten p WHERE p.id = NEW.sollposten_id) THEN
                    RAISE EXCEPTION 'abgleich: Zuordnungen übersteigen Sollposten %',
                        NEW.sollposten_id USING ERRCODE = 'check_violation';
                END IF;
                RETURN NULL;
                """,
            ),
        )

    def __str__(self) -> str:
        return f"Zuordnung #{self.pk}"

    @property
    def gueltig(self) -> bool:
        return self.storniert_am is None


# --- Personenfelder (CLAUDE.md, Regel 4.6) ------------------------------------------
# Grund und Storno-Grund sind Freitext und können Personen nennen.

personenfelder.unbedenklich(Zahlerregel, ["herkunft"])
personenfelder.unbedenklich(Handzahlung, ["art"])
personenfelder.register(Handzahlung, ["grund", "storno_grund"])
personenfelder.unbedenklich(Klaerfall, ["art", "ergebnis"])
personenfelder.register(Guthaben, ["storno_grund"])
personenfelder.register(Zuordnung, ["storno_grund"])
