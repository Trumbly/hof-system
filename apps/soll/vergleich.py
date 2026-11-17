"""Vergleich der Sollstellung mit dem Soll-Export aus HelloHousing (``soll_vergleich``).

Liest den Export (Spaltenzuordnung in ``konfiguration.toml``, Abschnitt
``[vergleich_hellohousing]``), findet je Zeile den Vertrag und zählt je Vertrag die
Abweichungen gegen die Sollposten im Hof-System. Verglichen werden Posten gleicher Art mit
gleichem Beginn; der Zeitraum des Vergleichs reicht vom frühesten bis zum spätesten Beginn
im Export (oder ``von``/``bis``).

Abweichungsarten: ``ABWEICHUNGEN``. Der Bericht nennt nur Zähler, Vertragsnummern, NW-IDs
und Zeilennummern, keine Beträge je Vertrag und keine Namen (CLAUDE.md, Abschnitt 1).
Der Vergleich ändert nichts.
"""

from __future__ import annotations

import csv
import datetime
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.db.models import Q

from apps.soll import konfiguration
from apps.soll.models import Sollposten
from apps.soll.services import GELTEND
from apps.stammdaten.models import Einheit, Vertrag

FEHLT_IM_HOF = "fehlt im Hof-System"
FEHLT_IM_EXPORT = "fehlt im Export"
BETRAG = "Betrag weicht ab"
ZEITRAUM = "Zeitraumende weicht ab"
FAELLIGKEIT = "Fälligkeit weicht ab"
DOPPELT = "doppelt im Export"
ABWEICHUNGEN = (FEHLT_IM_HOF, FEHLT_IM_EXPORT, BETRAG, ZEITRAUM, FAELLIGKEIT, DOPPELT)

OHNE_VERTRAG = "kein Vertrag gefunden"
ART_UNBEKANNT = "Sollart nicht zugeordnet"
UNGUELTIG = "Datum oder Betrag unlesbar"


class VergleichFehler(Exception):
    """Die Datei passt nicht zur Spaltenzuordnung. Die Meldung nennt nur Spaltenköpfe."""


@dataclass(frozen=True)
class Exportposten:
    zeile: int
    vertrag: Vertrag
    art: str
    von: datetime.date
    bis: datetime.date
    faellig_am: datetime.date | None
    betrag: Decimal


@dataclass
class Bericht:
    datei: str
    von: datetime.date | None = None
    bis: datetime.date | None = None
    zeilen: int = 0
    #: Vertragsnummer -> Abweichungsart -> Anzahl (auch Verträge ohne Abweichung, leer)
    je_vertrag: dict[str, Counter[str]] = field(default_factory=dict)
    einheiten: dict[str, str] = field(default_factory=dict)
    #: Grund -> Zeilennummern, die nicht verglichen wurden
    uebersprungen: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))

    @property
    def gesamt(self) -> Counter[str]:
        summe: Counter[str] = Counter()
        for zaehler in self.je_vertrag.values():
            summe.update(zaehler)
        return summe

    @property
    def mit_abweichung(self) -> list[str]:
        return sorted(n for n, z in self.je_vertrag.items() if sum(z.values()))

    def text(self) -> str:
        zeitraum = (
            f"{self.von:%d.%m.%Y} bis {self.bis:%d.%m.%Y}" if self.von and self.bis else "leer"
        )
        zeilen = [
            f"Vergleich der Sollstellung mit HelloHousing ({self.datei})",
            f"Beginn der Posten von {zeitraum}",
            f"Zeilen gelesen: {self.zeilen}",
            f"Verträge verglichen: {len(self.je_vertrag)}, "
            f"ohne Abweichung: {len(self.je_vertrag) - len(self.mit_abweichung)}, "
            f"mit Abweichungen: {len(self.mit_abweichung)}",
        ]
        gesamt = self.gesamt
        if gesamt:
            zeilen.append("Abweichungen gesamt:")
            zeilen += [f"  {art}: {gesamt[art]}" for art in ABWEICHUNGEN if gesamt[art]]
            zeilen.append("Je Vertrag:")
            for nummer in self.mit_abweichung:
                zaehler = self.je_vertrag[nummer]
                teile = ", ".join(f"{art} {zaehler[art]}" for art in ABWEICHUNGEN if zaehler[art])
                einheit = f" ({self.einheiten[nummer]})" if self.einheiten.get(nummer) else ""
                zeilen.append(f"  {nummer}{einheit}: {teile}")
        for grund, nummern in sorted(self.uebersprungen.items()):
            liste = ", ".join(str(n) for n in nummern)
            zeilen.append(f"Nicht verglichen, {grund}: {len(nummern)} (Zeile {liste})")
        return "\n".join(zeilen)


# --- Lesen ---------------------------------------------------------------------------


def _betrag(text: str, format_: konfiguration.Vergleichsformat) -> Decimal | None:
    if not text:
        return None
    sauber = text.replace("€", "").replace(" ", "").replace(" ", "")
    if format_.dezimaltrennzeichen == ",":
        sauber = sauber.replace(".", "").replace(",", ".")
    try:
        return Decimal(sauber).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def _datum(text: str, format_: konfiguration.Vergleichsformat) -> datetime.date | None:
    if not text:
        return None
    try:
        return datetime.datetime.strptime(text, format_.datumsformat).date()
    except ValueError:
        return None


def _einheit(text: str) -> Einheit | None:
    if not text:
        return None
    return (
        Einheit.objects.filter(nw_id=text).first()
        or Einheit.objects.filter(bezeichnung__iexact=text).first()
    )


