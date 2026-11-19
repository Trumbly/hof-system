"""Schreiben der Stammdaten über die App ``dokumente`` (Schritt 3c).

Die Stammdaten erzeugen hier Schreiben aus freigegebenen Vorlagen: Vertragsdokument
(H2.3), Kündigungsbestätigung (H2.8), Kautionsabrechnung (H2.10), Übergabe- und
Rückgabeprotokoll (H2.4, H2.9).

Jede Art hat
- eine **Vorlagenart** aus ``apps.dokumente.vorlagenarten`` (``vertrag``,
  ``kuendigungsbestaetigung``, ``kautionsabrechnung``, ``uebergabeprotokoll``,
  ``rueckgabeprotokoll``). Beim Vertrag nennt ``konfiguration.toml`` je Vertragsart den
  Namen der Vorlage (z. B. „Vertrag Garage“); fehlt sie, gilt die neueste freigegebene
  Vorlage der Art ``vertrag``.
- einen **Versandweg** (Standard der Vorlagenart; die Kündigungsbestätigung geht per Mail,
  wenn eine Adresse da ist, sonst als Brief) und
- einen **Kontext** (``kontext_vertrag`` usw.): nur einfache Werte (``Decimal``, ``date``,
  ``str``, Listen, Wörterbücher). Das Format setzt die Vorlage.

Das Schreiben entsteht im Status „bereit“ mit PDF; versendet wird es erst über den
Senden-Knopf (CLAUDE.md, Regel 4.8). Fehlt eine freigegebene Vorlage, fehlen Angaben zum
Empfänger oder lässt sich kein PDF erzeugen, entsteht statt eines Fehlers ein
Arbeitsposten „Schreiben erstellen“ mit Vorlagenart und Grund (ohne Namen).
"""

from __future__ import annotations

import contextlib
import contextvars
import datetime
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

from django.db import models, transaction

from apps.dokumente import pdf
from apps.dokumente import services as dokumente
from apps.dokumente.models import Schreiben
from apps.dokumente.vorlagenarten import Versandweg
from apps.kern import services as kern
from apps.kern import zeit
from apps.kern.models import Arbeitsposten
from apps.stammdaten import fristrechnung, konfiguration
from apps.stammdaten.models import (
    Einheit,
    Kaution,
    Kontaktweg,
    Kuendigung,
    Partei,
    Uebergabe,
    Vertrag,
    Vertragspartei,
)
from apps.zaehler import services as zaehler

#: Art des Arbeitspostens, wenn kein Schreiben entstehen kann.
ART = "Schreiben erstellen"

Kontext = dict[str, Any]
Ergebnis = Schreiben | Arbeitsposten

_OHNE_SCHREIBEN: contextvars.ContextVar[str] = contextvars.ContextVar(
    "stammdaten_ohne_schreiben", default=""
)


@contextlib.contextmanager
def ohne_schreiben(grund: str) -> Iterator[None]:
    """Legt in diesem Block statt Schreiben nur erledigte Arbeitsposten an (z. B. im
    Testhof, der keine Schreiben anlegt, damit gleicher Seed gleiche Daten ergibt). Sie
    stehen im Protokoll, aber in keiner Arbeitsliste und nicht im Cockpit."""
    marke = _OHNE_SCHREIBEN.set(grund)
    try:
        yield
    finally:
        _OHNE_SCHREIBEN.reset(marke)


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


def _briefanrede(partei: Partei) -> str:
    if partei.art == Partei.Art.FIRMA:
        return "Sehr geehrte Damen und Herren"
    if partei.anrede == "Frau" and partei.nachname:
        return f"Sehr geehrte Frau {partei.nachname}"
    if partei.anrede == "Herr" and partei.nachname:
        return f"Sehr geehrter Herr {partei.nachname}"
    return f"Guten Tag {partei.anzeigename}".strip()


