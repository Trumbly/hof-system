"""Sammelaktion des Datenstands: Scans hochladen und per V-Nummer im Dateinamen zuordnen
(Review 2, B12).

Ablauf: Max wählt mehrere PDFs („V-0005 unterschrieben.pdf“, „v0012.pdf“). Das System legt
sie in eine Zwischenablage (Dateispeicher, nicht die Datenbank), zeigt je Datei, zu welchem
Vertrag sie gehört oder warum nicht, und hängt sie erst nach „übernehmen“ an (über
``services.vertrag_scan_anhaengen``). Danach ist die Zwischenablage leer.

Dateinamen können Namen enthalten: Sie stehen nur in der Vorschau, nie in Logs.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import UploadedFile

from apps.stammdaten import services
from apps.stammdaten.models import Vertrag

ZWISCHENABLAGE = "zwischenablage/scans"
#: „V-0005“, „v0005“, „V_5“, „Vertrag V 12“ (die Ziffern ergeben die Nummer)
V_NUMMER = re.compile(r"(?<![A-Za-z])V[-_ ]?(\d{1,5})(?!\d)", re.IGNORECASE)
HOECHSTENS = 100


class ScanFehler(ValueError):
    pass


@dataclass(frozen=True)
class Zuordnung:
    """Eine Datei der Vorschau: zu welchem Vertrag, oder warum nicht."""

    nummer: int
    dateiname: str
    v_nummer: str
    vertrag: Vertrag | None
    hinweis: str = ""

    @property
    def ok(self) -> bool:
        return self.vertrag is not None and not self.hinweis


def v_nummer(dateiname: str) -> str:
    """Die V-Nummer im Dateinamen in der Schreibweise des Systems („V-0005“), sonst ""."""
    treffer = V_NUMMER.search(dateiname.rsplit("/", 1)[-1])
    return f"V-{int(treffer.group(1)):04d}" if treffer else ""


def _pfad(kennung: str, name: str = "") -> str:
    return f"{ZWISCHENABLAGE}/{kennung}/{name}" if name else f"{ZWISCHENABLAGE}/{kennung}"


def ablegen(dateien: Iterable[UploadedFile]) -> str:
    """Legt die Dateien in die Zwischenablage und gibt die Kennung zurück."""
    liste = list(dateien)
    if not liste:
        raise ScanFehler("Bitte mindestens eine Datei wählen.")
    if len(liste) > HOECHSTENS:
        raise ScanFehler(f"Höchstens {HOECHSTENS} Dateien auf einmal.")
    kennung = uuid.uuid4().hex
    eintraege = []
    for nummer, datei in enumerate(liste, start=1):
        inhalt = datei.read()
        pfad = default_storage.save(_pfad(kennung, f"{nummer:03d}.bin"), ContentFile(inhalt))
        eintraege.append(
            {"datei": (datei.name or "")[:200], "pfad": pfad, "pdf": inhalt.startswith(b"%PDF")}
        )
    default_storage.save(_pfad(kennung, "liste.json"), ContentFile(json.dumps(eintraege).encode()))
    return kennung


def _liste(kennung: str) -> list[dict[str, object]]:
    if not re.fullmatch(r"[0-9a-f]{32}", kennung or ""):
        raise ScanFehler("Die Vorschau ist abgelaufen. Bitte die Dateien noch einmal wählen.")
    pfad = _pfad(kennung, "liste.json")
    if not default_storage.exists(pfad):
        raise ScanFehler("Die Vorschau ist abgelaufen. Bitte die Dateien noch einmal wählen.")
    with default_storage.open(pfad, "rb") as datei:
        daten: list[dict[str, object]] = json.loads(datei.read())
    return daten


def vorschau(kennung: str) -> list[Zuordnung]:
    """Je Datei der Vertrag zur V-Nummer im Dateinamen, oder der Grund, warum nicht."""
    eintraege = _liste(kennung)
    nummern = [v_nummer(str(e["datei"])) for e in eintraege]
    vertraege = {v.nummer: v for v in Vertrag.objects.filter(nummer__in=[n for n in nummern if n])}
    gesehen: set[str] = set()
    ergebnis = []
    for i, (eintrag, nummer) in enumerate(zip(eintraege, nummern, strict=True), start=1):
        vertrag = vertraege.get(nummer)
        hinweis = ""
        if not eintrag.get("pdf"):
            hinweis = "Kein PDF; Scans bitte als PDF."
        elif not nummer:
            hinweis = "Keine V-Nummer im Dateinamen."
        elif vertrag is None:
            hinweis = f"Einen Vertrag {nummer} gibt es nicht."
        elif nummer in gesehen:
            hinweis = f"Für {nummer} ist schon eine andere Datei gewählt."
        elif vertrag.unterschriebener_scan:
            hinweis = "Hat schon einen Scan; der bleibt."
        if nummer:
            gesehen.add(nummer)
        ergebnis.append(
            Zuordnung(
                nummer=i,
                dateiname=str(eintrag["datei"]),
                v_nummer=nummer,
                vertrag=vertrag,
                hinweis=hinweis,
            )
        )
    return ergebnis


def aufraeumen(kennung: str) -> None:
    try:
        eintraege = _liste(kennung)
    except ScanFehler:
        return
    for eintrag in eintraege:
        default_storage.delete(str(eintrag["pfad"]))
    default_storage.delete(_pfad(kennung, "liste.json"))


def uebernehmen(kennung: str) -> list[Vertrag]:
    """Hängt jede passende Datei an ihren Vertrag und leert die Zwischenablage."""
    eintraege = _liste(kennung)
    angehaengt = []
    for zuordnung, eintrag in zip(vorschau(kennung), eintraege, strict=True):
        if not zuordnung.ok or zuordnung.vertrag is None:
            continue
        with default_storage.open(str(eintrag["pfad"]), "rb") as datei:
            inhalt = datei.read()
        angehaengt.append(
            services.vertrag_scan_anhaengen(
                zuordnung.vertrag, ContentFile(inhalt, name=f"{zuordnung.v_nummer}.pdf")
            )
        )
    aufraeumen(kennung)
    return angehaengt
