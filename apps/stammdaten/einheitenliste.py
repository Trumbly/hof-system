"""Lage der Einheiten an einem Tag (Review 2, B6 und B11). Nur lesen, nichts schreiben.

Für jede Einheit: Status heute (frei, vermietet, gekündigt, Vertrag im Entwurf), der Vertrag,
der sie bindet, die Mieter, die Miete heute, was offen ist (aus ``soll.offen_abfrage``, also
dieselbe Wahrheit wie überall), die Zielmiete und ab wann sie frei ist.

Genutzt von der Liste „Einheiten“, der Einheitenseite und der Einheitenauswahl von „Neuer
Vertrag“ (nur freie Einheiten, Schalter „alle“). Ein paar Abfragen für alle Einheiten
zusammen statt einer je Einheit.

Die Lage enthält Namen (Mieter) für Max' Oberfläche; nie in Logs oder Meldungen schreiben.
"""

from __future__ import annotations

import datetime
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import F, Q, QuerySet, Sum

from apps.stammdaten.models import (
    BINDEND,
    Einheit,
    EinheitStatus,
    Kondition,
    Vertrag,
    Vertragseinheit,
    Vertragspartei,
    Zielmiete,
)

NULL = Decimal("0.00")
EIN_TAG = datetime.timedelta(days=1)

FREI = "frei"
VERMIETET = "vermietet"
GEKUENDIGT = "gekuendigt"
ENTWURF = "entwurf"
WOHNRAUM = "wohnraum"
AUFGELOEST = "aufgeloest"

#: Status → (Text, Schlüssel des Badges in ``apps.kern.templatetags.bausteine.BADGES``)
STATUS: dict[str, tuple[str, str]] = {
    FREI: ("frei", "hinweis"),
    VERMIETET: ("vermietet", "aktiv"),
    GEKUENDIGT: ("gekündigt", "gekuendigt"),
    ENTWURF: ("Vertrag im Entwurf", "entwurf"),
    WOHNRAUM: ("Verwaltio", "neutral"),
    AUFGELOEST: ("aufgelöst", "neutral"),
}

#: Filter der Liste „Einheiten“ (Review 2, B11): Schlüssel, Titel.
STATUS_FILTER: tuple[tuple[str, str], ...] = (
    ("", "alle Status"),
    (VERMIETET, "vermietet"),
    (FREI, "frei"),
    (GEKUENDIGT, "gekündigt"),
    (ENTWURF, "Vertrag im Entwurf"),
)

_VERTRAG_STATUS = {
    Vertrag.Status.AKTIV: VERMIETET,
    Vertrag.Status.GEKUENDIGT: GEKUENDIGT,
    Vertrag.Status.ENTWURF: ENTWURF,
}


@dataclass
class Lage:
    einheit: Einheit
    status: str
    #: Der Vertrag, der die Einheit heute bindet (oder als nächster beginnt).
    vertrag: Vertrag | None = None
    #: Ein weiterer Vertrag nach ihm (z. B. Entwurf des Nachmieters nach einer Kündigung).
    nachfolger: Vertrag | None = None
    mieter: list[str] = field(default_factory=list)
    miete: Decimal | None = None
    #: Offen bis heute (Rückstand), aus ``soll.offen_abfrage``.
    offen: Decimal = NULL
    zielmiete: Decimal | None = None
    #: Frei seit (Status „frei“) bzw. frei ab dem Tag nach dem Vertragsende.
    frei_ab: datetime.date | None = None
    #: Status der Einheit aus ihrer Zeitreihe, wenn er etwas anderes sagt (z. B. gesperrt).
    einheit_status: str = ""

    @property
    def status_text(self) -> str:
        return STATUS[self.status][0]

    @property
    def badge(self) -> str:
        return STATUS[self.status][1]

    @property
    def ist_frei(self) -> bool:
        return self.status == FREI

    @property
    def mieter_text(self) -> str:
        return ", ".join(self.mieter)


def _gueltig(heute: datetime.date) -> Q:
    return Q(gueltig_ab__lte=heute) & (Q(gueltig_bis__isnull=True) | Q(gueltig_bis__gte=heute))


def offen_je_vertrag(vertrag_ids: Iterable[int], heute: datetime.date) -> dict[int, Decimal]:
    # Spät importiert: ``soll`` importiert die Stammdaten.
    from apps.soll import services as soll

    zeilen = (
        soll.offen_abfrage(stichtag=heute)
        .filter(vertrag_id__in=list(vertrag_ids))
        .order_by()
        .values("vertrag_id")
        .annotate(summe=Sum(F("betrag") - F("bezahlt")))
        .values_list("vertrag_id", "summe")
    )
    return {pk: summe or NULL for pk, summe in zeilen}