def _empfaenger(partei: Partei | None) -> Kontext:
    """Empfänger im Format der App dokumente: name, anrede, anschrift, email."""
    if partei is None:
        return {"name": "", "anrede": "", "anschrift": "", "email": ""}
    zeilen = [partei.adresszusatz, partei.strasse, f"{partei.plz} {partei.ort}".strip()]
    mail = (
        partei.kontaktwege.filter(art=Kontaktweg.Art.EMAIL)
        .order_by("-bevorzugt", "pk")
        .values_list("wert", flat=True)
        .first()
    )
    return {
        "name": partei.anzeigename,
        "anrede": _briefanrede(partei),
        "anschrift": "\n".join(z for z in zeilen if z) if partei.hat_anschrift else "",
        "email": mail or "",
    }


def einheit_titel(einheit: Einheit) -> str:
    """Typ und Bezeichnung ohne Doppelung: „Garten 3“ statt „Garten Garten 3“ (Review 2,
    A11); ohne Bezeichnung „Garage NW-G-0004“."""
    typ = str(einheit.get_typ_display())
    bezeichnung = (einheit.bezeichnung or "").strip()
    if not bezeichnung:
        return f"{typ} {einheit.nw_id}"
    if bezeichnung.casefold().startswith(typ.casefold()):
        return bezeichnung
    return f"{typ} {bezeichnung}"


def _einheit(einheit: Einheit) -> Kontext:
    return {
        "nw_id": einheit.nw_id,
        "kuerzel": einheit.nw_id,
        "typ": einheit.get_typ_display(),
        "titel": einheit_titel(einheit),
        "bezeichnung": einheit.bezeichnung or einheit.nw_id,
        "flaeche_qm": einheit.flaeche_qm,
        "objekt": einheit.objekt.bezeichnung,
        "objekt_anschrift": einheit.objekt.anschrift,
    }


def _brutto(miete: Decimal, pauschale: Decimal, steuersatz: Decimal) -> Decimal:
    netto = miete + pauschale
    return (netto * (1 + steuersatz / 100)).quantize(Decimal("0.01"))


