"""Seite „Datenstand“: fehlende Pflichtfelder je Bereich (Bauplan, Abschnitt „Seite Datenstand“).

Welche Felder Pflicht sind, steht in ``konfiguration.toml`` unter ``[datenstand.*]``.
Hier steht nur, wie jedes Feld geprüft wird (``PRUEFUNGEN``) und wohin der Link zur
Eingabe führt. Der Bereich Zähler kommt in Schritt 8 dazu.

``kennung`` einer Lücke ist eine ID (NW-ID, Vertragsnummer, „Partei #12“). Den Namen einer
Partei zeigt nur die Seite selbst (``name``), nie ein Bericht.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from django.urls import reverse

from apps.stammdaten import konfiguration
from apps.stammdaten.models import Einheit, Kaution, Kondition, Partei, Schluessel, Vertrag

GEBUNDEN = (Vertrag.Status.ENTWURF, Vertrag.Status.AKTIV, Vertrag.Status.GEKUENDIGT)
LAUFEND = (Vertrag.Status.AKTIV, Vertrag.Status.GEKUENDIGT)


@dataclass(frozen=True)
class Pruefung:
    titel: str
    #: (Objekt, Stichtag) -> True, wenn der Wert vorhanden ist.
    vorhanden: Callable[[Any, datetime.date], bool]
    #: Abschnitt der Eingabeseite, zu dem der Link springt.
    anker: str


def _kondition(vertrag: Vertrag, stichtag: datetime.date) -> Kondition | None:
    return vertrag.kondition_am(max(stichtag, vertrag.beginn))


def _kaution(vertrag: Vertrag) -> Kaution | None:
    return Kaution.objects.filter(vertrag=vertrag).first()


PRUEFUNGEN: dict[str, dict[str, Pruefung]] = {
    "einheit": {
        "typ": Pruefung("Typ", lambda e, _: bool(e.typ), "stammfelder"),
        "flaeche": Pruefung("Fläche", lambda e, _: bool(e.flaeche_qm), "stammfelder"),
        "objekt": Pruefung("Objekt", lambda e, _: e.objekt_id is not None, "stammfelder"),
        "status": Pruefung("Status", lambda e, t: e.status_am(t) is not None, "status"),
        "zielmiete": Pruefung("Zielmiete", lambda e, t: e.zielmiete_am(t) is not None, "zielmiete"),
    },
    "partei": {
        "anschrift": Pruefung("Anschrift", lambda p, _: p.hat_anschrift, "stammfelder"),
        "kontaktweg": Pruefung("Kontaktweg", lambda p, _: p.kontaktwege.exists(), "kontaktwege"),
    },
    "vertrag": {
        "beginn": Pruefung("Beginn", lambda v, _: v.beginn is not None, "stammfelder"),
        "miete": Pruefung("Miete", lambda v, t: _kondition(v, t) is not None, "kondition"),
        "zahlweise": Pruefung(
            "Zahlweise",
            lambda v, t: bool((k := _kondition(v, t)) and k.zahlweise),
            "kondition",
        ),
        "kuendigungsfrist": Pruefung(
            "Kündigungsfrist", lambda v, _: bool(v.kuendigungsregel), "stammfelder"
        ),
        "kaution": Pruefung(
            "Kaution",
            lambda v, _: (k := _kaution(v)) is not None and k.betrag is not None,
            "kaution",
        ),
        "scan": Pruefung(
            "unterschriebener Scan", lambda v, _: bool(v.unterschriebener_scan), "unterschrift"
        ),
    },
    "kaution": {
        "betrag": Pruefung("Betrag", lambda k, _: k.betrag is not None, "kaution"),
        "eingang": Pruefung("Eingangsdatum", lambda k, _: k.eingang_am is not None, "kaution"),
        "konto": Pruefung("Konto", lambda k, _: bool(k.konto), "kaution"),
    },
    "schluessel": {
        "anzahl": Pruefung(
            "Anzahl",
            lambda e, _: e.schluessel.filter(anzahl__gt=0).exists(),
            "schluessel",
        ),
        "inhaber": Pruefung(
            "wer sie hat",
            lambda e, _: not e.schluessel.filter(inhaber=Schluessel.Inhaber.UNBEKANNT).exists(),
            "schluessel",
        ),
    },
}


@dataclass(frozen=True)
class Luecke:
    bereich: str
    objekt_id: int
    kennung: str
    felder: tuple[tuple[str, str], ...]
    url: str
    #: Nur für die Seite (Name einer Partei), nie für Berichte.
    name: str = ""

    @property
    def felder_titel(self) -> str:
        return ", ".join(titel for _, titel in self.felder)


@dataclass
class Bereichsstand:
    schluessel: str
    titel: str
    geprueft: int = 0
    luecken: list[Luecke] = field(default_factory=list)

    @property
    def anzahl_luecken(self) -> int:
        """Zahl der fehlenden Pflichtfelder im Bereich."""
        return sum(len(luecke.felder) for luecke in self.luecken)


def konfiguration_pruefen() -> list[str]:
    """Pflichtfelder aus ``konfiguration.toml``, für die es keine Prüfung gibt."""
    fehler = []
    for schluessel, bereich in konfiguration.laden().datenstand.items():
        pruefungen = PRUEFUNGEN.get(schluessel)
        if pruefungen is None:
            fehler.append(f"Bereich {schluessel} unbekannt")
            continue
        for pflicht in (bereich.pflicht, *bereich.je_art.values()):
            fehler += [f"{schluessel}.{p}" for p in pflicht if p not in pruefungen]
    return fehler


def _objekte(bereich: str, typen: tuple[str, ...]) -> Iterable[tuple[Any, str | None]]:
    """Die geprüften Objekte eines Bereichs mit ihrer Art (für ``je_art``)."""
    if bereich == "einheit":
        for e in Einheit.objects.filter(aufgeloest_am__isnull=True).select_related("objekt"):
            yield e, e.typ
    elif bereich == "partei":
        for p in Partei.objects.all():
            yield p, None
    elif bereich == "vertrag":
        for v in Vertrag.objects.filter(status__in=GEBUNDEN):
            yield v, v.vertragsart
    elif bereich == "kaution":
        # Kautionen laufender Verträge, wenn eine vereinbart ist (Betrag 0 heißt: keine).
        for k in Kaution.objects.filter(vertrag__status__in=LAUFEND).exclude(betrag=0):
            yield k, None
    elif bereich == "schluessel":
        einheiten = Einheit.objects.filter(aufgeloest_am__isnull=True)
        if typen:
            einheiten = einheiten.filter(typ__in=typen)
        for e in einheiten.exclude(typ=Einheit.Typ.WOHNUNG):
            yield e, None


def _kennung(bereich: str, objekt: Any) -> tuple[str, str, str]:
    """(Kennung, Seite, Name) eines Objekts."""
    if bereich in ("einheit", "schluessel"):
        return objekt.nw_id, reverse("stammdaten:einheit", args=[objekt.pk]), ""
    if bereich == "partei":
        return (
            f"Partei #{objekt.pk}",
            reverse("stammdaten:partei", args=[objekt.pk]),
            objekt.anzeigename,
        )
    if bereich == "vertrag":
        return objekt.nummer, reverse("stammdaten:vertrag", args=[objekt.pk]), ""
    vertrag = objekt.vertrag
    return f"Kaution {vertrag.nummer}", reverse("stammdaten:vertrag", args=[vertrag.pk]), ""


def ermitteln(stichtag: datetime.date | None = None) -> list[Bereichsstand]:
    """Lücken je Bereich in der Reihenfolge der Konfiguration."""
    stichtag = stichtag or datetime.date.today()
    ergebnis = []
    for schluessel, bereich in konfiguration.laden().datenstand.items():
        pruefungen = PRUEFUNGEN[schluessel]
        stand = Bereichsstand(schluessel=schluessel, titel=bereich.titel)
        for objekt, art in _objekte(schluessel, bereich.typen):
            stand.geprueft += 1
            fehlend = [
                (pflicht, pruefungen[pflicht])
                for pflicht in bereich.pflicht_fuer(art)
                if not pruefungen[pflicht].vorhanden(objekt, stichtag)
            ]
            if not fehlend:
                continue
            kennung, seite, name = _kennung(schluessel, objekt)
            stand.luecken.append(
                Luecke(
                    bereich=schluessel,
                    objekt_id=objekt.pk,
                    kennung=kennung,
                    felder=tuple((p, pruefung.titel) for p, pruefung in fehlend),
                    url=f"{seite}#{fehlend[0][1].anker}",
                    name=name,
                )
            )
        ergebnis.append(stand)
    return ergebnis


def zaehler(stichtag: datetime.date | None = None) -> dict[str, int]:
    """Zahl der Lücken je Bereich, ohne Namen (für Cockpit und Status-MCP)."""
    return {stand.schluessel: stand.anzahl_luecken for stand in ermitteln(stichtag)}


def einheiten_mit_luecken(stichtag: datetime.date | None = None) -> list[str]:
    """NW-IDs der Einheiten mit Lücken (für Berichte)."""
    for stand in ermitteln(stichtag):
        if stand.schluessel == "einheit":
            return [luecke.kennung for luecke in stand.luecken]
    return []


__all__ = ["PRUEFUNGEN", "Bereichsstand", "Luecke", "ermitteln", "konfiguration_pruefen", "zaehler"]
