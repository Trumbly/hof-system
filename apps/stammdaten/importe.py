"""Importe der Stammdaten: Einheiten aus Notion, Parteien und Verträge aus HelloHousing.

- Spaltenzuordnung und Werte-Übersetzung stehen in ``import_spalten.toml``.
- Standard ist der Probelauf: alles wird gelesen, geprüft und in einer Transaktion
  angelegt, die am Ende zurückrollt. Erst ``ausfuehren=True`` behält die Änderungen.
- Geschrieben wird über ``stammdaten.services``.
- Der Bericht nennt nur Zähler, Abweichungsarten, NW-IDs, Vertragsnummern und
  Zeilennummern, nie Namen, Anschriften oder Beträge je Person (CLAUDE.md, Abschnitt 1).
"""

from __future__ import annotations

import csv
import datetime
import functools
import re
import tomllib
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from django.db import transaction

from apps.kern import zeit
from apps.kern.models import Objekt
from apps.stammdaten import services
from apps.stammdaten.models import Einheit, EinheitStatus, Kondition, Kontaktweg, Partei, Vertrag

SPALTEN_DATEI = Path(__file__).with_name("import_spalten.toml")
NW_ID_FORMAT = re.compile(r"NW-[A-Z]{1,2}-\d{4}")


class ImportFehler(Exception):
    """Die Datei passt nicht zur Spaltenzuordnung. Die Meldung nennt nur Spaltenköpfe."""


@functools.cache
def zuordnung(name: str) -> dict[str, Any]:
    with SPALTEN_DATEI.open("rb") as datei:
        daten = tomllib.load(datei)
    if name not in daten:
        raise ImportFehler(f"Abschnitt [{name}] fehlt in {SPALTEN_DATEI.name}")
    abschnitt: dict[str, Any] = daten[name]
    return abschnitt


@dataclass
class Bericht:
    """Ergebnis eines Imports, ohne Personendaten."""

    titel: str
    ausgefuehrt: bool
    zeilen: int = 0
    zaehler: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    #: Kennungen je Ergebnis, z. B. ``kennungen["neu"] = ["NW-G-0001", …]``.
    kennungen: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    #: Abweichungsart -> NW-IDs oder „Zeile n“.
    abweichungen: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    #: Abweichungsarten, bei denen die Zeile nicht übernommen wurde.
    uebersprungen: int = 0

    def merken(self, ergebnis: str, kennung: str) -> None:
        self.zaehler[ergebnis] += 1
        self.kennungen[ergebnis].append(kennung)

    def abweichung(self, art: str, kennung: str, *, uebersprungen: bool = False) -> None:
        self.abweichungen[art].append(kennung)
        if uebersprungen:
            self.uebersprungen += 1

    def text(self) -> str:
        modus = "AUSGEFÜHRT" if self.ausgefuehrt else "PROBELAUF, nichts geändert"
        zeilen = [f"{self.titel} – {modus}", f"Zeilen gelesen: {self.zeilen}"]
        for ergebnis, anzahl in sorted(self.zaehler.items()):
            liste = self.kennungen.get(ergebnis, [])
            zeilen.append(f"{ergebnis}: {anzahl}" + (f" ({', '.join(liste)})" if liste else ""))
        if self.abweichungen:
            zeilen.append("Abweichungen:")
            for art, kennungen in sorted(self.abweichungen.items()):
                zeilen.append(f"  {art}: {len(kennungen)} ({', '.join(kennungen)})")
        zeilen.append(f"übersprungen: {self.uebersprungen}")
        return "\n".join(zeilen)


# --- Lesen ---------------------------------------------------------------------------


def _lesen(pfad: Path, abschnitt: dict[str, Any], pflicht: tuple[str, ...]) -> list[dict[str, str]]:
    """Liest eine CSV-Datei und übersetzt die Spaltenköpfe in Feldnamen."""
    spalten: dict[str, str] = abschnitt["spalten"]
    with pfad.open(encoding="utf-8-sig", newline="") as datei:
        leser = csv.DictReader(datei, delimiter=abschnitt.get("trennzeichen", ","))
        koepfe = set(leser.fieldnames or [])
        fehlend = [spalten[f] for f in pflicht if spalten[f] not in koepfe]
        if fehlend:
            raise ImportFehler(f"{pfad.name}: Spalten fehlen: {', '.join(fehlend)}")
        return [
            {feld: (zeile.get(kopf) or "").strip() for feld, kopf in spalten.items()}
            for zeile in leser
        ]


