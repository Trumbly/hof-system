"""Testhof-Modul des Abgleichs (Schritt 6): Mietzahlungen passend zum Soll, Zahlerregeln und
die Sonderfälle des Abgleichs.

**Zeitraum.** Der Abgleich beginnt im Testhof am 01.07.2026 (``BEGINN``; im Betrieb
``HOF_ABGLEICH_BEGINN``). Ab Juli zahlen die 45 Mieter des Vivid-Kontos aus
``testhof/bank.py`` nicht mehr ihren erfundenen Zufallsbetrag, sondern das Soll ihres
Vertrags: Zahler *k* (IBAN der Regel „Miete G-*k+1*“) zahlt für den Vertrag mit Index *k*
(nach Nummer). Die Zahlungen entstehen in den Monatslieferungen der Bank
(``bankzahlungen``, eigener Zufall), damit Salden und Abrufe stimmen und die übrigen
Bankdaten gleich bleiben. Die Regeln des Kassenbuchs bleiben dieselben 60; dieses Modul
hängt an sie die Zahlerregeln. Die Kategorie im Kassenbuch (Garagen oder Lager) folgt der
alten Zuordnung des Zahlers, nicht der Einheit des Vertrags: im Testhof bewusst so gelassen.

**Sonderfälle** (``testhof.sonderfaelle``), Index = Vertrag nach Nummer:

- ``sammelzahler``: Zahler 0 zahlt mit einer Überweisung für die Verträge 0, 1 und 6.
- ``fremder_zahler``: Für Vertrag 7 überweist die abweichende Zahlerpartei des Vertrags.
- ``falsche_einheit_im_verwendungszweck``: Im September nennt der Zweck von Vertrag 9 die
  NW-ID der Einheit von Vertrag 10 (Klärfall, nie gebucht).
- ``ueberzahlung_guthaben``: Vertrag 11 zahlt im August 20 € zu viel (Guthaben, verrechnet
  mit dem Folgemonat).
- ``barzahlung``: Vertrag 13 zahlt die Septembermiete bar (Handzahlung mit Quittung als
  Beleg in der Ablage, kein Bankumsatz).
- ``teilzahlung_nachzahlung``: Vertrag 14 zahlt im August 60 %, im September den Rest und
  die Septembermiete.
- ``dauerauftrag_alte_miete``: Vertrag 4 (Staffel ab 01.01.2026) zahlt per Dauerauftrag die
  alte Miete (Teilzahlung, der Rest bleibt offen).
- ``rueckbuchung``: Die Augustmiete von Vertrag 16 bucht die Bank drei Tage später zurück
  („Rücklastschrift“; Klärfall).
- ``jahreszahler``: Vertrag 5 (jährlich) zahlt im Juli die Jahresmiete 2026.
- ``wohnraummiete_selbes_konto``: Die Wohnraummiete aus ``testhof/bank.py`` bleibt außen vor.

Pachten auf dem VR-Konto haben im Hof-System noch keinen Vertrag: Ihre Regel ist „nicht
vertragsbezogen“.

Geschrieben wird über ``apps.abgleich.services``.
"""

from __future__ import annotations

import datetime
import random
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

from django.contrib.contenttypes.models import ContentType

from apps.abgleich import services
from apps.abgleich.models import (
    Guthaben,
    Handzahlung,
    Klaerfall,
    Sammelvertrag,
    Zahlerregel,
    Zuordnung,
)
from apps.bank import services as bank_services
from apps.bank.models import Bankumsatz
from apps.dokumente.models import Dokument
from apps.kassenbuch.models import Zuordnungsregel
from apps.kern.models import Arbeitsposten
from apps.soll.models import Sollposten
from apps.stammdaten.models import Partei, Vertrag, Vertragspartei
from testhof import pdfs, register
from testhof.aufbau import Kontext

if TYPE_CHECKING:
    from testhof.bank import Zahler