def _vertrag(
    mietverhaeltnis: str,
    einheit_text: str,
    von: datetime.date,
    format_: konfiguration.Vergleichsformat,
) -> Vertrag | None:
    if mietverhaeltnis:
        treffer = Vertrag.objects.filter(
            herkunft=f"{format_.herkunft_praefix}{mietverhaeltnis}"
        ).first()
        if treffer is not None:
            return treffer
    einheit = _einheit(einheit_text)
    if einheit is None:
        return None
    return (
        einheit.vertraege.filter(beginn__lte=von)
        .filter(Q(ende__isnull=True) | Q(ende__gte=von))
        .exclude(status=Vertrag.Status.ENTWURF)
        .order_by("-beginn")
        .first()
    )


def _koepfe(pfad: Path, leser: csv.DictReader[str]) -> None:
    spalten = konfiguration.laden().vergleich.spalten
    koepfe = set(leser.fieldnames or [])
    pflicht = [spalten[f] for f in konfiguration.VERGLEICH_PFLICHT]
    fehlend = [kopf for kopf in pflicht if kopf not in koepfe]
    if fehlend:
        raise VergleichFehler(f"{pfad.name}: Spalten fehlen: {', '.join(fehlend)}")


def koepfe_pruefen(pfad: Path) -> None:
    """Prüft nur die Spaltenköpfe gegen die Zuordnung (vor dem eigentlichen Vergleich)."""
    format_ = konfiguration.laden().vergleich
    with pfad.open(encoding="utf-8-sig", newline="") as datei:
        _koepfe(pfad, csv.DictReader(datei, delimiter=format_.trennzeichen))


def export_lesen(pfad: Path, bericht: Bericht) -> list[Exportposten]:
    format_ = konfiguration.laden().vergleich
    spalten = format_.spalten
    with pfad.open(encoding="utf-8-sig", newline="") as datei:
        leser = csv.DictReader(datei, delimiter=format_.trennzeichen)
        _koepfe(pfad, leser)
        ergebnis = []
        for nummer, roh in enumerate(leser, start=2):  # Zeile 1 sind die Köpfe
            bericht.zeilen += 1
            zeile = {feld: (roh.get(kopf) or "").strip() for feld, kopf in spalten.items()}
            art = format_.arten.get(zeile["art"])
            if art is None:
                bericht.uebersprungen[ART_UNBEKANNT].append(nummer)
                continue
            von, bis = _datum(zeile["von"], format_), _datum(zeile["bis"], format_)
            betrag = _betrag(zeile["betrag"], format_)
            faellig = _datum(zeile.get("faellig", ""), format_)
            if von is None or bis is None or betrag is None:
                bericht.uebersprungen[UNGUELTIG].append(nummer)
                continue
            vertrag = _vertrag(
                zeile.get("mietverhaeltnis", ""), zeile.get("einheit", ""), von, format_
            )
            if vertrag is None:
                bericht.uebersprungen[OHNE_VERTRAG].append(nummer)
                continue
            ergebnis.append(Exportposten(nummer, vertrag, art, von, bis, faellig, betrag))
    return ergebnis


# --- Vergleich -----------------------------------------------------------------------


def vergleichen(
    pfad: Path, *, von: datetime.date | None = None, bis: datetime.date | None = None
) -> Bericht:
    """Zählt je Vertrag die Abweichungen zwischen Export und Sollposten. Ändert nichts."""
    bericht = Bericht(datei=pfad.name)
    export = export_lesen(pfad, bericht)
    if export:
        von = von or min(p.von for p in export)
        bis = bis or max(p.von for p in export)
    bericht.von, bericht.bis = von, bis
    if von is None or bis is None:
        return bericht
    export = [p for p in export if von <= p.von <= bis]
    arten = set(konfiguration.laden().vergleich.arten.values())

    aus_export: dict[int, dict[tuple[str, datetime.date], list[Exportposten]]] = defaultdict(
        lambda: defaultdict(list)
    )
    vertraege: dict[int, Vertrag] = {}
    for posten in export:
        aus_export[posten.vertrag.pk][(posten.art, posten.von)].append(posten)
        vertraege[posten.vertrag.pk] = posten.vertrag

    aus_hof: dict[int, dict[tuple[str, datetime.date], Sollposten]] = defaultdict(dict)
    hof = Sollposten.objects.filter(
        status__in=GELTEND, art__in=arten, von__gte=von, von__lte=bis
    ).select_related("vertrag")
    for posten in hof:
        aus_hof[posten.vertrag_id][(posten.art, posten.von)] = posten
        vertraege[posten.vertrag_id] = posten.vertrag

    for pk, vertrag in sorted(vertraege.items(), key=lambda e: e[1].nummer):
        zaehler: Counter[str] = Counter()
        im_export, im_hof = aus_export.get(pk, {}), aus_hof.get(pk, {})
        for schluessel in set(im_export) | set(im_hof):
            zeilen, soll = im_export.get(schluessel, []), im_hof.get(schluessel)
            if len(zeilen) > 1:
                zaehler[DOPPELT] += len(zeilen) - 1
            if not zeilen:
                zaehler[FEHLT_IM_EXPORT] += 1
                continue
            if soll is None:
                zaehler[FEHLT_IM_HOF] += 1
                continue
            zeile = zeilen[0]
            if zeile.bis != soll.bis:
                zaehler[ZEITRAUM] += 1
            if zeile.betrag != soll.betrag:
                zaehler[BETRAG] += 1
            if zeile.faellig_am is not None and zeile.faellig_am != soll.faellig_am:
                zaehler[FAELLIGKEIT] += 1
        bericht.je_vertrag[vertrag.nummer] = zaehler
        bericht.einheiten[vertrag.nummer] = ", ".join(
            e.nw_id for e in vertrag.einheiten.order_by("nw_id")
        )
    return bericht
