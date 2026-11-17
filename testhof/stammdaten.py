"""Testhof-Modul der Stammdaten (Schritt 4): 60 Einheiten, 45 Verträge, 50 Parteien.

- **Einheiten:** HOF mit 20 Garagen, 12 Stellplätzen und 6 Wohnungen (nur NW-ID, geführt in
  Verwaltio), HALLE mit 4 Hallen und 8 Lagern, FELD mit 6 Gärten und 4 Außenflächen.
- **Parteien:** 43 Mieter (davon einer mit drei Verträgen, einige Firmen), 4 abweichende
  Zahler, 3 Bürgen. Namen und Anschriften von Faker (``de_DE``), Mails auf ``.invalid``,
  Telefonnummern aus dem fiktiven Bereich (``testhof.erfunden``).
- **Verträge:** 45, davon 43 aktiv und 2 beendet.
- **Datenstand:** bewusst mit Lücken (Fläche, Zielmiete, Kontaktweg, Kündigungsfrist,
  Zahlweise, Kautionseingang, Schlüssel; kein Vertrag hat einen Scan).
- **Sonderfälle** (``testhof.sonderfaelle``):
  ``vertrag_monatsmitte`` (Beginn am 15., befristetes Ende am 15.),
  ``staffelmiete`` (Stufe am 01.01.2026, im Testzeitraum),
  ``kuendigung_rueckgabe_kaution`` (Kündigung, Rückgabe und Kautionsabrechnung über die
  Services, wie im Standardablauf 4). Die Zählerstände der Rückgaben erfasst das
  Testhof-Modul ``zaehler``.

Alle Daten rechnen von festen Daten aus (``aufbau.STICHTAG``), nie von ``heute``.
Geschrieben wird über ``apps.stammdaten.services``.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType

from apps.fristen.models import Frist, Fristverschiebung
from apps.kern.models import Arbeitsposten, Objekt
from apps.soll import services as soll
from apps.stammdaten import schreiben, services
from apps.stammdaten.models import (
    Checkliste,
    Checkpunkt,
    Einheit,
    EinheitStatus,
    Kaution,
    Kondition,
    Kontaktweg,
    Kuendigung,
    Partei,
    Schluessel,
    Uebergabe,
    UebergabeFoto,
    Vertrag,
    Vertragseinheit,
    Vertragspartei,
    Zielmiete,
)
from testhof import erfunden, register
from testhof.aufbau import Kontext

NAME = "stammdaten"
SONDERFAELLE = ("vertrag_monatsmitte", "staffelmiete", "kuendigung_rueckgabe_kaution")

#: Ab wann Zielmieten gelten.
BASIS = datetime.date(2023, 1, 1)
KONTO = "Vivid Kautionen"

#: (Objekt, Typ, Anzahl, Bezeichnung, Fläche von, Fläche bis)
EINHEITEN: tuple[tuple[str, str, int, str, int, int], ...] = (
    ("HOF", Einheit.Typ.GARAGE, 20, "Garage {n}", 15, 22),
    ("HOF", Einheit.Typ.STELLPLATZ, 12, "Stellplatz {n}", 12, 13),
    ("HOF", Einheit.Typ.WOHNUNG, 6, "Wohnung {n}", 0, 0),
    ("HALLE", Einheit.Typ.HALLE, 4, "Halle {n}", 200, 500),
    ("HALLE", Einheit.Typ.LAGER, 8, "Lager {n}", 20, 60),
    ("FELD", Einheit.Typ.GARTEN, 6, "Garten {n}", 150, 600),
    ("FELD", Einheit.Typ.AUSSENFLAECHE, 4, "Außenfläche {n}", 500, 2000),
)

#: Zielmiete je Monat: fester Betrag (Garage, Stellplatz) oder Euro je m².
ZIELMIETE: dict[str, tuple[str, Decimal, Decimal]] = {
    Einheit.Typ.GARAGE: ("fest", Decimal("70"), Decimal("95")),
    Einheit.Typ.STELLPLATZ: ("fest", Decimal("30"), Decimal("40")),
    Einheit.Typ.LAGER: ("qm", Decimal("7"), Decimal("9")),
    Einheit.Typ.HALLE: ("qm", Decimal("4"), Decimal("5")),
    Einheit.Typ.GARTEN: ("qm", Decimal("0.25"), Decimal("0.40")),
    Einheit.Typ.AUSSENFLAECHE: ("qm", Decimal("0.10"), Decimal("0.20")),
}

MIT_SCHLUESSELN = (Einheit.Typ.GARAGE, Einheit.Typ.LAGER, Einheit.Typ.HALLE)

ANZAHL_MIETER = 43
ANZAHL_ZAHLER = 4
ANZAHL_BUERGEN = 3
ANZAHL_VERTRAEGE = 45


@dataclass
class StammdatenDaten:
    einheiten: dict[str, Einheit]  # nach NW-ID
    parteien: list[Partei]
    vertraege: dict[str, Vertrag]  # nach Nummer
    #: Sonderfall-Schlüssel -> beteiligte Verträge
    sonderfaelle: dict[str, list[Vertrag]] = field(default_factory=dict)

    def frei(self) -> list[Einheit]:
        """Einheiten, die am Stichtag frei sind (ohne Wohnraum)."""
        from testhof.aufbau import STICHTAG

        return [
            e
            for e in self.einheiten.values()
            if not e.ist_wohnraum
            and (s := e.status_am(STICHTAG)) is not None
            and s.status == EinheitStatus.Status.FREI
        ]

    def aktive_vertraege(self) -> list[Vertrag]:
        return [v for v in self.vertraege.values() if v.status == Vertrag.Status.AKTIV]


def _geld(wert: Decimal) -> Decimal:
    return wert.quantize(Decimal("0.01"))


def _einheiten(kontext: Kontext, objekte: dict[str, Objekt]) -> list[Einheit]:
    z = kontext.zufall
    ergebnis = []
    ohne_flaeche = {3, 17, 33, 48}  # Lücken für den Datenstand
    ohne_zielmiete = {5, 41}
    laufend = 0
    for kuerzel, typ, anzahl, muster, von, bis in EINHEITEN:
        for n in range(1, anzahl + 1):
            laufend += 1
            if typ == Einheit.Typ.WOHNUNG:
                ergebnis.append(
                    services.einheit_anlegen(objekte[kuerzel], typ, bezeichnung=muster.format(n=n))
                )
                continue
            flaeche = _geld(Decimal(z.uniform(von, bis))) if von != bis else Decimal(von)
            art, mi, ma = ZIELMIETE[typ]
            satz = Decimal(str(round(z.uniform(float(mi), float(ma)), 2)))
            zielmiete = _geld(satz if art == "fest" else satz * flaeche)
            ergebnis.append(
                services.einheit_anlegen(
                    objekte[kuerzel],
                    typ,
                    bezeichnung=muster.format(n=n),
                    flaeche_qm=None if laufend in ohne_flaeche else _geld(flaeche),
                    vermarktung=Einheit.Vermarktung.WEBSITE,
                    zielmiete=None if laufend in ohne_zielmiete else zielmiete,
                    zielmiete_ab=BASIS,
                    zielmiete_begruendung="Marktvergleich Testhof",
                )
            )
    return ergebnis


def _partei(kontext: Kontext, nummer: int, firma: bool) -> Partei:
    f, z = kontext.faker, kontext.zufall
    luecke_anschrift = nummer in (7, 29)
    felder = {
        "strasse": "" if luecke_anschrift else f"{f.street_name()} {f.building_number()}",
        "plz": f.postcode(),
        "ort": f.city(),
    }
    if firma:
        name = f.company()
        partei = services.partei_anlegen(Partei.Art.FIRMA, firma=name, **felder)
        adresse = erfunden.email(z, name.split()[0], domain="firma")
    else:
        vorname, nachname = f.first_name(), f.last_name()
        partei = services.partei_anlegen(
            Partei.Art.PERSON,
            anrede=z.choice(("Frau", "Herr", "")),
            vorname=vorname,
            nachname=nachname,
            **felder,
        )
        adresse = erfunden.email(z, vorname, nachname)
    if nummer not in (11, 23, 37):  # Lücken für den Datenstand
        services.kontaktweg_hinzufuegen(partei, Kontaktweg.Art.EMAIL, adresse, bevorzugt=True)
        if z.random() < 0.6:
            services.kontaktweg_hinzufuegen(
                partei, Kontaktweg.Art.MOBIL, erfunden.telefon(z, mobil=True)
            )
    return partei


def _parteien(kontext: Kontext) -> list[Partei]:
    anzahl = ANZAHL_MIETER + ANZAHL_ZAHLER + ANZAHL_BUERGEN
    firmen = {4, 12, 19, 26, 35, 43, 44}
    return [_partei(kontext, n, n in firmen) for n in range(anzahl)]


def _beginn(kontext: Kontext) -> datetime.date:
    """Vertragsbeginn am Monatsersten zwischen 2019 und Mitte 2026."""
    monat = kontext.zufall.randrange(0, 7 * 12 + 6)
    return datetime.date(2019 + monat // 12, monat % 12 + 1, 1)


def _miete(einheit: Einheit) -> Decimal:
    ziel = einheit.zielmiete_am(BASIS)
    if ziel is not None:
        return _geld(ziel.betrag * Decimal("0.95"))
    return Decimal("60.00")


def _schluessel(kontext: Kontext, einheit: Einheit, mieter: Partei | None, nummer: int) -> None:
    if einheit.typ not in MIT_SCHLUESSELN or nummer in (2, 9, 30):  # Lücken
        return
    art = (
        Schluessel.Art.HANDSENDER
        if einheit.typ == Einheit.Typ.GARAGE
        else Schluessel.Art.SCHLUESSEL
    )
    if nummer in (14, 21):
        services.schluessel_anlegen(einheit, art=art, anzahl=2)  # wer sie hat: unbekannt
        return
    if mieter is None:
        services.schluessel_anlegen(einheit, art=art, anzahl=2, inhaber=Schluessel.Inhaber.HOF)
        return
    services.schluessel_anlegen(
        einheit, art=art, anzahl=2, inhaber=Schluessel.Inhaber.PARTEI, partei=mieter
    )
    if kontext.zufall.random() < 0.5:
        services.schluessel_anlegen(einheit, art=art, anzahl=1, inhaber=Schluessel.Inhaber.HOF)


def aufbauen(kontext: Kontext) -> StammdatenDaten:
    # Der Testhof legt keine Schreiben an (Dateinamen und PDFs hingen sonst von Datum und
    # IDs ab); Kündigungsbestätigung, Protokolle und Kautionsabrechnung werden Arbeitsposten.
    # Das Soll stellt das Modul testhof.soll danach zum festen Stichtag (nicht zu „heute“).
    with (
        schreiben.ohne_schreiben("Testhof legt keine Schreiben an"),
        soll.ohne_sollstellung("Testhof stellt das Soll in testhof.soll"),
    ):
        return _aufbauen(kontext)


def _aufbauen(kontext: Kontext) -> StammdatenDaten:
    objekte: dict[str, Objekt] = kontext.ablage["kern"].objekte
    z = kontext.zufall
    einheiten = _einheiten(kontext, objekte)
    parteien = _parteien(kontext)
    mieter = parteien[:ANZAHL_MIETER]
    zahler = parteien[ANZAHL_MIETER : ANZAHL_MIETER + ANZAHL_ZAHLER]
    buergen = parteien[ANZAHL_MIETER + ANZAHL_ZAHLER :]

    vermietbar = [e for e in einheiten if not e.ist_wohnraum]
    z.shuffle(vermietbar)
    # Der Sonderfall Kündigung (Index 3) ist eine Garage: Frist nach § 580a Abs. 1 Nr. 3 BGB.
    garage = next(i for i, e in enumerate(vermietbar) if e.typ == Einheit.Typ.GARAGE)
    vermietbar[3], vermietbar[garage] = vermietbar[garage], vermietbar[3]
    vermietet = vermietbar[:ANZAHL_VERTRAEGE]
    frei = vermietbar[ANZAHL_VERTRAEGE:]
    # Mieter 0 hat drei Verträge (Sammelzahler ab Schritt 6), alle anderen einen.
    mieter_je_vertrag = [mieter[0], mieter[0], *mieter]

    vertraege: list[Vertrag] = []
    sonderfaelle: dict[str, list[Vertrag]] = {s: [] for s in SONDERFAELLE}
    ohne_regel = {6, 18, 27, 33, 40}
    ohne_zahlweise = {8, 22, 39}
    ohne_kautionseingang = {10, 25, 31, 42}

    for i, einheit in enumerate(vermietet):
        partei = mieter_je_vertrag[i]
        beginn = _beginn(kontext)
        ende = None
        if i == 1:  # Sonderfall: Beginn mitten im Monat
            beginn = datetime.date(2025, 3, 15)
        if i == 2:  # Sonderfall: befristet, Ende mitten im Monat
            beginn, ende = datetime.date(2024, 9, 1), datetime.date(2026, 6, 15)
        if i == 3:  # Sonderfall: Kündigung, Rückgabe, Kautionsabrechnung
            beginn = datetime.date(2023, 5, 1)
        miete = _miete(einheit)
        zahlweise = Kondition.Zahlweise.JAEHRLICH if i == 5 else Kondition.Zahlweise.MONATLICH
        if i in ohne_zahlweise:
            zahlweise = Kondition.Zahlweise.UNBEKANNT
        kaution = Decimal("0.00") if einheit.typ == Einheit.Typ.STELLPLATZ else _geld(miete * 2)
        if i == 4:  # Sonderfall: Staffel mit einer Stufe im Testzeitraum
            beginn = datetime.date(2024, 1, 1)
        vertrag = services.vertrag_anlegen(
            einheiten=[einheit],
            mieter=[partei],
            beginn=beginn,
            ende=ende,
            miete=miete,
            zahlweise=zahlweise,
            kaution=kaution,
            kuendigungsregel="" if i in ohne_regel else None,
            zahler=[zahler[i % ANZAHL_ZAHLER]] if i in (7, 15, 24, 36) else (),
            buergen=[buergen[i % ANZAHL_BUERGEN]] if i in (12, 20, 28) else (),
            status=Vertrag.Status.AKTIV,
        )
        if i == 4:
            services.kondition_ab(
                vertrag,
                datetime.date(2026, 1, 1),
                miete=_geld(miete * Decimal("1.05")),
                grund="Staffel 2",
            )
            services.kondition_ab(
                vertrag,
                datetime.date(2027, 1, 1),
                miete=_geld(miete * Decimal("1.10")),
                grund="Staffel 3",
            )
            sonderfaelle["staffelmiete"].append(vertrag)
        if kaution and i not in ohne_kautionseingang:
            services.kaution_eingang_erfassen(
                vertrag.kaution, beginn - datetime.timedelta(days=z.randrange(3, 20)), KONTO
            )
        services.vertrag_unterschrieben(vertrag, beginn - datetime.timedelta(days=7))
        _schluessel(kontext, einheit, partei, i)
        vertraege.append(vertrag)

    for n, einheit in enumerate(frei):
        seit = datetime.date(2025, 1, 1) + datetime.timedelta(days=z.randrange(0, 600))
        services.einheit_status_setzen(einheit, EinheitStatus.Status.FREI, seit, "Leerstand")
        _schluessel(kontext, einheit, None, 50 + n)

    # Sonderfall „Vertragsbeginn und Vertragsende mitten im Monat“
    befristet = vertraege[2]
    sonderfaelle["vertrag_monatsmitte"] += [vertraege[1], befristet]
    rueckgabe = services.uebergabe_beginnen(
        befristet, Uebergabe.Art.RUECKGABE, datetime.date(2026, 6, 15)
    )
    services.uebergabe_speichern(
        rueckgabe,
        schluessel=[
            {"schluessel": s.pk, "anzahl": s.anzahl}
            for s in services.schluessel_der_uebergabe(rueckgabe)
        ],
        zustand="besenrein",
    )
    services.uebergabe_abschliessen(rueckgabe)

    # Sonderfall „Kündigung, Rückgabe und Kautionsabrechnung“ (Standardablauf 4)
    gekuendigt = vertraege[3]
    services.kuendigung_erfassen(gekuendigt, datetime.date(2026, 7, 2))
    rueckgabe = services.uebergabe_beginnen(
        gekuendigt, Uebergabe.Art.RUECKGABE, datetime.date(2026, 9, 30)
    )
    services.uebergabe_speichern(
        rueckgabe,
        schluessel=[
            {"schluessel": s.pk, "anzahl": s.anzahl}
            for s in services.schluessel_der_uebergabe(rueckgabe)
        ],
        zustand="Kratzer am Tor",
    )
    services.uebergabe_abschliessen(rueckgabe)
    gekuendigt.refresh_from_db()
    if gekuendigt.kaution.betrag:
        services.kaution_abrechnen(
            gekuendigt.kaution, datetime.date(2026, 10, 20), Decimal("50.00"), "Torreparatur"
        )
    sonderfaelle["kuendigung_rueckgabe_kaution"].append(gekuendigt)

    for v in vertraege:
        v.refresh_from_db()
    return StammdatenDaten(
        einheiten={e.nw_id: e for e in einheiten},
        parteien=parteien,
        vertraege={v.nummer: v for v in vertraege},
        sonderfaelle=sonderfaelle,
    )


def leeren() -> None:
    """Löscht die Stammdaten des Testhofs samt ihrer Arbeitsposten (nur erfundene Daten)."""
    typen = ContentType.objects.filter(app_label="stammdaten")
    Arbeitsposten.objects.filter(bezug_typ__in=typen).delete()
    fristen = Frist.objects.filter(bezug_typ__in=typen)
    Arbeitsposten.objects.filter(
        bezug_typ=ContentType.objects.get_for_model(Frist), bezug_id__in=fristen.values("pk")
    ).delete()
    Fristverschiebung.objects.filter(frist__in=fristen).delete()
    fristen.delete()
    for modell in (
        UebergabeFoto,
        Uebergabe,
        Kuendigung,
        Checkpunkt,
        Checkliste,
        Schluessel,
        Kaution,
        Kondition,
        Vertragspartei,
        Vertragseinheit,
        Vertrag,
        Kontaktweg,
        Partei,
        Zielmiete,
        EinheitStatus,
        Einheit,
    ):
        modell.objects.all().delete()


register.registrieren(
    NAME,
    abhaengig_von=("kern",),
    aufbauen=aufbauen,
    leeren=leeren,
    sonderfaelle=SONDERFAELLE,
)
