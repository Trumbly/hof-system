"""Testhof-Modul des Mahnwesens (Schritt 7): Stufen nach der SOP, freigegebene
Beispielvorlagen, der Sonderfall „Rechtsfall mit Mahnsperre“ und die Fälle je Stufe.

**Stufen.** Die Startdaten nach der SOP je Vertragsart (``apps.mahnwesen.startdaten``; die
Datenmigration legt sie schon an, ``startdaten_anlegen`` ist idempotent).

**Vorlagen.** Freigegeben vom Testbenutzer zum festen Zeitpunkt, nur mit Platzhaltern:
„Mahnung“ und „Abmahnung“ (dieses Modul) sowie „Zahlungserinnerung“ (Modul ``dokumente``).
Für die fristlose Kündigung gibt es absichtlich keine Vorlage („Fristlose Kündigung“): Diese
Stufe bleibt im Probebetrieb und erzeugt nur den Arbeitsposten.

**Fälle je Stufe** (Index = Vertrag nach Nummer wie in ``testhof.abgleich``). Der Testhof
gleicht nicht selbst ab; die Fälle entstehen, wenn Tests Einordnen, Abgleich und Mahnlauf
über die Montage ab dem 02.11.2026 laufen lassen (Bankdaten bis 31.10.2026):

- Erinnerung, Mahnung, Abmahnung: Vertrag 4 (Sonderfall ``dauerauftrag_alte_miete``). Der
  Dauerauftrag zahlt die alte Miete; Juli bis Oktober bleibt je ein Rest offen, also vier
  offene Monatsmieten.
- Kündigung: ebenfalls Vertrag 4, sobald die Bank die Zahlungen für September und Oktober
  zurückholt (der Test storniert ihre Zuordnungen): dann zwei Monatsmieten Rückstand. Die
  Stufe bleibt im Probebetrieb (keine Vorlage).
- Gesperrt durch einen Klärfall: Vertrag 9 (``falsche_einheit_im_verwendungszweck``).
- ``rechtsfall_mahnsperre``: Vertrag 16 (``rueckbuchung``) ist ein Rechtsfall mit
  Mahnsperre. Er bleibt gesperrt, auch wenn der Klärfall der Rückbuchung geklärt ist.
- ``teilzahlung_nachzahlung``: Vertrag 14 ist nach der Nachzahlung im September ohne
  Rückstand; kein Fall.

Geschrieben wird über ``apps.mahnwesen.services`` und ``apps.dokumente.services``.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field

from django.contrib.contenttypes.models import ContentType

from apps.dokumente import services as dokumente
from apps.dokumente.models import Vorlage
from apps.kern.models import Arbeitsposten
from apps.mahnwesen import services
from apps.mahnwesen.models import Mahnfall, Mahnschritt, Mahnsperre
from apps.stammdaten.models import Vertrag
from testhof import abgleich as testhof_abgleich
from testhof import register
from testhof.aufbau import Kontext
from testhof.dokumente import FORDERUNG_TABELLE

NAME = "mahnwesen"
SONDERFAELLE = ("rechtsfall_mahnsperre",)

#: Vertrag (Index nach Nummer) für Erinnerung, Mahnung, Abmahnung und Kündigung.
STUFENFALL = testhof_abgleich.DAUERAUFTRAG
#: Gesperrt durch einen offenen Klärfall.
KLAERFALL = testhof_abgleich.FALSCHER_ZWECK
#: Rechtsfall mit Mahnsperre.
RECHTSFALL = testhof_abgleich.RUECKBUCHUNG
RECHTSFALL_AB = datetime.date(2026, 10, 1)
#: Erster Montag nach dem Stichtag des Testhofs; die Bankdaten reichen bis 31.10.2026.
ERSTER_MONTAG = datetime.date(2026, 11, 2)

#: (Name, Art, Betreff, Text): nur Platzhalter, keine Namen, Anschriften oder Beträge.
VORLAGEN: tuple[tuple[str, str, str, str], ...] = (
    (
        "Mahnung",
        "mahnung",
        "Mahnung Vertrag {{ vertrag.nummer }}",
        """\
