"""Testhof-Modul der Fristen: ein paar Fristen mit Quelle „von Hand“ an den Objekten.

Die Daten rechnen vom Stichtag des Testhofs aus (``aufbau.STICHTAG``), damit gleicher
Seed gleiche Fristen ergibt:

- eine überfällige Frist (zehn Tage vor dem Stichtag),
- eine Frist am Stichtag,
- eine Frist in zehn Tagen mit 14 Tagen Vorlauf (also schon fällig),
- eine Frist in 60 Tagen (noch nicht fällig),
- eine erledigte Frist.

Hinweise sind feste Texte ohne Personendaten. Geschrieben wird über
``apps.fristen.services``.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from apps.fristen import services
from apps.fristen.models import Frist, Fristverschiebung
from testhof import register
from testhof.aufbau import Kontext
from testhof.kern import KernDaten

NAME = "fristen"


@dataclass(frozen=True)
class FristenDaten:
    ueberfaellig: Frist
    heute: Frist
    mit_vorlauf: Frist
    spaeter: Frist
    erledigt: Frist

    @property
    def alle(self) -> list[Frist]:
        return [self.ueberfaellig, self.heute, self.mit_vorlauf, self.spaeter, self.erledigt]


def aufbauen(kontext: Kontext) -> FristenDaten:
    kern: KernDaten = kontext.ablage["kern"]
    hof, halle, feld = kern.objekte["HOF"], kern.objekte["HALLE"], kern.objekte["FELD"]
    tag = kontext.stichtag

    def am(tage: int) -> datetime.date:
        return tag + datetime.timedelta(days=tage)

    hand = Frist.Quelle.HAND
    ueberfaellig = services.frist_anlegen(
        Frist.Art.SONSTIGE, am(-10), hand, hof, hinweis="Dachrinne der Garagenzeile prüfen"
    )
    heute = services.frist_anlegen(
        Frist.Art.ABRECHNUNGSFRIST, am(0), hand, halle, hinweis="Nebenkosten der Halle abrechnen"
    )
    mit_vorlauf = services.frist_anlegen(
        Frist.Art.SPERRFRIST, am(10), hand, feld, vorlauf_tage=14, hinweis="Gehölzschnitt"
    )
    spaeter = services.frist_anlegen(Frist.Art.SONSTIGE, am(60), hand, halle)
    erledigt = services.frist_anlegen(
        Frist.Art.KUENDIGUNG, am(-30), hand, hof, hinweis="Kündigung Stellplatz prüfen"
    )
    # Erledigt-Datum vor dem Stichtag des Testhofs, damit es nicht von „heute“ abhängt.
    erledigt = services.frist_erledigen(erledigt, am=am(-31))
    return FristenDaten(
        ueberfaellig=ueberfaellig,
        heute=heute,
        mit_vorlauf=mit_vorlauf,
        spaeter=spaeter,
        erledigt=erledigt,
    )


def leeren() -> None:
    """Löscht Verschiebungen und Fristen (nur erfundene Daten)."""
    Fristverschiebung.objects.all().delete()
    Frist.objects.all().delete()


register.registrieren(NAME, aufbauen=aufbauen, leeren=leeren, abhaengig_von=("kern",))