def lagen(heute: datetime.date, einheiten: QuerySet[Einheit] | None = None) -> list[Lage]:
    """Lage jeder Einheit am Tag ``heute`` (Standard: alle Einheiten), sortiert nach Objekt
    und NW-ID."""
    liste = list(
        (einheiten if einheiten is not None else Einheit.objects.all())
        .select_related("objekt")
        .order_by("objekt__kuerzel", "nw_id")
    )
    ids = [e.pk for e in liste]

    je_einheit: dict[int, list[Vertrag]] = defaultdict(list)
    for ve in Vertragseinheit.objects.filter(
        einheit_id__in=ids, vertrag__status__in=BINDEND
    ).select_related("vertrag"):
        je_einheit[ve.einheit_id].append(ve.vertrag)
    vertrag_ids = {v.pk for vs in je_einheit.values() for v in vs}

    mieter: dict[int, list[str]] = defaultdict(list)
    for vp in (
        Vertragspartei.objects.filter(vertrag_id__in=vertrag_ids, rolle=Vertragspartei.Rolle.MIETER)
        .select_related("partei")
        .order_by("pk")
    ):
        mieter[vp.vertrag_id].append(vp.partei.anzeigename)

    # Miete heute; bei einem Entwurf, der erst später beginnt, die erste Kondition.
    miete: dict[int, Decimal] = {}
    for k in Kondition.objects.filter(vertrag_id__in=vertrag_ids).order_by("gueltig_ab"):
        if k.vertrag_id not in miete or (
            k.gueltig_ab <= heute and (k.gueltig_bis is None or k.gueltig_bis >= heute)
        ):
            miete[k.vertrag_id] = k.miete

    offen = offen_je_vertrag(vertrag_ids, heute)
    ziel = {
        z.einheit_id: z.betrag
        for z in Zielmiete.objects.filter(_gueltig(heute), einheit_id__in=ids)
    }
    stand = {
        s.einheit_id: s for s in EinheitStatus.objects.filter(_gueltig(heute), einheit_id__in=ids)
    }

    ergebnis: list[Lage] = []
    for einheit in liste:
        status_stand = stand.get(einheit.pk)
        if einheit.ist_wohnraum:
            ergebnis.append(Lage(einheit, WOHNRAUM))
            continue
        if einheit.aufgeloest_am is not None:
            ergebnis.append(Lage(einheit, AUFGELOEST))
            continue
        vertraege = sorted(
            je_einheit.get(einheit.pk, []), key=lambda v: (v.beginn > heute, v.beginn)
        )
        lage = Lage(einheit, FREI, zielmiete=ziel.get(einheit.pk))
        if status_stand is not None and status_stand.status not in (
            EinheitStatus.Status.FREI,
            EinheitStatus.Status.VERMIETET,
        ):
            lage.einheit_status = status_stand.get_status_display()
        if not vertraege:
            if status_stand is not None and status_stand.status == EinheitStatus.Status.FREI:
                lage.frei_ab = status_stand.gueltig_ab
        else:
            v = vertraege[0]
            lage.status = _VERTRAG_STATUS[v.status]
            lage.vertrag = v
            lage.nachfolger = vertraege[1] if len(vertraege) > 1 else None
            lage.mieter = mieter.get(v.pk, [])
            lage.miete = miete.get(v.pk)
            lage.offen = offen.get(v.pk, NULL)
            if v.ende is not None and lage.nachfolger is None:
                lage.frei_ab = v.ende + EIN_TAG
        ergebnis.append(lage)
    return ergebnis


def lage(einheit: Einheit, heute: datetime.date) -> Lage:
    return lagen(heute, Einheit.objects.filter(pk=einheit.pk))[0]


def filtern(
    liste: list[Lage],
    *,
    suche: str = "",
    typ: str = "",
    objekt: str = "",
    status: str = "",
) -> list[Lage]:
    """Filter der Liste „Einheiten“: Suche in NW-ID, Bezeichnung, Objekt und Mieter;
    Typ, Objekt (Kürzel) und Status."""
    worte = suche.casefold().split()

    def passt(lage: Lage) -> bool:
        e = lage.einheit
        if typ and e.typ != typ:
            return False
        if objekt and e.objekt.kuerzel != objekt:
            return False
        if status and lage.status != status:
            return False
        if worte:
            text = " ".join(
                [
                    e.nw_id,
                    e.bezeichnung,
                    e.objekt.kuerzel,
                    lage.vertrag.nummer if lage.vertrag else "",
                    *lage.mieter,
                ]
            ).casefold()
            return all(w in text for w in worte)
        return True

    return [lage for lage in liste if passt(lage)]
