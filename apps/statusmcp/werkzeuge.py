"""Die Werkzeuge des Status-MCP: lesen Sichten ``sm_*`` und antworten mit JSON-artigen Daten.

Ohne Django-Importe. Jede Antwort enthält nur Zähler, Summen (je Objekt, je Kategorie und
Objekt, gesamt), Daten, Tage, Arten, Status, Objekt-Kürzel und NW-IDs. Beträge stehen als
Text mit zwei Nachkommastellen („1234.50“), nie als Gleitkommazahl.

Eingaben prüft jedes Werkzeug selbst; ein Fehler wird ``Eingabefehler`` mit einer Meldung
für Claude.
"""

from __future__ import annotations

import datetime
import re
from collections import defaultdict
from decimal import Decimal
from typing import Any

from apps.statusmcp.quelle import Leser

#: So viele nächste Fristen nennt der Überblick.
NAECHSTE_FRISTEN = 5
#: Längster Zeitraum für ``fristen``.
FRISTEN_HOECHSTENS_TAGE = 3 * 366
#: Standard für ``fristen`` ohne ``bis``.
FRISTEN_STANDARD_TAGE = 90

LAUF_SICHERUNG = "sicherung_pruefen"
LAUF_KENNZAHLEN = "statusmcp_aktualisieren"
PROBLEM = ("abgebrochen", "mit_fehlern")
#: Läufe ohne Arbeit (etwa ``benachrichtigen`` ohne Kanal außerhalb der Produktion): kein
#: Problem, aber auch kein Erfolg.
UEBERSPRUNGEN = "uebersprungen"

KEINE_KENNZAHLEN = (
    "Noch keine Kennzahlen: Der Lauf statusmcp_aktualisieren ist noch nicht gelaufen "
    "(er läuft im Tageslauf)."
)


class Eingabefehler(ValueError):
    """Eine Eingabe passt nicht; die Meldung geht an Claude."""


def betrag(wert: Decimal | int | None) -> str | None:
    if wert is None:
        return None
    return f"{Decimal(wert):.2f}"


def _zeit(wert: datetime.datetime | datetime.date | None) -> str | None:
    return wert.isoformat() if wert is not None else None


def _datum(wert: datetime.date | str | None, feld: str) -> datetime.date | None:
    if wert is None or wert == "":
        return None
    if isinstance(wert, datetime.date):
        return wert
    try:
        return datetime.date.fromisoformat(wert)
    except ValueError:
        raise Eingabefehler(f"{feld}: Datum im Format JJJJ-MM-TT erwartet.") from None


# --- ueberblick -----------------------------------------------------------------------


def _lauf(zeile: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": zeile["name"],
        "status": zeile["status"],
        "gestartet_am": _zeit(zeile["gestartet_am"]),
        "beendet_am": _zeit(zeile["beendet_am"]),
        "gebucht": zeile["gebucht"],
        "arbeitsposten": zeile["arbeitsposten"],
        "fehler": zeile["fehler"],
    }


def _frist(zeile: dict[str, Any], heute: datetime.date) -> dict[str, Any]:
    return {
        "datum": zeile["datum"].isoformat(),
        "art": zeile["art"],
        "quelle": zeile["quelle"],
        "faellig_ab": zeile["faellig_ab"].isoformat(),
        "ueberfaellig": zeile["datum"] < heute,
        "kennungen": list(zeile["kennungen"] or []),
    }


