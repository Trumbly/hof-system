"""Testhof-Modul der Bank: 2 Konten, 24 Monate, rund 1.800 Umsätze, Salden je Tag.

- **Vivid Geschäftskonto** (Enable Banking, Mandant GbR): je Monat 45 Mieten von
  erfundenen Zahlern und 10 Ausgaben.
- **VR Girokonto** (Zulieferung vom hof-mac, Mandant Nachlass): je Monat 12 Pachten und
  8 Ausgaben.
- Zeitraum: die 24 Monate vor ``aufbau.STICHTAG`` (1.11.2024 bis 31.10.2026).
- Kontostand zum Ende jedes Tages, passend zu den Umsätzen: Die Saldo-Prüfung findet im
  Testhof keine Abweichung.
- Bankfreigaben: Vivid läuft 10 Tage nach dem Stichtag ab (die Prüfung legt am Stichtag
  einen Arbeitsposten an), VR erst nach 60 Tagen.

Sonderfälle (``testhof.sonderfaelle``):

- ``doppelter_umsatz``: Die Umsätze kommen in Monatslieferungen, die jeweils die letzten
  fünf Tage des Vormonats noch einmal enthalten (wie ein erneuter Abruf). Der
  Fingerabdruck lässt jeden nur einmal ins System; ``BankDaten.doppelt_geliefert`` zählt
  die abgewiesenen. Dazu zwei *echt* gleiche Zahlungen desselben Zahlers am selben Tag
  (``gleiche_zahlungen``), die beide bleiben müssen (laufende Nummer 1 und 2).
- ``rueckbuchung``: Eine eingegangene Miete wird drei Tage später zurückgebucht
  (``rueckbuchung``: Eingang und Ausgang).

Für das Kassenbuch (``testhof/kassenbuch.py``) liefert Vivid außerdem je Monat

- die Miete einer Wohnung (W-01, geführt in Verwaltio) auf demselben Konto: Sonderfall
  „Wohnraummiete auf demselben Konto“ (``BankDaten.wohnraum_iban``),
- eine Entnahme an einen Gesellschafter der GbR (``BankDaten.entnahme_iban``).

Beide kommen aus einem eigenen Zufall, damit die übrigen Daten gleich bleiben.

Für den Abgleich (``testhof/abgleich.py``, Schritt 6) zahlen die 45 Mieter ab Juli 2026
nicht mehr ihren Zufallsbetrag, sondern das Soll ihres Vertrags, samt den Sonderfällen des
Abgleichs. Der Zufall der übrigen Umsätze bleibt unberührt (der Tag wird weiter gezogen);
das Modul hängt dafür von der Sollstellung ab.

Alles entsteht über ``apps.bank.services``; alle Namen, IBANs und Zwecke sind erfunden.
"""

from __future__ import annotations

import calendar
import datetime
import random
from dataclasses import dataclass
from decimal import Decimal

from faker import Faker

from apps.bank import services
from apps.bank.models import (
    Bankabruf,
    Bankfreigabe,
    Bankkonto,
    Bankumsatz,
    Kontostand,
)
from testhof import erfunden, register
from testhof.aufbau import Kontext

NAME = "bank"
MONATE = 24
UEBERLAPPUNG_TAGE = 5

EINGANG = Bankumsatz.Richtung.EINGANG
AUSGANG = Bankumsatz.Richtung.AUSGANG

#: Monat der Sonderfälle (Jahr, Monat).
MONAT_RUECKBUCHUNG = (2026, 3)
MONAT_GLEICHE_ZAHLUNGEN = (2025, 6)
#: Ab diesem Monat zahlen die Mieter auf Vivid das Soll ihres Vertrags (Abgleich, Schritt 6).
ABGLEICH_AB = (2026, 7)


@dataclass(frozen=True)
class KontoPlan:
    schluessel: str
    bank: str
    bezeichnung: str
    zugang: str
    mandant: str
    eb_bank_name: str
    anfangssaldo: Decimal
    zahler: int  # regelmäßige Eingänge je Monat
    ausgaben: int  # Ausgaben je Monat
    zweck: str  # Art der Eingänge im Verwendungszweck
    freigabe_tage: int  # Ablauf der Freigabe nach dem Stichtag


PLAENE = (
    KontoPlan(
        "vivid",
        "Vivid",
        "Vivid Geschäftskonto",
        Bankkonto.Zugang.ENABLE_BANKING,
        "gbr",
        "Vivid Money",
        Decimal("8000.00"),
        45,
        10,
        "Miete",
        10,
    ),
    KontoPlan(
        "vr",
        "VR",
        "VR Girokonto",
        Bankkonto.Zugang.ZULIEFERUNG,
        "nachlass",
        "",
        Decimal("3000.00"),
        12,
        8,
        "Pacht",
        60,
    ),
)

