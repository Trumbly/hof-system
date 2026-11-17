"""Import vorhandener Zähler und Stände (Schritt 8, Aufgabe 7).

- Spaltenzuordnung und Werte-Übersetzung stehen in ``import_spalten.toml``.
- Standard ist der Probelauf: alles wird gelesen, geprüft und in einer Transaktion
  angelegt, die am Ende zurückrollt. Erst ``ausfuehren=True`` behält die Änderungen.
- Geschrieben wird über ``zaehler.services``; dieselben Prüfungen wie am Handy gelten
  (ein kleinerer Stand als der letzte wird abgelehnt).
- Der Bericht nennt nur Zähler (Anzahlen), Abweichungsarten, Zählernummern, NW-IDs und
  Zeilennummern, nie Vermerke (CLAUDE.md, Abschnitt 1).
"""

from __future__ import annotations

import csv
import datetime
import functools
import tomllib
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from django.db import transaction

from apps.kern.models import Objekt
from apps.stammdaten.importe import Bericht, ImportFehler
from apps.stammdaten.models import Einheit
from apps.zaehler import services
from apps.zaehler.models import Zaehler

SPALTEN_DATEI = Path(__file__).with_name("import_spalten.toml")


@functools.cache
def zuordnung(name: str) -> dict[str, Any]:
    with SPALTEN_DATEI.open("rb") as datei:
        daten = tomllib.load(datei)
    if name not in daten:
        raise ImportFehler(f"Abschnitt [{name}] fehlt in {SPALTEN_DATEI.name}")
    abschnitt: dict[str, Any] = daten[name]
    return abschnitt


def _lesen(pfad: Path, abschnitt: dict[str, Any], pflicht: tuple[str, ...]) -> list[dict[str, str]]:
    spalten: dict[str, str] = abschnitt["spalten"]
    with pfad.open(encoding="utf-8-sig", newline="") as datei:
        leser = csv.DictReader(datei, delimiter=abschnitt.get("trennzeichen", ";"))
        koepfe = set(leser.fieldnames or [])
        fehlend = [spalten[f] for f in pflicht if spalten[f] not in koepfe]
        if fehlend:
            raise ImportFehler(f"{pfad.name}: Spalten fehlen: {', '.join(fehlend)}")
        return [
            {feld: (zeile.get(kopf) or "").strip() for feld, kopf in spalten.items()}
            for zeile in leser
        ]


def _datum(text: str, abschnitt: dict[str, Any]) -> datetime.date | None:
    if not text:
        return None
    try:
        return datetime.datetime.strptime(text, abschnitt["datumsformat"]).date()
    except ValueError:
        return None


def _stand(text: str, abschnitt: dict[str, Any]) -> Decimal | None:
    if not text:
        return None
    sauber = text.replace(" ", "")
    if abschnitt.get("dezimaltrennzeichen", ",") == ",":
        sauber = sauber.replace(".", "").replace(",", ".")
    try:
        return services.stand_lesen(sauber)
    except services.ZaehlerFehler:
        return None


def zaehler_importieren(
    pfad_zaehler: Path,
    pfad_staende: Path | None = None,
    *,
    ausfuehren: bool = False,
) -> Bericht:
    """Zähler (Nummer, Art, Einheit oder Objekt, Einbau, Eichfrist) und optional ihre Stände.

    Vorhandene Zählernummern werden nicht geändert, nur gemeldet (mit Abweichungen).
    Stände werden je Zähler nach Datum sortiert erfasst; einen Stand am selben Tag gibt es
    nur einmal.
    """
    abschnitt = zuordnung("zaehler")
    zeilen = _lesen(pfad_zaehler, abschnitt, ("nummer", "art"))
    staende_abschnitt = zuordnung("zaehlerstaende")
    staende = (
        _lesen(pfad_staende, staende_abschnitt, ("nummer", "datum", "stand"))
        if pfad_staende is not None
        else []
    )
    objekte = {o.kuerzel.upper(): o for o in Objekt.objects.all()}

    with transaction.atomic():
        bericht = Bericht("Import Zähler", ausfuehren, zeilen=len(zeilen) + len(staende))
        for nummer, zeile in enumerate(zeilen, start=2):
            _zaehler_importieren(zeile, nummer, abschnitt, objekte, bericht)
        _staende_importieren(staende, staende_abschnitt, bericht)
        if not ausfuehren:
            transaction.set_rollback(True)
    return bericht


