"""Klärfall im Posteingang der Arbeitslisten und auf seiner Seite (Review 2, B4).

Links der Umsatz (Betrag, Gegenpartei, Zweck), rechts der Vorschlag und bis zu zwei
Alternativen als Karten: Vertrag, Einheit, Mieter und offene Posten (aus ``soll.offen``). Ein
Klick auf „Zuordnen“ ordnet zu. Darunter „anderen Vertrag suchen“ als Suchfeld mit Namen.

Zeigt die Zahlerregel des Zahlers auf genau einen anderen Vertrag als die gewählte Karte, ist
„Zahlerregel auf diesen Vertrag ändern“ angehakt (Review 2, A11 und B4); die Karte sagt dazu,
dass sie die Regel ändert.

Namen und offene Posten liest die Zeile beim Anzeigen aus den Verträgen; der Hinweistext des
Postens nennt weiter nur IDs (er geht nach Notion und ins Log).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.db.models import Q, QuerySet
from django.urls import reverse

from apps.abgleich import services
from apps.abgleich.models import Klaerfall, Zahlerregel
from apps.kern import posteingang
from apps.kern.models import Arbeitsposten, Benutzer
from apps.kern.posteingang import Ergebnis, PostenFehler
from apps.soll import services as soll
from apps.soll.services import OffenerPosten
from apps.stammdaten.models import Vertrag, Vertragspartei

#: Höchstens so viele Karten: Vorschlag und zwei Alternativen (B4).
KARTEN = 3
#: So viele Treffer zeigt „anderen Vertrag suchen“.
SUCHTREFFER = 5
#: So viele offene Posten stehen auf einer Karte (der Rest als Zahl).
POSTEN_AUF_KARTE = 3


@dataclass
class Karte:
    vertrag: Vertrag
    einheiten: str
    mieter: str
    offen: list[OffenerPosten]
    summe_offen: Decimal
    vorschlag: bool = False
    #: Wählt man diese Karte, ändert „Zahlerregel ändern“ die Regel (sie zeigt woandershin).
    aendert_regel: bool = False

    @property
    def offen_kurz(self) -> list[OffenerPosten]:
        return self.offen[:POSTEN_AUF_KARTE]

    @property
    def offen_weitere(self) -> int:
        return max(0, len(self.offen) - POSTEN_AUF_KARTE)


def _mieter(vertrag: Vertrag) -> str:
    parteien = list(vertrag.vertragsparteien.all())
    mieter = [vp for vp in parteien if vp.rolle == Vertragspartei.Rolle.MIETER] or parteien
    return ", ".join(vp.partei.anzeigename for vp in mieter if vp.partei.anzeigename)


def _vertraege(ids: Iterable[int]) -> QuerySet[Vertrag]:
    return Vertrag.objects.filter(pk__in=list(ids)).prefetch_related(
        "einheiten", "vertragsparteien__partei"
    )


def karten_zu(vertraege: Iterable[Vertrag], *, regel: Zahlerregel | None) -> list[Karte]:
    karten = []
    for vertrag in vertraege:
        offen = soll.offen(vertrag)
        karten.append(
            Karte(
                vertrag=vertrag,
                einheiten=", ".join(e.nw_id for e in vertrag.einheiten.all()),
                mieter=_mieter(vertrag),
                offen=offen.posten,
                summe_offen=offen.summe_offen,
                aendert_regel=(
                    regel is not None
                    and regel.vertrag_id is not None
                    and regel.vertrag_id != vertrag.pk
                ),
            )
        )
    return karten


def _regel(fall: Klaerfall) -> Zahlerregel | None:
    """Die Zahlerregel des Zahlers, wenn sie auf genau einen Vertrag zeigt."""
    regel = services.zahlerregel_zu(fall.buchung)
    if regel is None or regel.vertrag_id is None:
        return None
    return regel


def vorschlag_id(fall: Klaerfall) -> int | None:
    wert = (fall.vorschlag or {}).get("vertrag")
    return int(wert) if wert else None


def karten(fall: Klaerfall) -> list[Karte]:
    """Vorschlag und bis zu zwei Alternativen (in dieser Reihenfolge)."""
    vorschlag = vorschlag_id(fall)
    ids = [vorschlag] if vorschlag else []
    for i in (fall.vorschlag or {}).get("alternativen", []):
        if int(i) not in ids:
            ids.append(int(i))
    ids = ids[:KARTEN]
    nach_id = {v.pk: v for v in _vertraege(ids)}
    liste = karten_zu([nach_id[i] for i in ids if i in nach_id], regel=_regel(fall))
    for karte in liste:
        karte.vorschlag = karte.vertrag.pk == vorschlag
    return liste


def suchen(text: str) -> list[Vertrag]:
    """Verträge zu V-Nummer, NW-ID oder Name (Vor-, Nachname, Firma) einer Partei."""
    text = text.strip()
    if len(text) < 2:
        return []
    bedingung = Q()
    for wort in text.split():
        bedingung &= (
            Q(nummer__icontains=wort)
            | Q(einheiten__nw_id__icontains=wort)
            | Q(vertragsparteien__partei__vorname__icontains=wort)
            | Q(vertragsparteien__partei__nachname__icontains=wort)
            | Q(vertragsparteien__partei__firma__icontains=wort)
        )
    ids = list(
        Vertrag.objects.exclude(status=Vertrag.Status.ENTWURF)
        .filter(bedingung)
        .order_by("nummer")
        .values_list("pk", flat=True)
        .distinct()[: SUCHTREFFER * 4]
    )
    gesehen: list[int] = []
    for i in ids:
        if i not in gesehen:
            gesehen.append(i)
    nach_id = {v.pk: v for v in _vertraege(gesehen[:SUCHTREFFER])}
    return [nach_id[i] for i in gesehen[:SUCHTREFFER] if i in nach_id]


def klaerfall_kontext(fall: Klaerfall) -> dict[str, Any]:
    """Was Seite und Zeile zeigen: Umsatz, Karten, Zahlerregel."""
    buchung = fall.buchung
    regel = _regel(fall)
    liste = karten(fall) if fall.art != Klaerfall.Art.RUECKBUCHUNG else []
    return {
        "klaerfall": fall,
        "buchung": buchung,
        "umsatz": buchung.umsatz,
        "offen_betrag": services.offener_betrag(buchung),
        "karten": liste,
        "zahlerregel": regel,
        # Vorangehakt, sobald es eine Zahlerregel mit einem Vertrag gibt: Sie ändert sich nur,
        # wenn der gewählte Vertrag (Karte oder Suche) von ihr abweicht.
        "regel_haken": regel is not None,
        "kann_suchen": fall.art != Klaerfall.Art.RUECKBUCHUNG,
    }


def zeilen_kontext(posten: Sequence[Arbeitsposten]) -> dict[int, dict[str, Any]]:
    faelle = {
        f.pk: f
        for f in Klaerfall.objects.filter(
            pk__in=[p.bezug_id for p in posten if p.bezug_id]
        ).select_related("buchung__umsatz__konto")
    }
    ergebnis: dict[int, dict[str, Any]] = {}
    for p in posten:
        fall = faelle.get(p.bezug_id or 0)
        if fall is None:
            continue
        kontext = klaerfall_kontext(fall)
        umsatz = kontext["umsatz"]
        kontext["titel"] = (
            f"{umsatz.gegenpartei or 'Zahlung ohne Gegenpartei'} · {fall.get_art_display()}"
        )
        kontext["kann_uebernehmen"] = fall.offen and any(k.vorschlag for k in kontext["karten"])
        # Enter klickt „Zuordnen“ auf der Karte des Vorschlags; kein zweiter Knopf im Kopf.
        kontext["uebernehmen_in_flaeche"] = True
        ergebnis[p.pk] = kontext
    return ergebnis


def _zahl(text: str | None) -> int:
    return int(text) if text and text.isdigit() else 0


def zuordnen(fall: Klaerfall, vertrag: Vertrag, *, zahlerregel_aendern: bool) -> Ergebnis:
    """Ordnet den Klärfall dem Vertrag zu (Rest als Guthaben) und meldet es mit Link."""
    regel = services.zahlerregel_weicht_ab(fall, vertrag) if zahlerregel_aendern else None
    try:
        services.klaerfall_zuordnen(
            fall, vertrag, rest_als_guthaben=True, zahlerregel_aendern=zahlerregel_aendern
        )
    except services.AbgleichFehler as fehler:
        raise PostenFehler(str(fehler)) from None
    text = f"Klärfall #{fall.pk}: Zahlung Vertrag {vertrag.nummer} zugeordnet."
    if regel is not None:
        text += f" Zahlerregel #{regel.pk} zeigt jetzt auf {vertrag.nummer}."
    return Ergebnis(
        text=text,
        link=reverse("abgleich:mietkonto", args=[vertrag.pk]),
        link_text="Mietkonto ansehen",
    )


def uebernehmen(posten: Arbeitsposten, benutzer: Benutzer, werte: Mapping[str, str]) -> Ergebnis:
    """Ein Klick auf eine Karte (``vertrag``) oder Enter (Vorschlag)."""
    fall = Klaerfall.objects.select_related("buchung__umsatz").filter(pk=posten.bezug_id).first()
    if fall is None:
        raise PostenFehler(f"Den Klärfall zu Posten #{posten.pk} gibt es nicht mehr.")
    vertrag_id = _zahl(werte.get("vertrag")) or vorschlag_id(fall)
    vertrag = Vertrag.objects.filter(pk=vertrag_id).first() if vertrag_id else None
    if vertrag is None:
        raise PostenFehler("Es gibt keinen Vorschlag. Bitte eine Karte wählen oder suchen.")
    # Ohne Werte aus der Zeile wie ihr Haken: angehakt, wenn die Regel abweicht.
    aendern = werte.get("zahlerregel") == "1" if posteingang.aus_zeile(werte) else True
    return zuordnen(fall, vertrag, zahlerregel_aendern=aendern)


def _bearbeiten(posten: Arbeitsposten) -> str:
    return reverse("abgleich:klaerfall", args=[posten.bezug_id])


posteingang.registrieren(
    posteingang.Bearbeitung(
        art=services.ART_KLAEREN,
        vorlage="abgleich/_klaerfall_zeile.html",
        kontext=zeilen_kontext,
        uebernehmen=uebernehmen,
        bearbeiten=_bearbeiten,
    )
)