def ueberblick(leser: Leser, heute: datetime.date) -> dict[str, Any]:
    """Offene Arbeitsposten je Art, nächste Fristen, Zustand der Läufe und der Sicherung."""
    je_art: dict[str, dict[str, Any]] = {}
    for zeile in leser.abfragen(
        "SELECT art, faellig_am FROM sm_arbeitsposten WHERE status = 'offen' ORDER BY art"
    ):
        eintrag = je_art.setdefault(
            zeile["art"], {"art": zeile["art"], "offen": 0, "ueberfaellig": 0, "naechste": None}
        )
        eintrag["offen"] += 1
        faellig: datetime.date | None = zeile["faellig_am"]
        if faellig is not None:
            if faellig < heute:
                eintrag["ueberfaellig"] += 1
            if eintrag["naechste"] is None or faellig < eintrag["naechste"]:
                eintrag["naechste"] = faellig
    arten = sorted(je_art.values(), key=lambda e: (-e["offen"], e["art"]))
    for eintrag in arten:
        eintrag["naechste_faelligkeit"] = _zeit(eintrag.pop("naechste"))

    fristen = leser.abfragen(
        "SELECT datum, art, quelle, faellig_ab, kennungen FROM sm_fristen ORDER BY datum, frist_id"
    )
    ueberfaellig = [f for f in fristen if f["datum"] < heute]
    kommend = [f for f in fristen if f["datum"] >= heute]

    laeufe = {
        z["name"]: z
        for z in leser.abfragen(
            "SELECT name, status, gestartet_am, beendet_am, gebucht, arbeitsposten, fehler "
            "FROM sm_laeufe ORDER BY name"
        )
    }
    sicherung = laeufe.get(LAUF_SICHERUNG)
    kennzahlen = laeufe.get(LAUF_KENNZAHLEN)
    return {
        "heute": heute.isoformat(),
        "arbeitsposten": {"offen": sum(e["offen"] for e in arten), "je_art": arten},
        "fristen": {
            "offen": len(fristen),
            "ueberfaellig": len(ueberfaellig),
            "ueberfaellige": [_frist(f, heute) for f in ueberfaellig[:NAECHSTE_FRISTEN]],
            "naechste": [_frist(f, heute) for f in kommend[:NAECHSTE_FRISTEN]],
        },
        "laeufe": {
            "mit_problem": sorted(n for n, z in laeufe.items() if z["status"] in PROBLEM),
            "uebersprungen": sorted(n for n, z in laeufe.items() if z["status"] == UEBERSPRUNGEN),
            "letzte": [_lauf(z) for z in laeufe.values()],
        },
        "sicherung": {
            "geprueft_am": _zeit(sicherung["gestartet_am"]) if sicherung else None,
            "in_ordnung": (sicherung["status"] == "erfolgreich") if sicherung else None,
            "status_der_pruefung": sicherung["status"] if sicherung else "nie geprüft",
        },
        "kennzahlen_stand": _zeit(kennzahlen["beendet_am"]) if kennzahlen else None,
    }


# --- rueckstaende ---------------------------------------------------------------------

_KLASSEN = {
    "summe": ("vertraege", "summe"),
    "0_30": ("vertraege_0_30", "summe_0_30"),
    "31_60": ("vertraege_31_60", "summe_31_60"),
    "ueber_60": ("vertraege_ueber_60", "summe_ueber_60"),
    "nicht_pruefbar": (None, "nicht_pruefbar"),
    "in_klaerung": ("vertraege_in_klaerung", None),
}


def _stand(zeilen: list[dict[str, Any]]) -> dict[str, Any]:
    if not zeilen:
        return {"stichtag": None, "stand": None}
    return {"stichtag": zeilen[0]["stichtag"].isoformat(), "stand": _zeit(zeilen[0]["stand"])}


