"""Testhof-Modul des Kassenbuchs: 35 Kategorien, 60 Zuordnungsregeln, zwei Sonderfälle.

Die Regeln passen zu den Umsätzen aus ``testhof/bank.py``, mit Absicht nicht zu allen. Nach
dem Lauf ``kassenbuch_einordnen`` ist jeder Umsatz gebucht oder steht in der Arbeitsliste:

- Mieten (45 Garagen und Lager, Regel je Gegen-IBAN), Wohnraummiete, Pachten (eine Regel mit
  Regex), Entnahmen und fast alle Ausgaben werden eindeutig gebucht.
- „Material Baumarkt“ auf Vivid hat keine Regel: Arbeitsposten (je Monat einer).
- „Versicherungsbeitrag“ auf VR passt auf zwei Regeln mit gleicher Priorität (allgemeine
  Versicherungsregel und Sammelregel des VR-Kontos): mehrdeutig, Arbeitsposten.
- Die Rückbuchung einer Miete (Ausgang von der IBAN eines Mieters) passt auf keine Regel:
  Arbeitsposten mit dem Hinweis auf den zurückgebuchten Eingang.

Sonderfälle (``testhof.sonderfaelle``):

- ``sammelrechnung_zwei_objekte``: Die Versicherung im Januar 2026 auf Vivid ist schon auf
  HOF und HALLE aufgeteilt (zwei Buchungen, Summe gleich dem Umsatz).
- ``wohnraummiete_selbes_konto``: Die Miete der Wohnung W-01 auf Vivid wird mit der
  Kategorie „Mieteinnahmen Wohnraum“ gebucht. Die Kategorie und jede Buchung tragen
  ``wohnraum=True``; der Abgleich (Schritt 6) lässt sie aus.

Alles entsteht über ``apps.kassenbuch.services``.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from decimal import Decimal

from apps.bank.models import Bankkonto, Bankumsatz
from apps.kassenbuch import services
from apps.kassenbuch.models import (
    Buchung,
    Kategorie,
    Monatsabschluss,
    Monatsbericht,
    Zuordnungsregel,
)
from apps.kern.models import Arbeitsposten, Mandant, Objekt
from testhof import register
from testhof.aufbau import Kontext
from testhof.bank import AUSGABEN

NAME = "kassenbuch"

E = Kategorie.Art.EINNAHME
A = Kategorie.Art.AUSGABE
U = Kategorie.Art.UMBUCHUNG

#: Name, Art, umlagefähiger Anteil in Prozent (Vorgabe).
KATEGORIEN: tuple[tuple[str, str, int], ...] = (
    ("Mieteinnahmen Garagen", E, 0),
    ("Mieteinnahmen Stellplätze", E, 0),
    ("Mieteinnahmen Lager und Hallen", E, 0),
    ("Mieteinnahmen Wohnraum", E, 0),
    ("Pachteinnahmen", E, 0),
    ("Nebenkostenvorauszahlungen", E, 0),
    ("Erstattungen", E, 0),
    ("Zinserträge", E, 0),
    ("Sonstige Einnahmen", E, 0),
    ("Strom", A, 50),
    ("Wasser und Abwasser", A, 100),
    ("Versicherungen", A, 100),
    ("Grundbesitzabgaben", A, 100),
    ("Reparaturen und Instandhaltung", A, 0),
    ("Winterdienst", A, 100),
    ("Müllabfuhr", A, 100),
    ("Material", A, 0),
    ("Kontoführung", A, 0),
    ("Schornsteinfeger", A, 100),
    ("Gartenpflege", A, 100),
    ("Heizung und Brennstoff", A, 100),
    ("Hausmeister", A, 100),
    ("Steuerberatung", A, 0),
    ("Rechtsberatung", A, 0),
    ("Porto und Versand", A, 0),
    ("Telefon und Internet", A, 0),
    ("Darlehenszinsen", A, 0),
    ("Sonstige Ausgaben", A, 0),
    ("Umbuchung zwischen Konten", U, 0),
    ("Kautionen (durchlaufend)", U, 0),
    ("Darlehenstilgung", U, 0),
)
WOHNRAUM = "Mieteinnahmen Wohnraum"
#: Entnahmen (B9): eine Kategorie je Mandant, benannt nach dem Kürzel (ohne Namen).
ENTNAHME_PRAEFIX = "Entnahmen "

#: Ausgabe in ``testhof.bank.AUSGABEN`` -> Kategorie
AUSGABE_KATEGORIE = {
    "Stromabschlag": "Strom",
    "Wasser und Abwasser": "Wasser und Abwasser",
    "Versicherungsbeitrag": "Versicherungen",
    "Grundbesitzabgaben": "Grundbesitzabgaben",
    "Reparatur Tor": "Reparaturen und Instandhaltung",
    "Winterdienst": "Winterdienst",
    "Müllabfuhr": "Müllabfuhr",
    "Material Baumarkt": "Material",
    "Kontoführung": "Kontoführung",
    "Schornsteinfeger": "Schornsteinfeger",
}
#: Für diese Ausgabe auf Vivid gibt es absichtlich keine Regel (Arbeitsposten).
OHNE_REGEL_VIVID = "Material Baumarkt"
#: Garagen bis zu dieser Einheit gehören zu HOF, die übrigen (Lager) zu HALLE.
LETZTE_GARAGE = 30
#: Monat der Sammelrechnung (Versicherung auf Vivid, aufgeteilt auf HOF und HALLE).
MONAT_SAMMELRECHNUNG = (2026, 1)
ANTEIL_HOF = Decimal("0.6")

PRIO_ALLGEMEIN = 10
PRIO_GEGENPARTEI = 20


@dataclass
class KassenbuchDaten:
    kategorien: dict[str, Kategorie]
    regeln: list[Zuordnungsregel]
    #: Umsatz der Sammelrechnung und seine zwei Buchungen (HOF, HALLE)
    sammelrechnung: tuple[Bankumsatz, list[Buchung]]
    wohnraum_regel: Zuordnungsregel


def _kategorien(mandanten: dict[str, Mandant]) -> dict[str, Kategorie]:
    kategorien = {
        name: services.kategorie_anlegen(
            name, art, wohnraum=name == WOHNRAUM, umlagefaehiger_anteil=anteil
        )
        for name, art, anteil in KATEGORIEN
    }
    for mandant in sorted(mandanten.values(), key=lambda m: m.kuerzel):
        name = ENTNAHME_PRAEFIX + mandant.kuerzel
        kategorien[name] = services.kategorie_anlegen(name, Kategorie.Art.ENTNAHME, mandant=mandant)
    return kategorien


def _ausgabe(konto: Bankkonto, art: str) -> Bankumsatz:
    umsatz = (
        konto.umsaetze.filter(
            richtung=Bankumsatz.Richtung.AUSGANG, verwendungszweck__startswith=f"{art} "
        )
        .order_by("buchungsdatum", "pk")
        .first()
    )
    if umsatz is None:
        raise LookupError(f"Testhof: Ausgabe {art} auf Konto #{konto.pk} fehlt.")
    return umsatz


def _mieter(konto: Bankkonto) -> dict[str, str]:
    """Einheit -> Gegen-IBAN der Mieter auf Vivid (aus dem Zweck „Miete MM/JJJJ G-NN“)."""
    muster = re.compile(r"^Miete \d{2}/\d{4} (G-\d{2})$")
    ergebnis: dict[str, str] = {}
    for zweck, iban in (
        konto.umsaetze.filter(richtung=Bankumsatz.Richtung.EINGANG)
        .order_by("buchungsdatum", "pk")
        .values_list("verwendungszweck", "gegen_iban")
    ):
        treffer = muster.match(zweck)
        if treffer:
            ergebnis.setdefault(treffer.group(1), iban)
    return dict(sorted(ergebnis.items()))


def _regeln(
    kategorien: dict[str, Kategorie],
    objekte: dict[str, Objekt],
    mandanten: dict[str, Mandant],
    konten: dict[str, Bankkonto],
    wohnraum_iban: str,
    entnahme_iban: str,
) -> tuple[list[Zuordnungsregel], Zuordnungsregel]:
    eingang, ausgang = Bankumsatz.Richtung.EINGANG, Bankumsatz.Richtung.AUSGANG
    vivid, vr = konten["vivid"], konten["vr"]
    regeln: list[Zuordnungsregel] = []

    # 45 Mieten, je Gegen-IBAN eine Regel. Bezeichnung nur mit der Einheit.
    for einheit, iban in _mieter(vivid).items():
        garage = int(einheit[2:]) <= LETZTE_GARAGE
        regeln.append(
            services.regel_anlegen(
                f"Miete {einheit}",
                gegen_iban=iban,
                richtung=eingang,
                kategorie=kategorien[
                    "Mieteinnahmen Garagen" if garage else "Mieteinnahmen Lager und Hallen"
                ],
                objekt=objekte["HOF" if garage else "HALLE"],
            )
        )
    wohnraum = services.regel_anlegen(
        "Miete Wohnung W-01",
        gegen_iban=wohnraum_iban,
        richtung=eingang,
        kategorie=kategorien[WOHNRAUM],
        objekt=objekte["HOF"],
    )
    regeln.append(wohnraum)
    regeln.append(
        services.regel_anlegen(
            "Pachten VR",
            konto=vr,
            richtung=eingang,
            zweck=r"^Pacht \d{2}/\d{4} F-\d{2}$",
            zweck_regex=True,
            kategorie=kategorien["Pachteinnahmen"],
            objekt=objekte["FELD"],
        )
    )
    regeln.append(
        services.regel_anlegen(
            "Entnahme GbR",
            gegen_iban=entnahme_iban,
            richtung=ausgang,
            kategorie=kategorien[ENTNAHME_PRAEFIX + mandanten["gbr"].kuerzel],
            objekt=objekte["HOF"],
            mandant=mandanten["gbr"],
        )
    )
    # Ausgaben auf Vivid je Gegen-IBAN, außer Material (bleibt Handarbeit).
    for art in AUSGABEN:
        if art == OHNE_REGEL_VIVID:
            continue
        regeln.append(
            services.regel_anlegen(
                f"Vivid {art}",
                gegen_iban=_ausgabe(vivid, art).gegen_iban,
                richtung=ausgang,
                kategorie=kategorien[AUSGABE_KATEGORIE[art]],
                objekt=objekte["HOF"],
                prioritaet=PRIO_GEGENPARTEI,
            )
        )
    # Allgemeine Regel nach Zweck und Sammelregel für VR: Bei der VR-Versicherung passen
    # beide mit gleicher Priorität (mehrdeutig, Arbeitsposten).
    regeln.append(
        services.regel_anlegen(
            "Versicherung (Zweck)",
            zweck="Versicherung",
            richtung=ausgang,
            kategorie=kategorien["Versicherungen"],
            objekt=objekte["HOF"],
            prioritaet=PRIO_ALLGEMEIN,
        )
    )
    regeln.append(
        services.regel_anlegen(
            "VR Ausgaben bis 600 Euro",
            konto=vr,
            richtung=ausgang,
            betrag_bis=Decimal("600.00"),
            kategorie=kategorien["Sonstige Ausgaben"],
            objekt=objekte["FELD"],
            prioritaet=PRIO_ALLGEMEIN,
        )
    )
    regeln.append(
        services.regel_anlegen(
            "VR Stromabschlag",
            gegen_iban=_ausgabe(vr, "Stromabschlag").gegen_iban,
            richtung=ausgang,
            kategorie=kategorien["Strom"],
            objekt=objekte["FELD"],
            prioritaet=PRIO_GEGENPARTEI,
        )
    )
    return regeln, wohnraum


def _sammelrechnung(
    vivid: Bankkonto, kategorien: dict[str, Kategorie], objekte: dict[str, Objekt]
) -> tuple[Bankumsatz, list[Buchung]]:
    jahr, monat = MONAT_SAMMELRECHNUNG
    umsatz = (
        vivid.umsaetze.filter(
            richtung=Bankumsatz.Richtung.AUSGANG,
            verwendungszweck__startswith="Versicherungsbeitrag ",
            buchungsdatum__gte=datetime.date(jahr, monat, 1),
        )
        .order_by("buchungsdatum", "pk")
        .first()
    )
    if umsatz is None:
        raise LookupError("Testhof: Versicherung für die Sammelrechnung fehlt.")
    hof = (umsatz.betrag * ANTEIL_HOF).quantize(services.CENT)
    teile = [
        services.Teil(betrag=hof, kategorie=kategorien["Versicherungen"], objekt=objekte["HOF"]),
        services.Teil(
            betrag=umsatz.betrag - hof,
            kategorie=kategorien["Versicherungen"],
            objekt=objekte["HALLE"],
        ),
    ]
    return umsatz, services.umsatz_buchen(umsatz, teile)


def aufbauen(kontext: Kontext) -> KassenbuchDaten:
    kern = kontext.ablage["kern"]
    bank = kontext.ablage["bank"]
    kategorien = _kategorien(kern.mandanten)
    regeln, wohnraum = _regeln(
        kategorien,
        kern.objekte,
        kern.mandanten,
        bank.konten,
        bank.wohnraum_iban,
        bank.entnahme_iban,
    )
    return KassenbuchDaten(
        kategorien=kategorien,
        regeln=regeln,
        sammelrechnung=_sammelrechnung(bank.konten["vivid"], kategorien, kern.objekte),
        wohnraum_regel=wohnraum,
    )


def leeren() -> None:
    """Löscht alle Daten des Kassenbuchs (nur erfundene Daten, siehe ``aufbau.leeren``)."""
    for bericht in Monatsbericht.objects.exclude(datei=""):
        bericht.datei.delete(save=False)
    Monatsbericht.objects.all().delete()
    Arbeitsposten.objects.filter(art__in=[services.ART_EINORDNEN, services.ART_ABSCHLUSS]).delete()
    Monatsabschluss.objects.all().delete()
    Buchung.objects.filter(storno_von__isnull=False).delete()
    Buchung.objects.all().delete()
    Zuordnungsregel.objects.all().delete()
    Kategorie.objects.all().delete()


register.registrieren(
    NAME,
    abhaengig_von=("kern", "bank"),
    aufbauen=aufbauen,
    leeren=leeren,
    sonderfaelle=("sammelrechnung_zwei_objekte", "wohnraummiete_selbes_konto"),
)