AUSGABEN = (
    "Stromabschlag",
    "Wasser und Abwasser",
    "Versicherungsbeitrag",
    "Grundbesitzabgaben",
    "Reparatur Tor",
    "Winterdienst",
    "Müllabfuhr",
    "Material Baumarkt",
    "Kontoführung",
    "Schornsteinfeger",
)


@dataclass(frozen=True)
class Zahler:
    name: str
    iban: str
    betrag: Decimal
    einheit: str


@dataclass
class BankDaten:
    konten: dict[str, Bankkonto]  # nach Schlüssel: "vivid", "vr"
    freigaben: dict[str, Bankfreigabe]
    beginn: datetime.date
    ende: datetime.date
    #: Umsätze, die eine Lieferung erneut brachte und der Fingerabdruck abwies.
    doppelt_geliefert: int
    #: Zwei echt gleiche Zahlungen am selben Tag (beide gespeichert).
    gleiche_zahlungen: tuple[Bankumsatz, Bankumsatz]
    #: Eingang und Rückbuchung (Ausgang) derselben Zahlung.
    rueckbuchung: tuple[Bankumsatz, Bankumsatz]
    #: Gegen-IBAN der Wohnraummiete auf dem Vivid-Konto (Verwaltio, nie im Abgleich).
    wohnraum_iban: str = ""
    #: Gegen-IBAN der monatlichen Entnahme an einen Gesellschafter der GbR.
    entnahme_iban: str = ""


def _monate(beginn: datetime.date) -> list[tuple[int, int]]:
    jahr, monat = beginn.year, beginn.month
    ergebnis = []
    for _ in range(MONATE):
        ergebnis.append((jahr, monat))
        monat += 1
        if monat == 13:
            jahr, monat = jahr + 1, 1
    return ergebnis


def _tag(jahr: int, monat: int, tag: int) -> datetime.date:
    return datetime.date(jahr, monat, min(tag, calendar.monthrange(jahr, monat)[1]))


def _zahler(kontext: Kontext, plan: KontoPlan) -> list[Zahler]:
    f, z = kontext.faker, kontext.zufall
    praefix = "G" if plan.zweck == "Miete" else "F"
    return [
        Zahler(
            name=f"{f.first_name()} {f.last_name()}",
            iban=erfunden.erfundene_iban(z),
            betrag=Decimal(z.randrange(4000, 45001, 500)) / 100,
            einheit=f"{praefix}-{nummer:02d}",
        )
        for nummer in range(1, plan.zahler + 1)
    ]


@dataclass(frozen=True)
class Zusatz:
    """Wohnraummiete und Entnahme auf dem Vivid-Konto, aus eigenem Zufall."""

    wohnraum: Zahler
    gesellschafter: Zahler


def _zusatz(kontext: Kontext) -> Zusatz:
    z = random.Random(f"testhof:{kontext.seed}:bank:zusatz")
    f = Faker("de_DE")
    f.seed_instance(f"testhof:{kontext.seed}:bank:zusatz:faker")
    return Zusatz(
        wohnraum=Zahler(
            name=f"{f.first_name()} {f.last_name()}",
            iban=erfunden.erfundene_iban(z),
            betrag=Decimal(z.randrange(60000, 110001, 500)) / 100,
            einheit="W-01",
        ),
        gesellschafter=Zahler(
            name=f"{f.first_name()} {f.last_name()}",
            iban=erfunden.erfundene_iban(z),
            betrag=Decimal(z.randrange(100000, 200001, 10000)) / 100,
            einheit="",
        ),
    )


def _vivid_zusatz(zusatz: Zusatz, jahr: int, monat: int) -> list[services.UmsatzDaten]:
    miete = _tag(jahr, monat, 3)
    entnahme = _tag(jahr, monat, 15)
    w, g = zusatz.wohnraum, zusatz.gesellschafter
    return [
        services.UmsatzDaten(
            buchungsdatum=miete,
            valuta=miete,
            betrag=w.betrag,
            richtung=EINGANG,
            gegenpartei=w.name,
            gegen_iban=w.iban,
            verwendungszweck=f"Miete Wohnung {w.einheit} {monat:02d}/{jahr}",
            bank_referenz=f"TH-vivid-{jahr}{monat:02d}-{w.einheit}",
        ),
        services.UmsatzDaten(
            buchungsdatum=entnahme,
            valuta=entnahme,
            betrag=g.betrag,
            richtung=AUSGANG,
            gegenpartei=g.name,
            gegen_iban=g.iban,
            verwendungszweck=f"Entnahme {monat:02d}/{jahr}",
            bank_referenz=f"TH-vivid-{jahr}{monat:02d}-E",
        ),
    ]