def _betrag(text: str, abschnitt: dict[str, Any]) -> Decimal | None:
    if not text:
        return None
    trenner = abschnitt.get("dezimaltrennzeichen", ",")
    sauber = text.replace("€", "").replace(" ", "")
    if trenner == ",":
        sauber = sauber.replace(".", "").replace(",", ".")
    try:
        return Decimal(sauber).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def _datum(text: str, abschnitt: dict[str, Any]) -> datetime.date | None:
    if not text:
        return None
    try:
        return datetime.datetime.strptime(text, abschnitt["datumsformat"]).date()
    except ValueError:
        return None


def _probelauf(ausfuehren: bool, arbeit: Any) -> Bericht:
    """Führt ``arbeit()`` in einem Sicherungspunkt aus; im Probelauf rollt er zurück."""
    with transaction.atomic():
        bericht: Bericht = arbeit()
        if not ausfuehren:
            transaction.set_rollback(True)
    return bericht


# --- Einheiten aus Notion ------------------------------------------------------------


def einheiten_importieren(
    pfad: Path, *, ausfuehren: bool = False, stichtag: datetime.date | None = None
) -> Bericht:
    """Einheiten mit NW-ID, Typ, Objekt, Fläche, Status und Zielmiete (H1.1).

    Vorhandene NW-IDs werden nicht geändert, nur gemeldet. Ohne NW-ID vergibt das System
    eine. Status und Zielmiete gelten ab „Status seit“, sonst ab dem Stichtag.
    """
    abschnitt = zuordnung("notion_einheiten")
    zeilen = _lesen(pfad, abschnitt, ("typ", "objekt"))
    stichtag = stichtag or zeit.heute()
    objekte = {o.kuerzel.upper(): o for o in Objekt.objects.all()}

    def arbeit() -> Bericht:
        bericht = Bericht("Import Einheiten (Notion)", ausfuehren, zeilen=len(zeilen))
        for nummer, zeile in enumerate(zeilen, start=2):
            _einheit_importieren(zeile, nummer, abschnitt, objekte, stichtag, bericht)
        return bericht

    return _probelauf(ausfuehren, arbeit)


def _einheit_importieren(
    zeile: dict[str, str],
    nummer: int,
    abschnitt: dict[str, Any],
    objekte: dict[str, Objekt],
    stichtag: datetime.date,
    bericht: Bericht,
) -> None:
    nw_id = zeile.get("nw_id", "").upper()
    kennung = nw_id or f"Zeile {nummer}"
    typ = abschnitt["typen"].get(zeile["typ"])
    if typ is None:
        bericht.abweichung("typ_unbekannt", kennung, uebersprungen=True)
        return
    objekt = objekte.get(zeile["objekt"].upper())
    if objekt is None:
        bericht.abweichung("objekt_unbekannt", kennung, uebersprungen=True)
        return
    if nw_id:
        vorhanden = Einheit.objects.filter(nw_id=nw_id).first()
        if vorhanden is not None:
            bericht.merken("vorhanden", nw_id)
            if vorhanden.typ != typ:
                bericht.abweichung("typ_abweichend", nw_id)
            return
        if not NW_ID_FORMAT.fullmatch(nw_id):
            bericht.abweichung("nw_id_fremdes_format", nw_id)

    if typ == Einheit.Typ.WOHNUNG:
        einheit = services.einheit_anlegen(
            objekt, typ, nw_id=nw_id or None, bezeichnung=zeile.get("bezeichnung", "")
        )
        bericht.merken("wohnraum_nur_nw_id", einheit.nw_id)
        return

    flaeche = _betrag(zeile.get("flaeche", ""), abschnitt)
    status = abschnitt["status"].get(zeile.get("status", ""))
    zielmiete = _betrag(zeile.get("zielmiete", ""), abschnitt)
    ab = _datum(zeile.get("status_seit", ""), abschnitt) or stichtag
    try:
        einheit = services.einheit_anlegen(
            objekt,
            typ,
            nw_id=nw_id or None,
            bezeichnung=zeile.get("bezeichnung", ""),
            flaeche_qm=flaeche or None,
            website_slug=zeile.get("website_slug", ""),
            status=status,
            status_ab=ab if status else None,
            zielmiete=zielmiete,
            zielmiete_ab=ab if zielmiete is not None else None,
            zielmiete_begruendung="Import Notion" if zielmiete is not None else "",
        )
    except services.StammdatenFehler:
        bericht.abweichung("ungueltig", kennung, uebersprungen=True)
        return
    bericht.merken("neu", einheit.nw_id)
    if not nw_id:
        bericht.abweichung("nw_id_vergeben", einheit.nw_id)
    if not flaeche:
        bericht.abweichung("flaeche_fehlt", einheit.nw_id)
    if status is None:
        bericht.abweichung("status_unbekannt", einheit.nw_id)
    if zielmiete is None:
        bericht.abweichung("zielmiete_fehlt", einheit.nw_id)


# --- Parteien und Verträge aus HelloHousing ------------------------------------------