def rueckstaende(leser: Leser) -> dict[str, Any]:
    """Verträge im Rückstand und Summen je Objekt; NW-IDs mit Alter, ohne Beträge je Vertrag."""
    zeilen = leser.abfragen(
        "SELECT objekt, klasse, vertraege, summe, datenstand_bank, stichtag, stand "
        "FROM sm_rueckstaende ORDER BY objekt, klasse"
    )
    if not zeilen:
        return {**_stand(zeilen), "hinweis": KEINE_KENNZAHLEN}
    je_objekt: dict[str, dict[str, Any]] = {}
    for zeile in zeilen:
        eintrag = je_objekt.setdefault(zeile["objekt"], {"objekt": zeile["objekt"] or None})
        anzahl_feld, summe_feld = _KLASSEN.get(zeile["klasse"], (None, None))
        if anzahl_feld:
            eintrag[anzahl_feld] = zeile["vertraege"] or 0
        if summe_feld:
            eintrag[summe_feld] = betrag(zeile["summe"] or 0)
    gesamt = je_objekt.pop("", {})
    gesamt.pop("objekt", None)
    einheiten = leser.abfragen(
        "SELECT nw_id, objekt, alter_tage, klasse FROM sm_rueckstaende_einheiten "
        "ORDER BY alter_tage DESC, nw_id"
    )
    datenstand_bank = zeilen[0]["datenstand_bank"]
    return {
        **_stand(zeilen),
        "datenstand_bank": _zeit(datenstand_bank),
        "gesamt": gesamt,
        "je_objekt": list(je_objekt.values()),
        "einheiten": [
            {
                "nw_id": e["nw_id"],
                "objekt": e["objekt"],
                "alter_tage": e["alter_tage"],
                "klasse": e["klasse"],
            }
            for e in einheiten
        ],
        "hinweis": "Summen nur je Objekt und gesamt; Beträge je Vertrag gibt der Dienst nicht aus.",
    }


# --- datenstand -----------------------------------------------------------------------


def datenstand(leser: Leser) -> dict[str, Any]:
    """Fehlende Pflichtfelder je Bereich und die NW-IDs bzw. Objekt-Kürzel mit Lücken."""
    zeilen = leser.abfragen(
        "SELECT bereich, luecken, geprueft, stichtag, stand FROM sm_datenstand ORDER BY bereich"
    )
    if not zeilen:
        return {**_stand(zeilen), "hinweis": KEINE_KENNZAHLEN}
    kennungen: dict[str, list[str]] = defaultdict(list)
    for zeile in leser.abfragen(
        "SELECT bereich, kennung FROM sm_datenstand_kennungen ORDER BY bereich, kennung"
    ):
        kennungen[zeile["bereich"]].append(zeile["kennung"])
    bereiche = [
        {
            "bereich": z["bereich"],
            "luecken": z["luecken"],
            "geprueft": z["geprueft"],
            "kennungen": kennungen.get(z["bereich"], []),
        }
        for z in zeilen
    ]
    return {
        **_stand(zeilen),
        "luecken_gesamt": sum(b["luecken"] or 0 for b in bereiche),
        "bereiche": bereiche,
        "kennungen_mit_luecken": sorted({k for liste in kennungen.values() for k in liste}),
    }


# --- leerstand ------------------------------------------------------------------------


def leerstand(leser: Leser) -> dict[str, Any]:
    """Freie Einheiten mit NW-ID, Typ und Tagen frei (Stand des letzten Laufs)."""
    zeilen = leser.abfragen(
        "SELECT nw_id, objekt, typ, frei_seit, tage_frei, stichtag, stand FROM sm_leerstand "
        "ORDER BY tage_frei DESC, nw_id"
    )
    if not zeilen:
        # Ohne Zeilen ist offen, ob niemand frei ist oder der Lauf fehlt: der Datenstand sagt es.
        stand = leser.abfragen("SELECT stichtag, stand FROM sm_datenstand LIMIT 1")
        if not stand:
            return {**_stand(stand), "hinweis": KEINE_KENNZAHLEN}
        return {**_stand(stand), "anzahl": 0, "je_typ": {}, "einheiten": []}
    je_typ: dict[str, int] = defaultdict(int)
    for zeile in zeilen:
        je_typ[zeile["typ"]] += 1
    return {
        **_stand(zeilen),
        "anzahl": len(zeilen),
        "je_typ": dict(sorted(je_typ.items())),
        "einheiten": [
            {
                "nw_id": z["nw_id"],
                "objekt": z["objekt"],
                "typ": z["typ"],
                "frei_seit": z["frei_seit"].isoformat(),
                "tage_frei": z["tage_frei"],
            }
            for z in zeilen
        ],
    }


# --- kassenbuch_monat -----------------------------------------------------------------

MONAT = re.compile(r"(\d{4})-(\d{2})")
_ART_SUMME = {
    "einnahme": "einnahmen",
    "ausgabe": "ausgaben",
    "entnahme": "entnahmen",
    "umbuchung": "umbuchungen",
}


