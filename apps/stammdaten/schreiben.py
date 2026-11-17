"""Andockstelle zur App ``dokumente`` (Schritt 3c): Schreiben aus Vorlagen.

Die Stammdaten fordern hier Schreiben an: Vertragsdokument (H2.3), Kündigungsbestätigung
(H2.8), Kautionsabrechnung (H2.10), Übergabe- und Rückgabeprotokoll (H2.4, H2.9).

Jede Art hat
- eine **Vorlagenart** (``VORLAGEN``; beim Vertrag je Vertragsart aus ``konfiguration.toml``),
- einen **Versandweg** und
- einen **Kontext** (``kontext_vertrag`` usw.): die Werte für die Platzhalter. Die Werte
  sind roh (``Decimal``, ``date``, ``str``); das Format setzt die Vorlage.

**Heute (vor Schritt 3c):** ``_anfordern`` legt einen Arbeitsposten „Schreiben erstellen“
an, mit Vorlagenart und Versandweg im Hinweis und dem Bezug (Vertrag, Kündigung …).

**Umstellung, sobald ``apps.dokumente`` steht:** Nur ``_anfordern`` ändert sich, etwa::

    from apps.dokumente import services as dokumente

    vorlage = dokumente.vorlage_fuer(vorlagenart)   # freigegebene Version der Vorlagenart
    return dokumente.schreiben_erzeugen(vorlage, bezug, kontext, versandweg)

Das Schreiben entsteht dann im Status „bereit“; versendet wird es erst über den
Senden-Knopf (CLAUDE.md, Regel 4.8). Die öffentlichen Funktionen und ihre Aufrufer in
``stammdaten.services`` bleiben gleich.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any

from django.db import models

from apps.kern import services as kern
from apps.kern.models import Arbeitsposten
from apps.stammdaten import konfiguration
from apps.stammdaten.models import Einheit, Kaution, Kuendigung, Partei, Uebergabe, Vertrag

#: Art der Arbeitsposten, solange die App dokumente fehlt.
ART = "Schreiben erstellen"

#: Vorlagenart und Versandweg je Schreiben (außer Vertrag: Vorlage je Vertragsart).
VORLAGEN: dict[str, tuple[str, str]] = {
    "vertrag": ("", "vor_ort"),
    "kuendigungsbestaetigung": ("kuendigungsbestaetigung", "mail"),
    "kautionsabrechnung": ("kautionsabrechnung", "brief"),
    "uebergabeprotokoll": ("uebergabeprotokoll", "vor_ort"),
    "rueckgabeprotokoll": ("rueckgabeprotokoll", "vor_ort"),
}

Kontext = dict[str, Any]


# --- Kontexte ------------------------------------------------------------------------


def _partei(partei: Partei) -> Kontext:
    return {
        "art": partei.art,
        "anrede": partei.anrede,
        "name": partei.anzeigename,
        "vorname": partei.vorname,
        "nachname": partei.nachname,
        "firma": partei.firma,
        "adresszusatz": partei.adresszusatz,
        "strasse": partei.strasse,
        "plz": partei.plz,
        "ort": partei.ort,
    }


def _einheit(einheit: Einheit) -> Kontext:
    return {
        "nw_id": einheit.nw_id,
        "typ": einheit.get_typ_display(),
        "bezeichnung": einheit.bezeichnung,
        "flaeche_qm": einheit.flaeche_qm,
        "objekt": einheit.objekt.bezeichnung,
        "objekt_anschrift": einheit.objekt.anschrift,
    }


def _brutto(miete: Decimal, pauschale: Decimal, steuersatz: Decimal) -> Decimal:
    netto = miete + pauschale
    return (netto * (1 + steuersatz / 100)).quantize(Decimal("0.01"))


def kontext_vertrag(vertrag: Vertrag, stichtag: datetime.date | None = None) -> Kontext:
    """Platzhalter für Vertragsdokument und alle Schreiben zu einem Vertrag.

    ``kondition`` ist die am Stichtag (Standard: Vertragsbeginn) gültige; ``staffel`` nennt
    alle Stufen in zeitlicher Folge.
    """
    stichtag = stichtag or vertrag.beginn
    art = konfiguration.vertragsart(vertrag.vertragsart)
    regel = (
        konfiguration.kuendigungsregel(vertrag.kuendigungsregel)
        if vertrag.kuendigungsregel
        else konfiguration.kuendigungsregel(art.kuendigungsregel)
    )
    konditionen = list(vertrag.konditionen.order_by("gueltig_ab"))
    aktuell = vertrag.kondition_am(stichtag) or (konditionen[0] if konditionen else None)
    kaution: Kaution | None = getattr(vertrag, "kaution", None)
    rollen = {
        rolle: [
            _partei(vp.partei)
            for vp in vertrag.vertragsparteien.filter(rolle=rolle).select_related("partei")
        ]
        for rolle in ("mieter", "zahler", "buerge")
    }
    return {
        "vertrag": {
            "nummer": vertrag.nummer,
            "art": vertrag.vertragsart,
            "art_titel": art.titel,
            "beginn": vertrag.beginn,
            "ende": vertrag.ende,
            "kuendigungsfrist": regel.titel,
            "sonderabreden": vertrag.sonderabreden,
        },
        "mandant": {"bezeichnung": vertrag.mandant.bezeichnung},
        "mieter": rollen["mieter"],
        "zahler": rollen["zahler"],
        "buergen": rollen["buerge"],
        "einheiten": [_einheit(e) for e in vertrag.einheiten.select_related("objekt")],
        "kondition": (
            {
                "miete": aktuell.miete,
                "pauschale": aktuell.pauschale,
                "steuersatz": aktuell.steuersatz,
                "brutto": _brutto(aktuell.miete, aktuell.pauschale, aktuell.steuersatz),
                "zahlweise": aktuell.get_zahlweise_display(),
                "gueltig_ab": aktuell.gueltig_ab,
            }
            if aktuell
            else None
        ),
        "staffel": [
            {"gueltig_ab": k.gueltig_ab, "gueltig_bis": k.gueltig_bis, "miete": k.miete}
            for k in konditionen
        ],
        "kaution": (
            {"betrag": kaution.betrag, "konto": kaution.konto} if kaution is not None else None
        ),
    }


def kontext_kuendigung(kuendigung: Kuendigung) -> Kontext:
    """Platzhalter der Kündigungsbestätigung: Vertrag plus Zugang, Fristende, Rückgabe."""
    kontext = kontext_vertrag(kuendigung.vertrag)
    kontext["kuendigung"] = {
        "von": kuendigung.get_von_display(),
        "zugang_am": kuendigung.zugang_am,
        "zum_wunsch": kuendigung.zum_wunsch,
        "fristende": kuendigung.fristende,
        "rueckgabetermin": kuendigung.rueckgabetermin,
        "kuendigungsfrist": konfiguration.kuendigungsregel(kuendigung.kuendigungsregel).titel,
    }
    return kontext


def kontext_kautionsabrechnung(kaution: Kaution) -> Kontext:
    """Platzhalter der Kautionsabrechnung.

    ``offene_posten`` setzt ab Schritt 5/6 die Sollstellung ein; bis dahin leer.
    """
    kontext = kontext_vertrag(kaution.vertrag)
    kontext["abrechnung"] = {
        "betrag": kaution.betrag,
        "eingang_am": kaution.eingang_am,
        "einbehalt": kaution.einbehalt,
        "ausgezahlt": kaution.ausgezahlt,
        "abgerechnet_am": kaution.abgerechnet_am,
        "offene_posten": [],
    }
    return kontext


def kontext_uebergabe(uebergabe: Uebergabe) -> Kontext:
    """Platzhalter des Übergabe- oder Rückgabeprotokolls."""
    kontext = kontext_vertrag(uebergabe.vertrag)
    kontext["uebergabe"] = {
        "art": uebergabe.get_art_display(),
        "datum": uebergabe.datum,
        "zaehlerstaende": list(uebergabe.zaehlerstaende),
        "schluessel": list(uebergabe.schluessel),
        "zustand": uebergabe.zustand,
        "zurueckgelassen": uebergabe.zurueckgelassen,
        "fotos": uebergabe.fotos.count(),
    }
    return kontext


# --- Anfordern -----------------------------------------------------------------------


def _anfordern(
    vorlagenart: str, bezug: models.Model, kontext: Kontext, versandweg: str
) -> Arbeitsposten:
    """Die eine Stelle, die bei Schritt 3c auf ``dokumente.services`` umgestellt wird.

    Bis dahin: Arbeitsposten mit Vorlagenart und Versandweg. Der Kontext wird nicht
    gespeichert (er enthält Personendaten); er entsteht beim Erzeugen neu.
    """
    return kern.arbeitsposten_anlegen(
        ART,
        bezug=bezug,
        hinweis=f"Vorlagenart {vorlagenart}, Versandweg {versandweg}",
    )


def vorlagenart_vertrag(vertrag: Vertrag) -> str:
    return konfiguration.vertragsart(vertrag.vertragsart).vorlage


def vertragsdokument_anfordern(vertrag: Vertrag) -> Arbeitsposten:
    """Vertrag und Anlagen aus der Vorlage der Vertragsart (H2.3)."""
    _, versandweg = VORLAGEN["vertrag"]
    return _anfordern(vorlagenart_vertrag(vertrag), vertrag, kontext_vertrag(vertrag), versandweg)


def kuendigungsbestaetigung_anfordern(kuendigung: Kuendigung) -> Arbeitsposten:
    """Bestätigung der Kündigung mit Fristende und Rückgabetermin (H2.8)."""
    vorlagenart, versandweg = VORLAGEN["kuendigungsbestaetigung"]
    return _anfordern(vorlagenart, kuendigung, kontext_kuendigung(kuendigung), versandweg)


def kautionsabrechnung_anfordern(kaution: Kaution) -> Arbeitsposten:
    """Abrechnung der Kaution nach der Rückgabe (H2.10)."""
    vorlagenart, versandweg = VORLAGEN["kautionsabrechnung"]
    return _anfordern(vorlagenart, kaution, kontext_kautionsabrechnung(kaution), versandweg)


def protokoll_anfordern(uebergabe: Uebergabe) -> Arbeitsposten:
    """Übergabe- oder Rückgabeprotokoll (H2.4, H2.9)."""
    schluessel = (
        "uebergabeprotokoll" if uebergabe.art == Uebergabe.Art.UEBERGABE else "rueckgabeprotokoll"
    )
    vorlagenart, versandweg = VORLAGEN[schluessel]
    return _anfordern(vorlagenart, uebergabe, kontext_uebergabe(uebergabe), versandweg)