def _einheit_finden(text: str) -> Einheit | None:
    """Einheit nach NW-ID, sonst nach Bezeichnung (genau eine, ohne Groß/Klein)."""
    if not text:
        return None
    treffer = Einheit.objects.filter(nw_id=text.upper()).first()
    if treffer is not None:
        return treffer
    kandidaten = list(Einheit.objects.filter(bezeichnung__iexact=text)[:2])
    return kandidaten[0] if len(kandidaten) == 1 else None


def _mietverlauf(
    pfad: Path | None,
) -> tuple[dict[str, list[tuple[datetime.date, Decimal, Decimal | None]]], int]:
    """Mietverlauf je Mietverhältnis (sortiert) und Zahl der unlesbaren Zeilen."""
    if pfad is None:
        return {}, 0
    abschnitt = zuordnung("hellohousing_mietverlauf")
    verlauf: dict[str, list[tuple[datetime.date, Decimal, Decimal | None]]] = defaultdict(list)
    unlesbar = 0
    for zeile in _lesen(pfad, abschnitt, ("mietverhaeltnis", "gueltig_ab", "miete")):
        ab = _datum(zeile["gueltig_ab"], abschnitt)
        miete = _betrag(zeile["miete"], abschnitt)
        if ab is None or miete is None:
            unlesbar += 1
            continue
        verlauf[zeile["mietverhaeltnis"]].append(
            (ab, miete, _betrag(zeile.get("pauschale", ""), abschnitt))
        )
    for stufen in verlauf.values():
        stufen.sort(key=lambda s: s[0])
    return verlauf, unlesbar


def hellohousing_importieren(
    mieter_pfad: Path,
    mietverlauf_pfad: Path | None = None,
    *,
    ausfuehren: bool = False,
    stichtag: datetime.date | None = None,
) -> Bericht:
    """Parteien und laufende Verträge außerhalb von Wohnraum (H1.2, H1.3).

    - Zeilen auf Wohnraum werden übersprungen und nur gezählt (Wohnraum führt Verwaltio).
    - Beendete Mietverhältnisse (Ende vor dem Stichtag) werden übersprungen.
    - Kündigungsfrist, Kaution und Zahlweise stehen nicht im Export; sie bleiben offen und
      erscheinen auf der Seite „Datenstand“ (aus dem PDF nachtragen).
    """
    abschnitt = zuordnung("hellohousing_mieter")
    zeilen = _lesen(
        mieter_pfad, abschnitt, ("mietverhaeltnis", "mieter_nr", "einheit", "beginn", "miete")
    )
    verlauf, unlesbar = _mietverlauf(mietverlauf_pfad)
    stichtag = stichtag or zeit.heute()

    def arbeit() -> Bericht:
        bericht = Bericht("Import Parteien und Verträge (HelloHousing)", ausfuehren, len(zeilen))
        if unlesbar:
            bericht.zaehler["mietverlauf_unlesbar"] = unlesbar
        for nummer, zeile in enumerate(zeilen, start=2):
            _vertrag_importieren(zeile, nummer, abschnitt, verlauf, stichtag, bericht)
        return bericht

    return _probelauf(ausfuehren, arbeit)


def _partei_finden_oder_anlegen(
    zeile: dict[str, str], abschnitt: dict[str, Any], bericht: Bericht, kennung: str
) -> Partei | None:
    herkunft = f"hellohousing:{zeile['mieter_nr']}"
    vorhanden = Partei.objects.filter(herkunft=herkunft).first()
    if vorhanden is not None:
        bericht.zaehler["parteien_vorhanden"] += 1
        return vorhanden
    ist_firma = zeile.get("art", "") == abschnitt.get("firma_wert", "Firma")
    try:
        partei = services.partei_anlegen(
            Partei.Art.FIRMA if ist_firma else Partei.Art.PERSON,
            anrede=Partei.Anrede.aus_text(zeile.get("anrede", ""), firma=ist_firma),
            vorname=zeile.get("vorname", ""),
            nachname=zeile.get("nachname", ""),
            firma=zeile.get("firma", ""),
            strasse=zeile.get("strasse", ""),
            plz=zeile.get("plz", ""),
            ort=zeile.get("ort", ""),
            herkunft=herkunft,
        )
    except services.StammdatenFehler:
        bericht.abweichung("partei_ungueltig", kennung, uebersprungen=True)
        return None
    bericht.zaehler["parteien_neu"] += 1
    if zeile.get("email"):
        try:
            services.kontaktweg_hinzufuegen(
                partei, Kontaktweg.Art.EMAIL, zeile["email"], bevorzugt=True
            )
        except services.StammdatenFehler:
            bericht.abweichung("email_ungueltig", kennung)
    if zeile.get("telefon"):
        services.kontaktweg_hinzufuegen(partei, Kontaktweg.Art.TELEFON, zeile["telefon"])
    if not partei.hat_anschrift:
        bericht.abweichung("anschrift_fehlt", kennung)
    return partei