NAME = "abgleich"
SONDERFAELLE = (
    "sammelzahler",
    "fremder_zahler",
    "falsche_einheit_im_verwendungszweck",
    "ueberzahlung_guthaben",
    "barzahlung",
    "teilzahlung_nachzahlung",
    "dauerauftrag_alte_miete",
)
#: Sonderfälle, die andere Module anmelden und dieses Modul im Abgleich mit Daten füllt.
AUCH_GENUTZT = ("rueckbuchung", "jahreszahler", "wohnraummiete_selbes_konto")

#: Erster Monat, in dem die Mieter das Soll zahlen; zugleich Beginn des Abgleichs.
ABGLEICH_AB = (2026, 7)
BEGINN = datetime.date(2026, 7, 1)

SAMMEL = (0, 1, 6)
#: Diese Verträge zahlen nicht selbst: im Sammel enthalten oder vor Juli beendet.
OHNE_EIGENE_ZAHLUNG = frozenset({1, 2, 6})
DAUERAUFTRAG = 4
JAHRESZAHLER = 5
FREMDER_ZAHLER = 7
FALSCHER_ZWECK, FALSCHE_EINHEIT_VON, MONAT_FALSCHER_ZWECK = 9, 10, (2026, 9)
UEBERZAHLUNG, MONAT_UEBERZAHLUNG, MEHR = 11, (2026, 8), Decimal("20.00")
BARZAHLUNG, MONAT_BARZAHLUNG, TAG_BARZAHLUNG = 13, (2026, 9), datetime.date(2026, 9, 4)
TEILZAHLUNG, MONAT_TEILZAHLUNG, ANTEIL = 14, (2026, 8), Decimal("0.6")
RUECKBUCHUNG, MONAT_RUECKBUCHUNG = 16, (2026, 8)
PACHTREGEL = "Pachten VR"

CENT = Decimal("0.01")
EINGANG = Bankumsatz.Richtung.EINGANG
AUSGANG = Bankumsatz.Richtung.AUSGANG


def _vertraege() -> list[Vertrag]:
    return list(Vertrag.objects.prefetch_related("einheiten").order_by("nummer"))


def _monatsposten(vertrag: Vertrag, jahr: int, monat: int) -> list[Sollposten]:
    return list(
        Sollposten.objects.filter(
            vertrag=vertrag,
            art__in=(Sollposten.Art.MIETE, Sollposten.Art.PAUSCHALE),
            von__year=jahr,
            von__month=monat,
        )
        .exclude(status=Sollposten.Status.STORNIERT)
        .order_by("von", "art")
    )


def _monatsbetrag(vertrag: Vertrag, jahr: int, monat: int) -> Decimal:
    return sum((p.betrag for p in _monatsposten(vertrag, jahr, monat)), Decimal("0.00"))


def _nw(vertrag: Vertrag) -> str:
    return ", ".join(e.nw_id for e in vertrag.einheiten.all())


def _alte_miete(vertrag: Vertrag) -> Decimal:
    """Monatsbetrag der Kondition vor der Staffel (Dauerauftrag mit alter Miete)."""
    kondition = vertrag.kondition_am(datetime.date(2025, 12, 31))
    if kondition is None:
        return Decimal("0.00")
    netto = kondition.miete + kondition.pauschale
    return (netto + netto * kondition.steuersatz / 100).quantize(CENT)


def _zahlerpartei(vertrag: Vertrag) -> str:
    vp = (
        Vertragspartei.objects.filter(vertrag=vertrag, rolle=Vertragspartei.Rolle.ZAHLER)
        .select_related("partei")
        .first()
    )
    if vp is None:
        return ""
    partei: Partei = vp.partei
    return partei.firma or f"{partei.vorname} {partei.nachname}".strip()