<p>{{ empfaenger.anrede|default:"Guten Tag" }},</p>
<p>trotz unserer Zahlungserinnerung ist für den Vertrag {{ vertrag.nummer }}
({{ einheit.bezeichnung }}) ein Betrag von {{ betrag.offen|euro }} offen:</p>
"""
        + FORDERUNG_TABELLE
        + """\
<p>Bitte zahlen Sie den Betrag bis zum {{ frist.bis|datum }} ({{ frist.tage }} Tage).</p>
<p>Mit freundlichen Grüßen<br>{{ absender.name }}</p>
""",
    ),
    (
        "Abmahnung",
        "abmahnung",
        "Abmahnung Vertrag {{ vertrag.nummer }}",
        """\
<p>{{ empfaenger.anrede|default:"Guten Tag" }},</p>
<p>für den Vertrag {{ vertrag.nummer }} ({{ einheit.bezeichnung }}) sind
{{ mahnung.offene_monatsmieten }} Monatsmieten ganz oder teilweise offen, zusammen
{{ betrag.offen|euro }}:</p>
"""
        + FORDERUNG_TABELLE
        + """\
<p>Wir mahnen den Zahlungsverzug ab. Zahlen Sie den Betrag nicht bis zum
{{ frist.bis|datum }}, behalten wir uns die Kündigung des Vertrags vor.</p>
<p>Mit freundlichen Grüßen<br>{{ absender.name }}</p>
""",
    ),
)


@dataclass
class MahnwesenDaten:
    vorlagen: dict[str, Vorlage]
    rechtsfall: Mahnsperre
    #: Rolle → Vertrag (``stufen``, ``klaerfall``, ``rechtsfall``)
    vertraege: dict[str, Vertrag] = field(default_factory=dict)
    erster_montag: datetime.date = ERSTER_MONTAG


def aufbauen(kontext: Kontext) -> MahnwesenDaten:
    services.startdaten_anlegen()
    benutzer = kontext.ablage["kern"].benutzer
    vorlagen = {}
    for name, art, betreff, text in VORLAGEN:
        vorlage = dokumente.vorlage_anlegen(name, art, text, betreff=betreff, herkunft="testhof")
        vorlagen[art] = dokumente.vorlage_freigeben(vorlage, benutzer, am=kontext.zeitpunkt)

    vertraege = list(Vertrag.objects.order_by("nummer"))
    rechtsfall = vertraege[RECHTSFALL]
    sperre = services.sperre_setzen(
        rechtsfall,
        Mahnsperre.Grund.RECHTSFALL,
        von=RECHTSFALL_AB,
        hinweis="Rechtsfall, Akte bei der Kanzlei (Testhof)",
    )
    return MahnwesenDaten(
        vorlagen=vorlagen,
        rechtsfall=sperre,
        vertraege={
            "stufen": vertraege[STUFENFALL],
            "klaerfall": vertraege[KLAERFALL],
            "rechtsfall": rechtsfall,
        },
    )


def leeren() -> None:
    """Löscht Fälle, Schritte und Sperren (nur erfundene Daten, siehe ``aufbau.leeren``).
    Die Stufen bleiben: Sie sind Konfiguration aus der Datenmigration. Die Vorlagen löscht
    das Modul ``dokumente``."""
    Mahnschritt.objects.all().delete()
    Arbeitsposten.objects.filter(bezug_typ=ContentType.objects.get_for_model(Mahnfall)).delete()
    Mahnfall.objects.all().delete()
    Mahnsperre.objects.all().delete()


register.registrieren(
    NAME,
    abhaengig_von=("abgleich", "dokumente", "stammdaten"),
    aufbauen=aufbauen,
    leeren=leeren,
    sonderfaelle=SONDERFAELLE,
)