def kontext_vertrag(vertrag: Vertrag, stichtag: datetime.date | None = None) -> Kontext:
    """Platzhalter für Vertragsdokument und alle Schreiben zu einem Vertrag.

    ``empfaenger`` ist der erste Mieter; ``einheit`` und ``objekt`` die erste Einheit (alle
    stehen in ``einheiten``). ``kondition`` ist die am Stichtag (Standard: Vertragsbeginn)
    gültige; ``staffel`` nennt alle Stufen in zeitlicher Folge.
    """
    stichtag = stichtag or vertrag.beginn
    art = konfiguration.vertragsart(vertrag.vertragsart)
    regel = konfiguration.kuendigungsregel(vertrag.kuendigungsregel or art.kuendigungsregel)
    konditionen = list(vertrag.konditionen.order_by("gueltig_ab"))
    aktuell = vertrag.kondition_am(stichtag) or (konditionen[0] if konditionen else None)
    kaution: Kaution | None = Kaution.objects.filter(vertrag=vertrag).first()
    parteien = {
        rolle: [
            vp.partei
            for vp in vertrag.vertragsparteien.filter(rolle=rolle).select_related("partei")
        ]
        for rolle in ("mieter", "zahler", "buerge")
    }
    einheiten = list(vertrag.einheiten.select_related("objekt"))
    erste = einheiten[0] if einheiten else None
    kondition = (
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
    )
    return {
        "empfaenger": _empfaenger(parteien["mieter"][0] if parteien["mieter"] else None),
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
        "mieter": [_partei(p) for p in parteien["mieter"]],
        "zahler": [_partei(p) for p in parteien["zahler"]],
        "buergen": [_partei(p) for p in parteien["buerge"]],
        "einheit": _einheit(erste) if erste else {},
        "objekt": (
            {
                "kuerzel": erste.objekt.kuerzel,
                "bezeichnung": erste.objekt.bezeichnung,
                "anschrift": erste.objekt.anschrift,
            }
            if erste
            else {}
        ),
        "einheiten": [_einheit(e) for e in einheiten],
        "kondition": kondition,
        "betrag": {
            "miete": kondition["miete"] if kondition else None,
            "brutto": kondition["brutto"] if kondition else None,
            "kaution": kaution.betrag if kaution is not None else None,
        },
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
    regel = konfiguration.kuendigungsregel(kuendigung.kuendigungsregel)
    kontext["kuendigung"] = {
        "von": kuendigung.get_von_display(),
        "zugang_am": kuendigung.zugang_am,
        "zum_wunsch": kuendigung.zum_wunsch,
        "fristende": kuendigung.fristende,
        "rueckgabetermin": kuendigung.rueckgabetermin,
        "kuendigungsfrist": regel.titel,
        # Review 2, A3: Wie die Kündigung zuging. Bei Textform (Website, Mail) erkennt die
        # Bestätigung die Form nicht ausdrücklich an, bis der Anwalt das geklärt hat.
        "zugangsweg": kuendigung.get_zugangsweg_display() if kuendigung.zugangsweg else "",
        "textform": kuendigung.in_textform,
        "website": kuendigung.zugangsweg == Kuendigung.Zugangsweg.WEBSITE,
        "aufhebung": kuendigung.aufhebung,
    }
    if kuendigung.aufhebung:
        kontext["aufhebung"] = {
            "zum": kuendigung.fristende,
            "fristende_ordentlich": fristrechnung.fristende(kuendigung.zugang_am, regel),
            "rueckgabetermin": kuendigung.rueckgabetermin,
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
        "notiz": kaution.abrechnung_notiz,
        "offene_posten": [],
    }
    return kontext


def kontext_uebergabe(uebergabe: Uebergabe) -> Kontext:
    """Platzhalter des Übergabe- oder Rückgabeprotokolls."""
    kontext = kontext_vertrag(uebergabe.vertrag)
    kontext["uebergabe"] = {
        "art": uebergabe.get_art_display(),
        "datum": uebergabe.datum,
        "zaehlerstaende": zaehler.uebergabe_staende_protokoll(uebergabe),
        "schluessel": list(uebergabe.schluessel),
        "zustand": uebergabe.zustand,
        "zurueckgelassen": uebergabe.zurueckgelassen,
        "fotos": uebergabe.fotos.count(),
    }
    return kontext


# --- Erzeugen ------------------------------------------------------------------------


def _arbeitsposten(vorlagenart: str, bezug: models.Model, grund: str) -> Arbeitsposten:
    return kern.arbeitsposten_anlegen(
        ART, bezug=bezug, hinweis=f"Vorlagenart {vorlagenart}: {grund}"
    )


def _erzeugen(
    vorlagenart: str,
    bezug: models.Model,
    kontext: Kontext,
    versandweg: str | None = None,
    *,
    name: str | None = None,
) -> Ergebnis:
    """Die eine Stelle, die ``dokumente.schreiben_erzeugen`` aufruft.

    ``name`` wählt eine bestimmte Vorlage der Art; fehlt sie, gilt die neueste freigegebene
    der Art. Geht es nicht, entsteht ein Arbeitsposten mit dem Grund (Meldungen der App
    dokumente nennen keine Namen).
    """
    grund = _OHNE_SCHREIBEN.get()
    if grund:
        # Gewollt kein Schreiben (Testhof, Tests): Der Posten hält das nur fest und ist
        # gleich erledigt; er wartet auf niemanden (Review 1, Punkt 7).
        return kern.arbeitsposten_erledigen(_arbeitsposten(vorlagenart, bezug, grund))
    try:
        with transaction.atomic():
            try:
                vorlage = dokumente.freigegebene_vorlage(vorlagenart, name)
            except dokumente.VorlageNichtFreigegeben:
                if name is None:
                    raise
                vorlage = dokumente.freigegebene_vorlage(vorlagenart)
            return dokumente.schreiben_erzeugen(vorlage, bezug, kontext, versandweg)
    except (
        dokumente.SchreibenFehler,
        dokumente.PlatzhalterFehler,
        pdf.PdfNichtVerfuegbar,
    ) as fehler:
        return _arbeitsposten(vorlagenart, bezug, str(fehler))


def _mail_oder_brief(kontext: Kontext) -> str:
    return Versandweg.MAIL if kontext["empfaenger"]["email"] else Versandweg.BRIEF


def vertragsdokument_erzeugen(vertrag: Vertrag) -> Ergebnis:
    """Vertrag und Anlagen aus der Vorlage der Vertragsart (H2.3); unterschrieben wird vor
    Ort (Versandweg „selbst“)."""
    name = konfiguration.vertragsart(vertrag.vertragsart).vorlage
    return _erzeugen("vertrag", vertrag, kontext_vertrag(vertrag), name=name)


def kuendigungsbestaetigung_erzeugen(kuendigung: Kuendigung) -> Ergebnis:
    """Bestätigung der Kündigung mit Fristende und Rückgabetermin (H2.8)."""
    kontext = kontext_kuendigung(kuendigung)
    return _erzeugen("kuendigungsbestaetigung", kuendigung, kontext, _mail_oder_brief(kontext))


def aufhebungsvertrag_erzeugen(kuendigung: Kuendigung) -> Ergebnis:
    """Aufhebungsvertrag zum Wunschtermin vor dem Fristende (Review 2, A11). Beide Seiten
    unterschreiben; bis dahin gilt die ordentliche Kündigung."""
    kontext = kontext_kuendigung(kuendigung)
    return _erzeugen("aufhebungsvertrag", kuendigung, kontext, _mail_oder_brief(kontext))


def kautionsabrechnung_erzeugen(kaution: Kaution) -> Ergebnis:
    """Abrechnung der Kaution nach der Rückgabe (H2.10)."""
    kontext = kontext_kautionsabrechnung(kaution)
    weg = Versandweg.BRIEF if kontext["empfaenger"]["anschrift"] else _mail_oder_brief(kontext)
    return _erzeugen("kautionsabrechnung", kaution, kontext, weg)


def protokoll_erzeugen(uebergabe: Uebergabe) -> Ergebnis:
    """Übergabe- oder Rückgabeprotokoll (H2.4, H2.9)."""
    art = "uebergabeprotokoll" if uebergabe.art == Uebergabe.Art.UEBERGABE else "rueckgabeprotokoll"
    return _erzeugen(art, uebergabe, kontext_uebergabe(uebergabe))


# --- Serienmitteilung an Nutzer (H8.4; Review 2, A8) ---------------------------------


def _vertrag_bezeichnung(vertrag: Vertrag) -> str:
    """Zeile der Auswahlliste: V-Nummer, Einheiten und Mieter (Max' Oberfläche)."""
    einheiten = ", ".join(e.nw_id for e in vertrag.einheiten.all())
    mieter = ", ".join(
        vp.partei.anzeigename
        for vp in vertrag.vertragsparteien.all()
        if vp.rolle == Vertragspartei.Rolle.MIETER
    )
    return " · ".join(t for t in (vertrag.nummer, einheiten, mieter) if t)


def _vertraege_fuer_serie() -> models.QuerySet[Vertrag]:
    """Laufende Verträge (aktiv oder gekündigt), ohne Wohnraum (den führt Verwaltio)."""
    return (
        Vertrag.objects.laufend()
        .exclude(einheiten__typ=Einheit.Typ.WOHNUNG)
        .prefetch_related("einheiten", "vertragsparteien__partei")
        .distinct()
        .order_by("nummer")
    )


def vertrag_anmelden() -> None:
    """Meldet Verträge als Bezug der Serienmitteilung an (Empfänger: erster Mieter)."""
    from apps.dokumente import bezuege

    bezuege.registrieren(
        Vertrag,
        titel="Verträge",
        kontext=lambda v: kontext_vertrag(v, zeit.heute()),
        auswahl=_vertraege_fuer_serie,
        bezeichnung=_vertrag_bezeichnung,
        filtern=_vertraege_eingrenzen,
        nach_typ=True,
    )


def _vertraege_eingrenzen(
    auswahl: models.QuerySet[Vertrag], objekt: Any, typ: str
) -> models.QuerySet[Vertrag]:
    """Serienmitteilung nach Objekt und Typ der Einheit (Review 2, B13)."""
    if objekt is not None:
        auswahl = auswahl.filter(einheiten__objekt=objekt)
    if typ:
        auswahl = auswahl.filter(einheiten__typ=typ)
    return auswahl.distinct()