def _zaehler_importieren(
    zeile: dict[str, str],
    zeilennummer: int,
    abschnitt: dict[str, Any],
    objekte: dict[str, Objekt],
    bericht: Bericht,
) -> None:
    nummer = zeile["nummer"]
    kennung = nummer or f"Zeile {zeilennummer}"
    art = abschnitt["arten"].get(zeile["art"])
    if not nummer:
        bericht.abweichung("nummer_fehlt", kennung, uebersprungen=True)
        return
    if art is None:
        bericht.abweichung("art_unbekannt", kennung, uebersprungen=True)
        return
    einheit = None
    objekt = None
    if zeile.get("einheit"):
        einheit = Einheit.objects.filter(nw_id=zeile["einheit"].upper()).first()
        if einheit is None:
            bericht.abweichung("einheit_unbekannt", kennung, uebersprungen=True)
            return
    elif zeile.get("objekt"):
        objekt = objekte.get(zeile["objekt"].upper())
        if objekt is None:
            bericht.abweichung("objekt_unbekannt", kennung, uebersprungen=True)
            return
    else:
        bericht.abweichung("ort_fehlt", kennung, uebersprungen=True)
        return

    vorhanden = Zaehler.objects.filter(nummer=nummer).first()
    if vorhanden is not None:
        bericht.merken("vorhanden", nummer)
        if vorhanden.art != art:
            bericht.abweichung("art_abweichend", nummer)
        if (vorhanden.einheit_id, vorhanden.objekt_id) != (
            einheit.pk if einheit else None,
            objekt.pk if objekt else None,
        ):
            bericht.abweichung("ort_abweichend", nummer)
        return

    einbau = _datum(zeile.get("einbaudatum", ""), abschnitt)
    eichfrist = _datum(zeile.get("eichfrist_bis", ""), abschnitt)
    einbau_stand = _stand(zeile.get("einbau_stand", ""), abschnitt)
    if zeile.get("einbau_stand") and einbau_stand is None:
        bericht.abweichung("einbaustand_unlesbar", nummer)
    try:
        services.zaehler_anlegen(
            nummer,
            art,
            einheit=einheit,
            objekt=objekt,
            bezeichnung=zeile.get("bezeichnung", ""),
            einbaudatum=einbau,
            eichfrist_bis=eichfrist,
            einbau_stand=einbau_stand if einbau is not None else None,
        )
    except services.ZaehlerFehler:
        bericht.abweichung("ungueltig", kennung, uebersprungen=True)
        return
    bericht.merken("neu", nummer)
    if einbau is None:
        bericht.abweichung("einbaudatum_fehlt", nummer)
    if eichfrist is None:
        bericht.abweichung("eichfrist_fehlt", nummer)


def _staende_importieren(
    zeilen: list[dict[str, str]],
    abschnitt: dict[str, Any],
    bericht: Bericht,
) -> None:
    je_zaehler: dict[str, list[tuple[datetime.date, int, dict[str, str]]]] = defaultdict(list)
    for zeilennummer, zeile in enumerate(zeilen, start=2):
        kennung = f"Zeile {zeilennummer}"
        datum = _datum(zeile["datum"], abschnitt)
        if datum is None:
            bericht.abweichung("stand_datum_unlesbar", kennung, uebersprungen=True)
            continue
        je_zaehler[zeile["nummer"]].append((datum, zeilennummer, zeile))

    for nummer in sorted(je_zaehler):
        zaehler = Zaehler.objects.filter(nummer=nummer).first() if nummer else None
        for datum, zeilennummer, zeile in sorted(je_zaehler[nummer], key=lambda e: e[:2]):
            kennung = f"Zeile {zeilennummer}"
            if zaehler is None:
                bericht.abweichung("stand_zaehler_unbekannt", kennung, uebersprungen=True)
                continue
            stand = _stand(zeile["stand"], abschnitt)
            art = abschnitt["arten"].get(zeile.get("art") or "abgelesen")
            if stand is None or art is None:
                bericht.abweichung("stand_unlesbar", kennung, uebersprungen=True)
                continue
            if zaehler.staende.filter(datum=datum).exists():
                bericht.merken("stand_vorhanden", nummer)
                continue
            try:
                services.stand_erfassen(
                    zaehler, datum, stand, art=art, vermerk=zeile.get("vermerk", "")
                )
            except services.StandUnplausibel:
                bericht.abweichung("stand_unplausibel", kennung, uebersprungen=True)
                continue
            except services.ZaehlerFehler:
                bericht.abweichung("stand_ungueltig", kennung, uebersprungen=True)
                continue
            bericht.zaehler["staende_neu"] += 1
            if art == "geschaetzt":
                bericht.zaehler["staende_geschaetzt"] += 1