def _umsaetze(
    kontext: Kontext, plan: KontoPlan, monate: list[tuple[int, int]]
) -> dict[tuple[int, int], list[services.UmsatzDaten]]:
    """Alle Umsätze eines Kontos, je Monat, sortiert nach Tag."""
    z = kontext.zufall
    zahler = _zahler(kontext, plan)
    zusatz = _zusatz(kontext) if plan.schluessel == "vivid" else None
    firmen = [(kontext.faker.company(), erfunden.erfundene_iban(z)) for _ in AUSGABEN]
    je_monat: dict[tuple[int, int], list[services.UmsatzDaten]] = {}
    for jahr, monat in monate:
        liste: list[services.UmsatzDaten] = []
        ab_soll = plan.schluessel == "vivid" and (jahr, monat) >= ABGLEICH_AB
        for person in zahler:
            datum = _tag(jahr, monat, z.randint(1, 5))
            if ab_soll:
                continue  # ab Juli 2026 zahlen die Mieter ihr Soll (testhof/abgleich.py)
            daten = services.UmsatzDaten(
                buchungsdatum=datum,
                valuta=datum,
                betrag=person.betrag,
                richtung=EINGANG,
                gegenpartei=person.name,
                gegen_iban=person.iban,
                verwendungszweck=f"{plan.zweck} {monat:02d}/{jahr} {person.einheit}",
                bank_referenz=f"TH-{plan.schluessel}-{jahr}{monat:02d}-{person.einheit}",
            )
            liste.append(daten)
            vivid = plan.schluessel == "vivid"
            if vivid and (jahr, monat) == MONAT_GLEICHE_ZAHLUNGEN and person is zahler[0]:
                # Sonderfall: dieselbe Überweisung zweimal am selben Tag.
                liste.append(daten)
            if vivid and (jahr, monat) == MONAT_RUECKBUCHUNG and person is zahler[1]:
                # Sonderfall: Die Bank bucht die Zahlung drei Tage später zurück.
                zurueck = datum + datetime.timedelta(days=3)
                liste.append(
                    services.UmsatzDaten(
                        buchungsdatum=zurueck,
                        valuta=zurueck,
                        betrag=person.betrag,
                        richtung=AUSGANG,
                        gegenpartei=person.name,
                        gegen_iban=person.iban,
                        verwendungszweck=f"Rückbuchung {daten.verwendungszweck}",
                        bank_referenz=f"{daten.bank_referenz}-RB",
                    )
                )
        for nummer in range(plan.ausgaben):
            art = AUSGABEN[nummer % len(AUSGABEN)]
            firma, iban = firmen[nummer % len(firmen)]
            datum = _tag(jahr, monat, z.randint(1, 28))
            liste.append(
                services.UmsatzDaten(
                    buchungsdatum=datum,
                    valuta=datum,
                    betrag=Decimal(z.randrange(1000, 60000)) / 100,
                    richtung=AUSGANG,
                    gegenpartei=firma,
                    gegen_iban=iban,
                    verwendungszweck=f"{art} {monat:02d}/{jahr} Rechnung {z.randint(1000, 9999)}",
                    bank_referenz=f"TH-{plan.schluessel}-{jahr}{monat:02d}-A{nummer:02d}",
                )
            )
        if zusatz is not None:
            liste += _vivid_zusatz(zusatz, jahr, monat)
        if ab_soll:
            from testhof import abgleich

            liste += abgleich.bankzahlungen(kontext.seed, zahler, jahr, monat)
        # Stabile Sortierung: gleiche Tage behalten die Reihenfolge der Erzeugung.
        je_monat[(jahr, monat)] = sorted(liste, key=lambda u: u.buchungsdatum)
    return je_monat


def _tagessalden(
    plan: KontoPlan,
    umsaetze: list[services.UmsatzDaten],
    beginn: datetime.date,
    ende: datetime.date,
) -> dict[datetime.date, Decimal]:
    summen: dict[datetime.date, Decimal] = {}
    for u in umsaetze:
        vorzeichen = 1 if u.richtung == EINGANG else -1
        summen[u.buchungsdatum] = summen.get(u.buchungsdatum, Decimal("0")) + vorzeichen * u.betrag
    salden = {}
    saldo = plan.anfangssaldo
    tag = beginn - datetime.timedelta(days=1)
    salden[tag] = saldo
    while tag < ende:
        tag += datetime.timedelta(days=1)
        saldo += summen.get(tag, Decimal("0"))
        salden[tag] = saldo
    return salden


