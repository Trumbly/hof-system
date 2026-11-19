"""Seite „Datenstand“: fehlende Pflichtfelder je Bereich (Bauplan, Abschnitt „Seite Datenstand“).

Welche Felder Pflicht sind, steht in ``konfiguration.toml`` unter ``[datenstand.*]``.
Hier steht nur, wie jedes Feld geprüft wird (``PRUEFUNGEN``) und wohin der Link zur
Eingabe führt. Den Bereich Zähler prüft ``apps.zaehler.services`` (Stand zum letzten
Stichtag, siehe ``apps/zaehler/konfiguration.toml``).

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
from apps.stammdaten.models import (
    BINDEND,
    LAUFEND,
    Einheit,
    Kaution,
    Kondition,
    Partei,
    Schluessel,
    Vertrag,
)
from apps.zaehler import services as zaehler_services
from apps.zaehler.models import Zaehler

GEBUNDEN = BINDEND  # stammdaten.models.VertragQuerySet


@dataclass(frozen=True)
class Pruefung:
    titel: str
    #: (Objekt, Stichtag) -> True, wenn der Wert vorhanden ist.
    vorhanden: Callable[[Any, datetime.date], bool]
    #: Abschnitt der Eingabeseite, zu dem der Link springt.
    anker: str
    #: Andere Eingabeseite als die des Objekts (Objekt -> URL), z. B. die Bilder.
    seite: Callable[[Any], str] | None = None


def _kondition(vertrag: Vertrag, stichtag: datetime.date) -> Kondition | None:
    return vertrag.kondition_am(max(stichtag, vertrag.beginn))


def _kaution(vertrag: Vertrag) -> Kaution | None:
    return Kaution.objects.filter(vertrag=vertrag).first()


def _stand_ok(zaehler: Zaehler, stichtag: datetime.date) -> bool:
    letzter = zaehler_services.letzter_stichtag(stichtag)
    if not zaehler_services.braucht_stand(zaehler, letzter):
        return True
    return zaehler_services.stand_zum_stichtag(zaehler, letzter) is not None


def _foto_ok(zaehler: Zaehler, stichtag: datetime.date) -> bool:
    """Fehlt der Stand ganz, zählt nur er als Lücke, nicht noch einmal das Foto."""
    letzter = zaehler_services.letzter_stichtag(stichtag)
    if not zaehler_services.braucht_stand(zaehler, letzter):
        return True
    stand = zaehler_services.stand_zum_stichtag(zaehler, letzter)
    return stand is None or bool(stand.foto)


PRUEFUNGEN: dict[str, dict[str, Pruefung]] = {
    "einheit": {
        "typ": Pruefung("Typ", lambda e, _: bool(e.typ), "stammfelder"),
        "flaeche": Pruefung("Fläche", lambda e, _: bool(e.flaeche_qm), "stammfelder"),
        "objekt": Pruefung("Objekt", lambda e, _: e.objekt_id is not None, "stammfelder"),
        "status": Pruefung("Status", lambda e, t: e.status_am(t) is not None, "status"),
        "zielmiete": Pruefung("Zielmiete", lambda e, t: e.zielmiete_am(t) is not None, "zielmiete"),
        "zaehler": Pruefung(
            "Zähler", lambda e, _: e.zaehler.filter(ausgebaut_am__isnull=True).exists(), "zaehler"
        ),
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
    # Review 2, A11: Was die Website für eine Einheit braucht. Ohne Fläche ist sie nicht
    # buchbar (apps.anbindungen.services.buchbar_ab).
    "katalog": {
        "flaeche": Pruefung("Fläche", lambda e, _: bool(e.flaeche_qm), "stammfelder"),
        "bilder": Pruefung(
            "Bilder",
            lambda e, _: e.bilder.filter(aktiv=True).exists(),
            "",
            seite=lambda e: reverse("anbindungen:bilder", args=[e.pk]),
        ),
        "slug": Pruefung(
            "Adresse auf der Website (Slug)", lambda e, _: bool(e.website_slug), "stammfelder"
        ),
    },
    "zaehler": {
        "nummer": Pruefung("Nummer", lambda z, _: bool(z.nummer), "stammfelder"),
        "art": Pruefung("Art", lambda z, _: bool(z.art), "stammfelder"),
        "einbaudatum": Pruefung(
            "Einbaudatum", lambda z, _: z.einbaudatum is not None, "stammfelder"
        ),
        "eichfrist": Pruefung("Eichfrist", lambda z, _: z.eichfrist_bis is not None, "stammfelder"),
        "stand_stichtag": Pruefung("Stand zum letzten Stichtag", _stand_ok, "staende"),
        "foto_stichtag": Pruefung("Foto zum Stand am Stichtag", _foto_ok, "staende"),
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


#: Wie die Objekte eines Bereichs in der Mehrzahl heißen („44 Verträge ohne Scan“).
MEHRZAHL = {
    "einheit": ("Einheit", "Einheiten"),
    "partei": ("Partei", "Parteien"),
    "vertrag": ("Vertrag", "Verträge"),
    "kaution": ("Kaution", "Kautionen"),
    "schluessel": ("Einheit", "Einheiten"),
    "katalog": ("Einheit im Katalog", "Einheiten im Katalog"),
    "zaehler": ("Zähler", "Zähler"),
}
#: Titel einer Gruppe, wo „ohne <Feld>“ schief klingt.
OHNE = {
    ("vertrag", "scan"): "ohne Scan",
    ("schluessel", "anzahl"): "ohne Schlüssel",
    ("schluessel", "inhaber"): "mit Schlüsseln bei Unbekannt",
    ("katalog", "slug"): "ohne Adresse auf der Website",
}
#: Sammelaktionen je Gruppe (Bereich, Feld) → Schlüssel des Dialogs
SAMMELAKTIONEN = {("vertrag", "scan"): "scans"}


@dataclass(frozen=True)
class Feldgruppe:
    """Alle Lücken eines Bereichs, denen dasselbe Feld fehlt (Review 2, B12)."""

    bereich: str
    feld: str
    feld_titel: str
    luecken: tuple[Luecke, ...]

    @property
    def anzahl(self) -> int:
        return len(self.luecken)

    @property
    def titel(self) -> str:
        einzahl, mehrzahl = MEHRZAHL.get(self.bereich, ("Eintrag", "Einträge"))
        ohne = OHNE.get((self.bereich, self.feld), f"ohne {self.feld_titel}")
        return f"{self.anzahl} {einzahl if self.anzahl == 1 else mehrzahl} {ohne}"

    @property
    def sammelaktion(self) -> str:
        return SAMMELAKTIONEN.get((self.bereich, self.feld), "")

    @property
    def mit_namen(self) -> bool:
        return any(luecke.name for luecke in self.luecken)

    #: Lange Gruppen stehen zugeklappt, damit die Seite überschaubar bleibt.
    OFFEN_BIS = 20

    @property
    def offen(self) -> bool:
        return self.anzahl <= self.OFFEN_BIS


def nach_feld(stand: Bereichsstand) -> list[Feldgruppe]:
    """Die Lücken eines Bereichs gruppiert nach fehlendem Feld, die größte Gruppe zuerst.
    Eine Lücke mit zwei fehlenden Feldern steht in beiden Gruppen."""
    je_feld: dict[str, list[Luecke]] = {}
    titel: dict[str, str] = {}
    for luecke in stand.luecken:
        for feld, feld_titel in luecke.felder:
            je_feld.setdefault(feld, []).append(luecke)
            titel[feld] = feld_titel
    reihenfolge = list(je_feld)
    return sorted(
        (Feldgruppe(stand.schluessel, f, titel[f], tuple(je_feld[f])) for f in je_feld),
        key=lambda g: (-g.anzahl, reihenfolge.index(g.feld)),
    )


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
        for v in Vertrag.objects.bindend():
            yield v, v.vertragsart
    elif bereich == "kaution":
        # Kautionen laufender Verträge, wenn eine vereinbart ist (Betrag 0 heißt: keine).
        for k in Kaution.objects.filter(vertrag__status__in=LAUFEND).exclude(betrag=0):
            yield k, None
    elif bereich == "katalog":
        einheiten = Einheit.objects.filter(
            aufgeloest_am__isnull=True, vermarktung=Einheit.Vermarktung.WEBSITE
        ).exclude(typ=Einheit.Typ.WOHNUNG)
        for e in einheiten.order_by("nw_id"):
            yield e, None
    elif bereich == "schluessel":
        einheiten = Einheit.objects.filter(aufgeloest_am__isnull=True)
        if typen:
            einheiten = einheiten.filter(typ__in=typen)
        for e in einheiten.exclude(typ=Einheit.Typ.WOHNUNG):
            yield e, None
    elif bereich == "zaehler":
        for z in zaehler_services.eingebaute_zaehler().select_related(
            "einheit", "objekt", "einheit__objekt"
        ):
            yield z, z.art


def _kennung(bereich: str, objekt: Any) -> tuple[str, str, str]:
    """(Kennung, Seite, Name) eines Objekts."""
    if bereich in ("einheit", "schluessel", "katalog"):
        return objekt.nw_id, reverse("stammdaten:einheit", args=[objekt.pk]), ""
    if bereich == "partei":
        return (
            f"Partei #{objekt.pk}",
            reverse("stammdaten:partei", args=[objekt.pk]),
            objekt.anzeigename,
        )
    if bereich == "vertrag":
        # Review 2, B12: Mieter-Name, damit Max den Papiervertrag findet (nur die Seite).
        mieter = ", ".join(
            vp.partei.anzeigename
            for vp in objekt.vertragsparteien.select_related("partei").order_by("pk")
            if vp.rolle == "mieter"
        )
        return objekt.nummer, reverse("stammdaten:vertrag", args=[objekt.pk]), mieter
    if bereich == "zaehler":
        return f"Zähler {objekt.nummer}", reverse("zaehler:zaehler", args=[objekt.pk]), ""
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
            erste = fehlend[0][1]
            url = erste.seite(objekt) if erste.seite else f"{seite}#{erste.anker}"
            stand.luecken.append(
                Luecke(
                    bereich=schluessel,
                    objekt_id=objekt.pk,
                    kennung=kennung,
                    felder=tuple((p, pruefung.titel) for p, pruefung in fehlend),
                    url=url,
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


__all__ = [
    "PRUEFUNGEN",
    "Bereichsstand",
    "Feldgruppe",
    "Luecke",
    "ermitteln",
    "konfiguration_pruefen",
    "nach_feld",
    "zaehler",
]