def bankzahlungen(
    seed: int, zahler: list[Zahler], jahr: int, monat: int
) -> list[bank_services.UmsatzDaten]:
    """Mietzahlungen eines Monats ab Juli 2026 auf Vivid, passend zum Soll (für
    ``testhof/bank.py``). Eigener Zufall je Monat; ändert keine anderen Bankdaten."""
    if (jahr, monat) < ABGLEICH_AB:
        return []
    from testhof.bank import _tag

    z = random.Random(f"testhof:{seed}:abgleich:zahlungen:{jahr}-{monat:02d}")
    vertraege = _vertraege()[: len(zahler)]
    liste: list[bank_services.UmsatzDaten] = []

    def zahlung(
        i: int, betrag: Decimal, zweck: str, tag: datetime.date, name: str = ""
    ) -> bank_services.UmsatzDaten:
        daten = bank_services.UmsatzDaten(
            buchungsdatum=tag,
            valuta=tag,
            betrag=betrag,
            richtung=EINGANG,
            gegenpartei=name or zahler[i].name,
            gegen_iban=zahler[i].iban,
            verwendungszweck=zweck,
            bank_referenz=f"TH-vivid-{jahr}{monat:02d}-AB{i:02d}",
        )
        liste.append(daten)
        return daten

    for i, vertrag in enumerate(vertraege):
        tag = _tag(jahr, monat, z.randint(1, 5))
        ohne_id = z.random() < 0.3
        if i in OHNE_EIGENE_ZAHLUNG or (i == BARZAHLUNG and (jahr, monat) == MONAT_BARZAHLUNG):
            continue
        betrag = _monatsbetrag(vertrag, jahr, monat)
        zweck = f"Miete {monat:02d}/{jahr}" + ("" if ohne_id else f" {_nw(vertrag)}")
        if i == SAMMEL[0]:
            teile = [vertraege[k] for k in SAMMEL]
            betrag = sum((_monatsbetrag(v, jahr, monat) for v in teile), Decimal("0.00"))
            zweck = f"Mieten {monat:02d}/{jahr} " + ", ".join(_nw(v) for v in teile)
        elif i == JAHRESZAHLER:
            if (jahr, monat) != ABGLEICH_AB:
                continue
            betrag = sum(
                (
                    p.betrag
                    for p in Sollposten.objects.filter(
                        vertrag=vertrag, von__year=jahr, art=Sollposten.Art.MIETE
                    ).exclude(status=Sollposten.Status.STORNIERT)
                ),
                Decimal("0.00"),
            )
            zweck = f"Jahresmiete {jahr} {_nw(vertrag)}"
        elif i == DAUERAUFTRAG:
            betrag = _alte_miete(vertrag)
            zweck = f"Dauerauftrag Miete {_nw(vertrag)}"
        elif i == FALSCHER_ZWECK and (jahr, monat) == MONAT_FALSCHER_ZWECK:
            zweck = f"Miete {monat:02d}/{jahr} {_nw(vertraege[FALSCHE_EINHEIT_VON])}"
        elif i == UEBERZAHLUNG and (jahr, monat) == MONAT_UEBERZAHLUNG:
            betrag += MEHR
        elif i == TEILZAHLUNG and (jahr, monat) == MONAT_TEILZAHLUNG:
            betrag = (betrag * ANTEIL).quantize(CENT)
        elif i == TEILZAHLUNG and (jahr, monat) > MONAT_TEILZAHLUNG:
            vorher = _monatsbetrag(vertrag, *MONAT_TEILZAHLUNG)
            if (jahr, monat) == (2026, 9):
                betrag += vorher - (vorher * ANTEIL).quantize(CENT)
        if betrag <= 0:
            continue
        name = _zahlerpartei(vertrag) if i == FREMDER_ZAHLER else ""
        daten = zahlung(i, betrag, zweck, tag, name)
        if i == RUECKBUCHUNG and (jahr, monat) == MONAT_RUECKBUCHUNG:
            zurueck = tag + datetime.timedelta(days=3)
            liste.append(
                bank_services.UmsatzDaten(
                    buchungsdatum=zurueck,
                    valuta=zurueck,
                    betrag=betrag,
                    richtung=AUSGANG,
                    gegenpartei=daten.gegenpartei,
                    gegen_iban=daten.gegen_iban,
                    verwendungszweck=f"Rücklastschrift {zweck}",
                    bank_referenz=f"{daten.bank_referenz}-RL",
                )
            )
    return liste