def aufbauen(kontext: Kontext) -> BankDaten:
    mandanten = kontext.ablage["kern"].mandanten
    stichtag = kontext.stichtag
    ende = stichtag - datetime.timedelta(days=1)
    beginn = _tag(stichtag.year - MONATE // 12, stichtag.month, 1)
    monate = _monate(beginn)

    konten: dict[str, Bankkonto] = {}
    freigaben: dict[str, Bankfreigabe] = {}
    doppelt = 0
    for plan in PLAENE:
        konto = services.bankkonto_anlegen(
            mandanten[plan.mandant],
            bank=plan.bank,
            bezeichnung=plan.bezeichnung,
            iban=erfunden.erfundene_iban(kontext.zufall),
            zugang=plan.zugang,
            eb_bank_name=plan.eb_bank_name,
        )
        konten[plan.schluessel] = konto
        je_monat = _umsaetze(kontext, plan, monate)
        alle: list[services.UmsatzDaten] = []
        vormonat: list[services.UmsatzDaten] = []
        quelle = (
            Bankabruf.Quelle.ENABLE_BANKING
            if plan.zugang == Bankkonto.Zugang.ENABLE_BANKING
            else Bankabruf.Quelle.ZULIEFERUNG
        )
        for jahr, monat in monate:
            erster = datetime.date(jahr, monat, 1)
            von = erster - datetime.timedelta(days=UEBERLAPPUNG_TAGE)
            # Sonderfall „doppelter Umsatz“: Die letzten Tage des Vormonats kommen erneut.
            lieferung = [u for u in vormonat if u.buchungsdatum >= von] + je_monat[(jahr, monat)]
            abruf = services.bankabruf_starten(
                konto, quelle, von=max(von, beginn), bis=_tag(jahr, monat, 31)
            )
            ergebnis = services.umsaetze_uebernehmen(konto, lieferung, abruf=abruf)
            services.bankabruf_beenden(
                abruf, meldung=f"Testhof: {ergebnis.neu} neu, {ergebnis.doppelt} doppelt"
            )
            doppelt += ergebnis.doppelt
            alle += je_monat[(jahr, monat)]
            vormonat = je_monat[(jahr, monat)]
        services.kontostaende_erfassen(konto, _tagessalden(plan, alle, beginn, ende))
        freigaben[plan.schluessel] = services.bankfreigabe_anlegen(
            konto,
            gueltig_ab=stichtag - datetime.timedelta(days=170),
            gueltig_bis=stichtag + datetime.timedelta(days=plan.freigabe_tage),
            sitzung_id=f"testhof-{plan.schluessel}" if plan.eb_bank_name else "",
            eb_konto_uid=f"testhof-{plan.schluessel}-uid" if plan.eb_bank_name else None,
        )

    vivid = konten["vivid"]
    jahr, monat = MONAT_GLEICHE_ZAHLUNGEN
    zweite = vivid.umsaetze.get(
        buchungsdatum__year=jahr, buchungsdatum__month=monat, laufende_nummer=2
    )
    zurueck = vivid.umsaetze.get(richtung=AUSGANG, verwendungszweck__startswith="Rückbuchung ")
    eingang = vivid.umsaetze.get(
        richtung=EINGANG,
        gegen_iban=zurueck.gegen_iban,
        verwendungszweck=zurueck.verwendungszweck.removeprefix("Rückbuchung "),
    )
    return BankDaten(
        konten=konten,
        freigaben=freigaben,
        beginn=beginn,
        ende=ende,
        doppelt_geliefert=doppelt,
        gleiche_zahlungen=(_gleicher_erster(zweite), zweite),
        rueckbuchung=(eingang, zurueck),
        wohnraum_iban=_zusatz(kontext).wohnraum.iban,
        entnahme_iban=_zusatz(kontext).gesellschafter.iban,
    )


def _gleicher_erster(zweiter: Bankumsatz) -> Bankumsatz:
    return Bankumsatz.objects.get(
        konto=zweiter.konto,
        buchungsdatum=zweiter.buchungsdatum,
        betrag=zweiter.betrag,
        richtung=zweiter.richtung,
        gegen_iban=zweiter.gegen_iban,
        verwendungszweck=zweiter.verwendungszweck,
        laufende_nummer=1,
    )


def leeren() -> None:
    """Löscht alle Daten der Bank (nur erfundene Daten, siehe ``aufbau.leeren``)."""
    Kontostand.objects.all().delete()
    Bankumsatz.objects.all().delete()
    Bankabruf.objects.all().delete()
    Bankfreigabe.objects.all().delete()
    Bankkonto.objects.all().delete()


register.registrieren(
    NAME,
    abhaengig_von=("kern", "soll"),
    aufbauen=aufbauen,
    leeren=leeren,
    sonderfaelle=("doppelter_umsatz", "rueckbuchung"),
)
