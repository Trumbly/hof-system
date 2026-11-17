"""Import der Kategorien aus einer Liste (Name und Art): lesen, prüfen, Bericht mit Zählern.

Der Bericht nennt nur Zähler, Abweichungsarten und Zeilennummern, nie Namen (Kategorienamen
können Personennamen enthalten, etwa „Entnahme …“). Geschrieben wird über
``services.kategorie_anlegen``. Eine Kategorie, deren Name es schon gibt, bleibt unverändert;
weicht ihre Art ab, zählt der Bericht das als Abweichung.
"""

from __future__ import annotations

import csv
import io
from collections import Counter
from dataclasses import dataclass, field

from django.db import transaction

from apps.kassenbuch import services
from apps.kassenbuch.importformat import PFLICHTFELDER, KategorienFormat
from apps.kassenbuch.models import Kategorie
from apps.kern.models import Mandant


class ImportFehler(ValueError):
    """Die Datei passt nicht zum Format. Die Meldung nennt nur Spalten und Zähler."""


@dataclass(frozen=True)
class Zeile:
    nummer: int
    name: str
    art: str
    mandant: Mandant | None


@dataclass
class ImportBericht:
    zeilen: int = 0
    gueltig: int = 0
    fehlerhaft: Counter[str] = field(default_factory=Counter)
    #: Zeilennummern je Abweichungsart (ohne Namen)
    fehler_zeilen: dict[str, list[int]] = field(default_factory=dict)
    neu: int = 0
    vorhanden: int = 0
    art_weicht_ab: int = 0
    ausgefuehrt: bool = False
    je_art: Counter[str] = field(default_factory=Counter)

    def fehler(self, art: str, nummer: int) -> None:
        self.fehlerhaft[art] += 1
        self.fehler_zeilen.setdefault(art, []).append(nummer)

    def zeilen_text(self) -> list[str]:
        """Der Bericht als Zeilen: nur Zähler, Abweichungsarten und Zeilennummern."""
        zeilen = [
            f"Zeilen gelesen: {self.zeilen}",
            f"gültig: {self.gueltig}",
            f"fehlerhaft: {sum(self.fehlerhaft.values())}",
        ]
        for art, anzahl in sorted(self.fehlerhaft.items()):
            nummern = ", ".join(str(n) for n in self.fehler_zeilen.get(art, [])[:20])
            zeilen.append(f"  {art}: {anzahl} (Zeilen {nummern})")
        zeilen += [f"  davon {art}: {anzahl}" for art, anzahl in sorted(self.je_art.items())]
        zeilen += [
            f"neu: {self.neu}",
            f"schon vorhanden: {self.vorhanden}",
            f"vorhanden mit anderer Art (nicht geändert): {self.art_weicht_ab}",
        ]
        zeilen.append(
            "Ausgeführt."
            if self.ausgefuehrt
            else "Probelauf: nichts geändert. Übernehmen mit --ausfuehren."
        )
        return zeilen


def lesen(inhalt: str, format: KategorienFormat) -> tuple[list[Zeile], ImportBericht]:
    leser = csv.DictReader(io.StringIO(inhalt), delimiter=format.trennzeichen)
    kopf = {s.strip() for s in (leser.fieldnames or [])}
    fehlend = [format.spalten[f] for f in PFLICHTFELDER if format.spalten[f] not in kopf]
    if fehlend:
        raise ImportFehler(f"Spalten fehlen: {', '.join(fehlend)}")
    spalte_mandant = format.spalten.get("mandant", "")
    mandanten = {m.kuerzel.casefold(): m for m in Mandant.objects.all()}

    bericht = ImportBericht()
    zeilen: list[Zeile] = []
    namen: set[str] = set()
    for nummer, roh in enumerate(leser, start=2):  # Zeile 1 ist der Kopf
        werte = {(k or "").strip(): (v or "").strip() for k, v in roh.items()}
        if not any(werte.values()):
            continue
        bericht.zeilen += 1
        name = werte.get(format.spalten["name"], "")
        art = format.arten.get(werte.get(format.spalten["art"], "").casefold())
        kuerzel = werte.get(spalte_mandant, "") if spalte_mandant else ""
        mandant = mandanten.get(kuerzel.casefold()) if kuerzel else None
        if not name:
            bericht.fehler("Name leer", nummer)
        elif art is None:
            bericht.fehler("Art unbekannt", nummer)
        elif name.casefold() in namen:
            bericht.fehler("Name doppelt in der Datei", nummer)
        elif kuerzel and mandant is None:
            bericht.fehler("Mandant unbekannt", nummer)
        elif art == Kategorie.Art.ENTNAHME and mandant is None:
            bericht.fehler("Entnahme ohne Mandant", nummer)
        else:
            namen.add(name.casefold())
            bericht.gueltig += 1
            bericht.je_art[art] += 1
            zeilen.append(
                Zeile(
                    nummer=nummer,
                    name=name,
                    art=art,
                    mandant=mandant if art == Kategorie.Art.ENTNAHME else None,
                )
            )
    return zeilen, bericht


def importieren(inhalt: str, format: KategorienFormat, *, ausfuehren: bool) -> ImportBericht:
    """Probelauf (Standard) zählt nur; mit ``ausfuehren`` werden neue Kategorien angelegt."""
    zeilen, bericht = lesen(inhalt, format)
    vorhanden = {k.bezeichnung.casefold(): k for k in Kategorie.objects.all()}
    with transaction.atomic():
        for zeile in zeilen:
            alt = vorhanden.get(zeile.name.casefold())
            if alt is not None:
                bericht.vorhanden += 1
                if alt.art != zeile.art:
                    bericht.art_weicht_ab += 1
                continue
            bericht.neu += 1
            if ausfuehren:
                try:
                    services.kategorie_anlegen(zeile.name, zeile.art, mandant=zeile.mandant)
                except services.KassenbuchFehler:
                    bericht.neu -= 1
                    bericht.fehler("nicht angelegt", zeile.nummer)
    bericht.ausgefuehrt = ausfuehren
    return bericht
