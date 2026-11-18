"""Schreibweg und Lesehilfen des Cockpits: Aufgaben im Hof-Backlog und Sicherung prüfen.

Aufgaben im Hof-Backlog (Notion, N1, Aufgabe 6 von Schritt 9):

- ``kern.services`` ruft bei jeder Änderung eines Arbeitspostens ``kern.aufgaben.melden``
  auf; das landet in ``aufgabe_vormerken``. Das merkt nur vor (eine Zeile
  ``NotionAufgabe`` je Art), Notion ruft es nicht auf. Warum: Eine Buchung, ein Lauf oder
  ein Klick in der Arbeitsliste darf nie daran scheitern, dass Notion langsam ist oder
  nicht antwortet, und es gibt keine halben Zustände, wenn die Transaktion danach
  zurückrollt.
- Der Lauf ``aufgaben_abgleich`` (``aufgaben_abgleichen``) rechnet für jede Art aus
  ``HOF_NOTION_ARTEN`` den Inhalt aus den offenen Posten neu und ruft Notion nur, wenn
  sich der Inhalt geändert hat. Er läuft im Tageslauf und alle 30 Minuten.
- Inhalt: Titel aus Anzahl und Art („3 Mahnungen senden“), im Text die NW-IDs bzw.
  Objekt-Kürzel, die früheste Fälligkeit und der Link ins Hof-System. Keine Namen, keine
  Beträge, keine Hinweise der Posten.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models, transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode

from apps.cockpit.models import NotionAufgabe
from apps.cockpit.notion import AufgabenInhalt, NotionClient, NotionFehler
from apps.kern import services as kern
from apps.kern.models import Arbeitsposten, Lauf, Objekt

logger = logging.getLogger(__name__)

ART_SICHERUNG = "Sicherung prüfen"

#: So viele Kennungen stehen höchstens im Text einer Aufgabe.
KENNUNGEN_GRENZE = 60


# --- Kennungen: NW-ID oder Objekt-Kürzel zu einem Arbeitsposten ----------------------

Kennungsweg = Callable[[Any], list[str]]
_KENNUNGEN: dict[type[models.Model], Kennungsweg] = {}

#: Beziehungen, denen die Suche nach einer NW-ID folgt (in dieser Reihenfolge).
_WEGE = ("einheit", "vertrag", "zaehler", "beleg", "bezug", "objekt")


def kennung_anmelden(modell: type[models.Model], funktion: Kennungsweg) -> None:
    """Meldet für ein Modell an, welche NW-IDs oder Kürzel es in Aufgaben vertritt.

    Ohne Anmeldung sucht ``kennungen`` selbst: ``nw_id`` am Objekt, ein Objekt-Kürzel,
    die Einheiten eines Vertrags oder der Weg über ``einheit``, ``vertrag``, ``zaehler``,
    ``beleg`` (Eingangsrechnung), ``bezug`` und ``objekt``. Die Funktion darf nur IDs und
    Kürzel liefern, nie Namen.
    """
    _KENNUNGEN[modell] = funktion


def _suchen(objekt: Any, tiefe: int) -> list[str]:
    if objekt is None or tiefe > 3:
        return []
    for modell, funktion in _KENNUNGEN.items():
        if isinstance(objekt, modell):
            return funktion(objekt)
    nw_id = getattr(objekt, "nw_id", None)
    if isinstance(nw_id, str) and nw_id:
        return [nw_id]
    if isinstance(objekt, Objekt):
        return [objekt.kuerzel]
    einheiten = getattr(objekt, "einheiten", None)
    if einheiten is not None and hasattr(einheiten, "all"):
        ids = sorted(e.nw_id for e in einheiten.all() if getattr(e, "nw_id", ""))
        if ids:
            return ids
    for weg in _WEGE:
        try:
            ziel = getattr(objekt, weg, None)
        except ObjectDoesNotExist:
            continue
        if isinstance(ziel, models.Model):
            gefunden = _suchen(ziel, tiefe + 1)
            if gefunden:
                return gefunden
    return []


def kennungen_zu_bezug(bezug: models.Model | None) -> list[str]:
    """NW-IDs bzw. Objekt-Kürzel zu einem beliebigen Objekt (z. B. dem Bezug einer Frist),
    auf demselben Weg wie ``kennungen``; leer, wenn keine zu finden sind (Status-MCP)."""
    return _suchen(bezug, 0)


def kennungen(posten: Arbeitsposten) -> list[str]:
    """NW-IDs bzw. Objekt-Kürzel zum Bezug eines Postens; sonst Modell und ID."""
    bezug = posten.bezug if posten.bezug_id is not None else None
    gefunden = _suchen(bezug, 0)
    if gefunden:
        return gefunden
    if isinstance(bezug, models.Model):
        return [f"{bezug._meta.verbose_name} #{bezug.pk}"]
    return [f"Arbeitsposten #{posten.pk}"]


# --- Aufgaben im Hof-Backlog ---------------------------------------------------------


def arten() -> dict[str, tuple[str, str]]:
    """Arten, die als Aufgabe ins Hof-Backlog gehen: Art → (Titel bei 1, Titel ab 2)."""
    konfiguriert: dict[str, tuple[str, str]] = settings.HOF_NOTION_ARTEN
    return konfiguriert


def _offene_aufgabe(art: str, buendel: str = "") -> NotionAufgabe | None:
    return NotionAufgabe.objects.filter(
        art=art, buendel=buendel, status=NotionAufgabe.Status.OFFEN
    ).first()


def aufgabe_vormerken(posten: Arbeitsposten) -> NotionAufgabe | None:
    """Empfänger von ``kern.aufgaben.melden``: merkt die Aufgabe der Art zum Abgleich vor.

    Nur für Arten aus ``HOF_NOTION_ARTEN``. Ein neuer oder wieder offener Posten legt die
    Aufgabe an, falls es noch keine offene gibt; ein erledigter oder zurückgestellter
    merkt eine vorhandene vor.
    """
    if posten.art not in arten():
        return None
    jetzt = timezone.now()
    aufgabe = _offene_aufgabe(posten.art)
    if aufgabe is None:
        if posten.status != Arbeitsposten.Status.OFFEN:
            return None
        aufgabe = NotionAufgabe(art=posten.art, vorgemerkt_am=jetzt)
        aufgabe.save()
        return aufgabe
    aufgabe.vorgemerkt_am = jetzt
    aufgabe.save(update_fields=["vorgemerkt_am"])
    return aufgabe


def arbeitsliste_link(art: str) -> str:
    basis: str = settings.HOF_BASIS_URL
    return basis.rstrip("/") + reverse("kern:arbeitslisten") + "?" + urlencode({"art": art})


def titel(art: str, anzahl: int) -> str:
    eins, mehr = arten().get(art, (art, art))
    return f"{anzahl} {eins if anzahl == 1 else mehr}"


def inhalt_bauen(art: str, posten: Iterable[Arbeitsposten]) -> AufgabenInhalt:
    """Titel, Text, Fälligkeit und Link aus den offenen Posten einer Art.

    Nur Anzahl, Art, NW-IDs bzw. Kürzel, Datum und Link; nie Hinweise, Namen, Beträge.
    """
    liste = list(posten)
    alle: list[str] = []
    for p in liste:
        for kennung in kennungen(p):
            if kennung not in alle:
                alle.append(kennung)
    alle.sort()
    gezeigt = alle[:KENNUNGEN_GRENZE]
    rest = len(alle) - len(gezeigt)
    termine = [p.faellig_am for p in liste if p.faellig_am is not None]
    faellig = min(termine) if termine else None
    link = arbeitsliste_link(art)
    zeilen = [
        "NW-IDs/Kürzel: " + ", ".join(gezeigt) + (f" und {rest} weitere" if rest > 0 else ""),
        (f"Fällig: {faellig:%d.%m.%Y} (frühester Posten)" if faellig else "Fällig: ohne Datum"),
        f"Im Hof-System: {link}",
    ]
    return AufgabenInhalt(
        titel=titel(art, len(liste)), text="\n".join(zeilen), faellig_am=faellig, link=link
    )


def _fingerabdruck(inhalt: AufgabenInhalt) -> str:
    daten = {
        "titel": inhalt.titel,
        "text": inhalt.text,
        "faellig_am": inhalt.faellig_am.isoformat() if inhalt.faellig_am else None,
        "link": inhalt.link,
        "erledigt": inhalt.erledigt,
    }
    text = json.dumps(daten, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class AbgleichErgebnis:
    angelegt: list[str] = field(default_factory=list)
    aktualisiert: list[str] = field(default_factory=list)
    erledigt: list[str] = field(default_factory=list)
    unveraendert: list[str] = field(default_factory=list)
    fehler: list[str] = field(default_factory=list)

    def text(self) -> str:
        teile = [
            f"{len(self.angelegt)} angelegt",
            f"{len(self.aktualisiert)} aktualisiert",
            f"{len(self.erledigt)} erledigt",
            f"{len(self.unveraendert)} unverändert",
            f"{len(self.fehler)} Fehler",
        ]
        text = ", ".join(teile)
        if self.fehler:
            text += f" ({', '.join(self.fehler)})"
        return text


def _offene_posten(art: str) -> list[Arbeitsposten]:
    return list(
        Arbeitsposten.objects.filter(art=art, status=Arbeitsposten.Status.OFFEN)
        .select_related("bezug_typ")
        .order_by(models.F("faellig_am").asc(nulls_last=True), "angelegt_am", "pk")
    )


def _art_abgleichen(client: NotionClient, art: str, jetzt: datetime.datetime) -> str | None:
    posten = _offene_posten(art)
    aufgabe = (
        NotionAufgabe.objects.select_for_update()
        .filter(art=art, buendel="", status=NotionAufgabe.Status.OFFEN)
        .first()
    )
    if not posten:
        if aufgabe is None:
            return None
        if aufgabe.page_id:
            client.aktualisieren(
                aufgabe.page_id,
                AufgabenInhalt(
                    titel=aufgabe.titel or titel(art, 0),
                    text=aufgabe.text,
                    faellig_am=aufgabe.faellig_am,
                    link=arbeitsliste_link(art),
                    erledigt=True,
                ),
            )
        aufgabe.status = NotionAufgabe.Status.ERLEDIGT
        aufgabe.erledigt_am = jetzt
        aufgabe.anzahl = 0
        aufgabe.abgeglichen_am = jetzt
        aufgabe.fehler = ""
        aufgabe.save()
        return "erledigt"

    inhalt = inhalt_bauen(art, posten)
    schluessel = _fingerabdruck(inhalt)
    if aufgabe is None:
        aufgabe = NotionAufgabe(art=art)
    if not aufgabe.page_id:
        aufgabe.page_id = client.anlegen(inhalt)
        ergebnis = "angelegt"
    elif schluessel != aufgabe.inhalt_schluessel:
        client.aktualisieren(aufgabe.page_id, inhalt)
        ergebnis = "aktualisiert"
    else:
        ergebnis = "unveraendert"
    aufgabe.titel = inhalt.titel
    aufgabe.text = inhalt.text
    aufgabe.faellig_am = inhalt.faellig_am
    aufgabe.anzahl = len(posten)
    aufgabe.inhalt_schluessel = schluessel
    aufgabe.abgeglichen_am = jetzt
    aufgabe.fehler = ""
    aufgabe.save()
    return ergebnis


def _fehler_vermerken(art: str, grund: str) -> None:
    aufgabe = _offene_aufgabe(art)
    if aufgabe is not None:
        aufgabe.fehler = grund[:200]
        aufgabe.save(update_fields=["fehler"])


def aufgaben_abgleichen(
    client: NotionClient, *, jetzt: datetime.datetime | None = None
) -> AbgleichErgebnis:
    """Bringt die Aufgaben im Hof-Backlog auf den Stand der offenen Arbeitsposten.

    Je Art: Aufgabe anlegen, wenn es offene Posten gibt; aktualisieren, wenn sich Anzahl,
    Kennungen oder Fälligkeit geändert haben; auf Erledigt setzen, wenn keiner mehr offen
    ist. Scheitert Notion bei einer Art, bleibt deren Stand, und die anderen laufen weiter.
    """
    jetzt = jetzt or timezone.now()
    ergebnis = AbgleichErgebnis()
    for art in arten():
        try:
            with transaction.atomic():
                status = _art_abgleichen(client, art, jetzt)
        except NotionFehler as fehler:
            logger.warning("Aufgabe %s nicht abgeglichen: %s", art, fehler)
            _fehler_vermerken(art, str(fehler))
            ergebnis.fehler.append(art)
            continue
        if status is not None:
            liste: list[str] = getattr(ergebnis, status)
            liste.append(art)
    return ergebnis


# --- Sicherung prüfen (Schritt 0d, Statusdatei von scripts/backup.sh) -----------------


@dataclass(frozen=True)
class Sicherungsstand:
    """Stand der letzten Sicherung laut Statusdatei. ``grund`` ist leer, wenn alles passt."""

    pfad: str
    zeit: datetime.datetime | None = None
    ergebnis: str = ""
    snapshot_id: str = ""
    fehler_bei: str = ""
    alter_stunden: float | None = None
    grund: str = ""

    @property
    def ok(self) -> bool:
        return not self.grund


def _zeit_lesen(text: Any) -> datetime.datetime | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        zeit = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if timezone.is_naive(zeit):
        zeit = zeit.replace(tzinfo=datetime.UTC)
    return zeit


def sicherung_lesen(
    pfad: str | Path | None = None, *, jetzt: datetime.datetime | None = None
) -> Sicherungsstand:
    """Liest ``HOF_BACKUP_STATUS`` (JSON aus scripts/backup.sh) und bewertet ihn.

    Nicht in Ordnung ist: Datei fehlt oder ist unlesbar, ``ergebnis`` ist nicht „ok“, oder
    die Sicherung ist älter als ``HOF_BACKUP_HOECHSTALTER_STUNDEN`` (Standard 26).
    """
    pfad = Path(pfad or settings.HOF_BACKUP_STATUS)
    jetzt = jetzt or timezone.now()
    hoechstalter: int = settings.HOF_BACKUP_HOECHSTALTER_STUNDEN
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Sicherungsstand(pfad=str(pfad), grund="Statusdatei fehlt")
    except (OSError, ValueError):
        return Sicherungsstand(pfad=str(pfad), grund="Statusdatei unlesbar")
    if not isinstance(daten, dict):
        return Sicherungsstand(pfad=str(pfad), grund="Statusdatei unlesbar")
    zeit = _zeit_lesen(daten.get("zeit"))
    ergebnis = str(daten.get("ergebnis") or "")
    alter = (jetzt - zeit).total_seconds() / 3600 if zeit else None
    if zeit is None:
        grund = "Zeit der Sicherung fehlt"
    elif ergebnis != "ok":
        grund = f"Ergebnis „{ergebnis or 'leer'}“"
        if daten.get("fehler_bei"):
            grund += f" bei {daten['fehler_bei']}"
    elif alter is not None and alter > hoechstalter:
        grund = f"älter als {hoechstalter} Stunden"
    else:
        grund = ""
    return Sicherungsstand(
        pfad=str(pfad),
        zeit=zeit,
        ergebnis=ergebnis,
        snapshot_id=str(daten.get("snapshot_id") or ""),
        fehler_bei=str(daten.get("fehler_bei") or ""),
        alter_stunden=alter,
        grund=grund,
    )


@dataclass(frozen=True)
class Sicherungspruefung:
    stand: Sicherungsstand
    neuer_posten: Arbeitsposten | None
    geschlossen: int


def sicherung_pruefen(
    *, jetzt: datetime.datetime | None = None, lauf: Lauf | None = None
) -> Sicherungspruefung:
    """Prüft die letzte Sicherung. Ist sie nicht in Ordnung, entsteht ein Arbeitsposten
    „Sicherung prüfen“ (höchstens einer offen); ist sie wieder in Ordnung, schließt er sich.
    """
    stand = sicherung_lesen(jetzt=jetzt)
    offen = list(Arbeitsposten.objects.filter(art=ART_SICHERUNG, status=Arbeitsposten.Status.OFFEN))
    if stand.ok:
        for posten in offen:
            kern.arbeitsposten_erledigen(posten)
        return Sicherungspruefung(stand=stand, neuer_posten=None, geschlossen=len(offen))
    if offen:
        return Sicherungspruefung(stand=stand, neuer_posten=None, geschlossen=0)
    posten = kern.arbeitsposten_anlegen(
        ART_SICHERUNG,
        aktionen=kern.STANDARD_AKTIONEN,
        faellig_am=timezone.localdate(),
        hinweis=f"Sicherung nicht in Ordnung: {stand.grund}. Siehe docs/betrieb.md, Sicherung.",
        lauf=lauf,
    )
    return Sicherungspruefung(stand=stand, neuer_posten=posten, geschlossen=0)