def _vortrag_importieren(
    vertrag: Vertrag,
    zeile: dict[str, str],
    abschnitt: dict[str, Any],
    kennung: str,
    bericht: Bericht,
) -> None:
    """Spalte „Offener Saldo“ (optional): der Vortrag aus dem alten System (Review 1, Punkt 1).
    Der Bericht nennt nur Zähler und NW-IDs, keine Beträge."""
    text = zeile.get("vortrag", "")
    if not text:
        return
    betrag = _betrag(text, abschnitt)
    if betrag is None or betrag < 0:
        bericht.abweichung("vortrag_unlesbar", kennung)
        return
    if betrag == 0:
        return
    # Spät importiert: ``soll`` importiert die Stammdaten.
    from apps.soll import services as soll

    try:
        soll.vortrag_setzen(vertrag, betrag)
    except soll.SollFehler:
        bericht.abweichung("vortrag_ohne_stichtag", kennung)
        return
    bericht.zaehler["vortraege"] += 1


def _vertrag_importieren(
    zeile: dict[str, str],
    nummer: int,
    abschnitt: dict[str, Any],
    verlauf: dict[str, list[tuple[datetime.date, Decimal, Decimal | None]]],
    stichtag: datetime.date,
    bericht: Bericht,
) -> None:
    zeilen_kennung = f"Zeile {nummer}"
    einheit = _einheit_finden(zeile["einheit"])
    if einheit is None:
        bericht.abweichung("einheit_unbekannt", zeilen_kennung, uebersprungen=True)
        return
    if einheit.ist_wohnraum:
        # Wohnraum führt Verwaltio: nichts weiter lesen, nur zählen.
        bericht.zaehler["wohnraum_uebersprungen"] += 1
        return
    kennung = einheit.nw_id
    beginn = _datum(zeile["beginn"], abschnitt)
    ende = _datum(zeile.get("ende", ""), abschnitt)
    if beginn is None:
        bericht.abweichung("beginn_fehlt", kennung, uebersprungen=True)
        return
    if ende is not None and ende < stichtag:
        bericht.zaehler["beendet_uebersprungen"] += 1
        return
    herkunft = f"hellohousing:{zeile['mietverhaeltnis']}"
    vorhanden = Vertrag.objects.filter(herkunft=herkunft).first()
    if vorhanden is not None:
        bericht.merken("vertraege_vorhanden", f"{vorhanden.nummer}/{kennung}")
        return

    status_vorher = einheit.status_am(stichtag)
    partei = _partei_finden_oder_anlegen(zeile, abschnitt, bericht, kennung)
    if partei is None:
        return
    stufen = list(verlauf.get(zeile["mietverhaeltnis"], []))
    if not stufen:
        miete = _betrag(zeile["miete"], abschnitt)
        if miete is not None:
            stufen = [(beginn, miete, _betrag(zeile.get("pauschale", ""), abschnitt))]
    try:
        with transaction.atomic():
            erste = stufen[0] if stufen else None
            vertrag = services.vertrag_anlegen(
                einheiten=[einheit],
                mieter=[partei],
                beginn=beginn,
                ende=ende,
                miete=erste[1] if erste else None,
                pauschale=(erste[2] or Decimal("0.00")) if erste else Decimal("0.00"),
                zahlweise=Kondition.Zahlweise.UNBEKANNT,
                kuendigungsregel="",
                kaution=None,
                status=Vertrag.Status.AKTIV,
                herkunft=herkunft,
                konditionsgrund="Import HelloHousing",
            )
            for ab, miete, pauschale in stufen[1:]:
                services.kondition_ab(
                    vertrag,
                    max(ab, beginn),
                    miete=miete,
                    pauschale=pauschale,
                    grund="Import Mietverlauf",
                )
            _vortrag_importieren(vertrag, zeile, abschnitt, kennung, bericht)
    except services.StammdatenFehler:
        bericht.abweichung("einheit_belegt_oder_ungueltig", kennung, uebersprungen=True)
        return
    bericht.merken("vertraege_neu", f"{vertrag.nummer}/{kennung}")
    if not stufen:
        bericht.abweichung("miete_fehlt", kennung)
    if len(stufen) > 1:
        bericht.abweichung("staffel_oder_verlauf", kennung)
    if status_vorher is not None and status_vorher.status != EinheitStatus.Status.VERMIETET:
        # Laut Einheiten-Verzeichnis nicht vermietet, laut HelloHousing ein laufender Vertrag.
        bericht.abweichung("status_widerspruch", kennung)