def monat_lesen(monat: str) -> datetime.date:
    treffer = MONAT.fullmatch((monat or "").strip())
    if not treffer:
        raise Eingabefehler("monat: Format JJJJ-MM erwartet, z. B. 2026-10.")
    jahr, nummer = int(treffer[1]), int(treffer[2])
    if not 1 <= nummer <= 12 or not 2000 <= jahr <= 2100:
        raise Eingabefehler("monat: Monat 01 bis 12, Jahr 2000 bis 2100.")
    return datetime.date(jahr, nummer, 1)


def kassenbuch_monat(leser: Leser, monat: str) -> dict[str, Any]:
    """Summen mit Vorzeichen (Eingang plus, Ausgang minus) je Objekt und Kategorie."""
    erster = monat_lesen(monat)
    zeilen = leser.abfragen(
        "SELECT objekt, kategorie_id, kategorie_art, wohnraum, buchungen, summe "
        "FROM sm_kassenbuch_monat WHERE monat = %s ORDER BY objekt, kategorie_art, kategorie_id",
        [erster],
    )
    je_objekt: dict[str, dict[str, Any]] = {}
    gesamt: dict[str, Decimal] = defaultdict(Decimal)
    for zeile in zeilen:
        eintrag = je_objekt.setdefault(
            zeile["objekt"],
            {"objekt": zeile["objekt"], "summen": defaultdict(Decimal), "kategorien": []},
        )
        summe: Decimal = zeile["summe"] or Decimal(0)
        art_summe = _ART_SUMME.get(zeile["kategorie_art"], "sonstige")
        for ziel in (eintrag["summen"], gesamt):
            ziel[art_summe] += summe
            ziel["saldo"] += summe
        eintrag["kategorien"].append(
            {
                "kategorie_id": zeile["kategorie_id"],
                "art": zeile["kategorie_art"],
                "wohnraum": zeile["wohnraum"],
                "buchungen": zeile["buchungen"],
                "summe": betrag(summe),
            }
        )
    objekte = []
    for eintrag in je_objekt.values():
        summen = eintrag.pop("summen")
        eintrag.update({schluessel: betrag(wert) for schluessel, wert in sorted(summen.items())})
        objekte.append(eintrag)
    return {
        "monat": erster.strftime("%Y-%m"),
        "gesamt": {schluessel: betrag(wert) for schluessel, wert in sorted(gesamt.items())},
        "je_objekt": objekte,
        "hinweis": (
            "Kategorien nur mit ID und Art: Ihre Bezeichnungen können Namen enthalten und "
            "gehen nicht hinaus. Beträge mit Vorzeichen (Eingang plus, Ausgang minus)."
        ),
    }


# --- fristen --------------------------------------------------------------------------


def fristen(
    leser: Leser,
    heute: datetime.date,
    von: datetime.date | str | None = None,
    bis: datetime.date | str | None = None,
) -> dict[str, Any]:
    """Offene Fristen von ``von`` bis ``bis`` (beide eingeschlossen) mit Art und Kennungen."""
    anfang = _datum(von, "von") or heute
    ende = _datum(bis, "bis") or anfang + datetime.timedelta(days=FRISTEN_STANDARD_TAGE)
    if ende < anfang:
        raise Eingabefehler("bis liegt vor von.")
    if (ende - anfang).days > FRISTEN_HOECHSTENS_TAGE:
        raise Eingabefehler(f"Zeitraum höchstens {FRISTEN_HOECHSTENS_TAGE} Tage.")
    zeilen = leser.abfragen(
        "SELECT datum, art, quelle, faellig_ab, kennungen FROM sm_fristen "
        "WHERE datum BETWEEN %s AND %s ORDER BY datum, frist_id",
        [anfang, ende],
    )
    return {
        "von": anfang.isoformat(),
        "bis": ende.isoformat(),
        "anzahl": len(zeilen),
        "fristen": [_frist(z, heute) for z in zeilen],
    }