@dataclass
class AbgleichDaten:
    zahlerregeln: list[Zahlerregel]
    handzahlung: Handzahlung
    #: Sonderfall-Schlüssel -> beteiligte Verträge
    sonderfaelle: dict[str, list[Vertrag]] = field(default_factory=dict)
    beginn: datetime.date = BEGINN


def aufbauen(kontext: Kontext) -> AbgleichDaten:
    vertraege = _vertraege()
    regeln = {r.bezeichnung: r for r in Zuordnungsregel.objects.order_by("pk")}
    zahlerregeln = []
    for i, vertrag in enumerate(vertraege):
        regel = regeln.get(f"Miete G-{i + 1:02d}")
        if regel is None or i in OHNE_EIGENE_ZAHLUNG:
            continue
        if i == SAMMEL[0]:
            zahlerregeln.append(
                services.zahlerregel_setzen(
                    regel, vertraege=[vertraege[k] for k in SAMMEL], herkunft="testhof"
                )
            )
        else:
            zahlerregeln.append(
                services.zahlerregel_setzen(regel, vertrag=vertrag, herkunft="testhof")
            )
    zahlerregeln.append(
        services.zahlerregel_setzen(
            regeln[PACHTREGEL], nicht_vertragsbezogen=True, herkunft="testhof"
        )
    )

    # Sonderfall „Barzahlung“: Handzahlung mit Beleg auf die Septembermiete.
    bar = vertraege[BARZAHLUNG]
    posten = _monatsposten(bar, *MONAT_BARZAHLUNG)
    betrag = sum((p.betrag for p in posten), Decimal("0.00"))
    beleg = services.beleg_ablegen(
        pdfs.einfaches_pdf(
            ["Quittung über eine Barzahlung (erfunden)", f"Vertrag {bar.nummer}", "Miete 09/2026"]
        ),
        datum=TAG_BARZAHLUNG,
        herkunft=Dokument.Herkunft.TESTHOF,
    )
    handzahlung = services.handzahlung_erfassen(
        bar,
        Handzahlung.Art.BAR,
        datum=TAG_BARZAHLUNG,
        betrag=betrag,
        grund="Barzahlung der Septembermiete gegen Quittung (Testhof)",
        beleg=beleg,
        anteile=[services.Anteil(p, p.betrag) for p in posten],
    )
    return AbgleichDaten(
        zahlerregeln=zahlerregeln,
        handzahlung=handzahlung,
        sonderfaelle={
            "sammelzahler": [vertraege[k] for k in SAMMEL],
            "fremder_zahler": [vertraege[FREMDER_ZAHLER]],
            "falsche_einheit_im_verwendungszweck": [
                vertraege[FALSCHER_ZWECK],
                vertraege[FALSCHE_EINHEIT_VON],
            ],
            "ueberzahlung_guthaben": [vertraege[UEBERZAHLUNG]],
            "barzahlung": [bar],
            "teilzahlung_nachzahlung": [vertraege[TEILZAHLUNG]],
            "dauerauftrag_alte_miete": [vertraege[DAUERAUFTRAG]],
            "rueckbuchung": [vertraege[RUECKBUCHUNG]],
            "jahreszahler": [vertraege[JAHRESZAHLER]],
        },
    )


def leeren() -> None:
    """Löscht die Daten des Abgleichs (nur erfundene Daten, siehe ``aufbau.leeren``)."""
    Arbeitsposten.objects.filter(bezug_typ=ContentType.objects.get_for_model(Klaerfall)).delete()
    Zuordnung.objects.all().delete()
    while Guthaben.objects.exists():
        Guthaben.objects.filter(reste__isnull=True).delete()
    Klaerfall.objects.all().delete()
    Handzahlung.objects.all().delete()
    Sammelvertrag.objects.all().delete()
    Zahlerregel.objects.all().delete()


register.registrieren(
    NAME,
    abhaengig_von=("bank", "belege", "kassenbuch", "soll"),
    aufbauen=aufbauen,
    leeren=leeren,
    sonderfaelle=SONDERFAELLE,
)
