"""Stammdaten im Protokoll (Review 2, B14): Filter „nach Vertrag/Einheit“ und „nach Person“.

Der Filter ``?bezug=stammdaten.einheit:<ID>`` zeigt auch die Vorgänge der Verträge der
Einheit, ``?bezug=stammdaten.partei:<ID>`` die der Verträge der Partei.

Das Suchfeld des Protokolls (R2-9) findet Verträge (V-Nummer), Einheiten (NW-ID) und Parteien
(Name, auch Teile davon) und schlägt sie mit Namen vor; das ist Max' eigene Oberfläche.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from django.db import models
from django.db.models import Q

from apps.kern import vorgaenge
from apps.stammdaten.models import Einheit, Partei, Vertrag, Vertragspartei


def _vertraege_der_einheit(einheit: Einheit) -> Iterable[models.Model]:
    return Vertrag.objects.filter(einheiten=einheit).distinct()


def _vertraege_der_partei(partei: Partei) -> Iterable[models.Model]:
    return Vertrag.objects.filter(vertragsparteien__partei=partei).distinct()


#: „Tom Bohnbach · Partei #12“: Namen sind nicht eindeutig, die ID in der Klammer schon.
_PARTEI_ID = re.compile(r"Partei #(\d+)")


def _vertrag_text(vertrag: Vertrag) -> str:
    teile = [vertrag.nummer, *(e.nw_id for e in vertrag.einheiten.all())]
    teile += [
        vp.partei.anzeigename
        for vp in vertrag.vertragsparteien.all()
        if vp.rolle == Vertragspartei.Rolle.MIETER
    ]
    return " · ".join(t for t in teile if t)


def _einheit_text(einheit: Einheit) -> str:
    return " · ".join(t for t in (einheit.nw_id, einheit.bezeichnung) if t)


def _partei_text(partei: Partei) -> str:
    return f"{partei.anzeigename or 'ohne Namen'} · Partei #{partei.pk}"


def _suchen(text: str) -> list[models.Model]:
    """Suchfeld des Protokolls: V-Nummer, NW-ID oder Name (Teile des Namens genügen)."""
    text = text.strip()
    if not text:
        return []
    if treffer := _PARTEI_ID.search(text):
        return list(Partei.objects.filter(pk=int(treffer[1])))
    erstes = text.split("·", 1)[0].strip()
    if vertraege := list(Vertrag.objects.filter(nummer__iexact=erstes)):
        return list(vertraege)
    if einheiten := list(Einheit.objects.filter(nw_id__iexact=erstes)):
        return list(einheiten)
    parteien = Partei.objects.all()
    for wort in erstes.split():
        parteien = parteien.filter(
            Q(vorname__icontains=wort) | Q(nachname__icontains=wort) | Q(firma__icontains=wort)
        )
    return list(parteien.order_by("nachname", "vorname", "firma", "pk")[:10])


def _vorschlaege() -> list[str]:
    vertraege = Vertrag.objects.prefetch_related("einheiten", "vertragsparteien__partei").order_by(
        "nummer"
    )
    return [
        *(_vertrag_text(v) for v in vertraege),
        *(_einheit_text(e) for e in Einheit.objects.order_by("nw_id")),
        *(_partei_text(p) for p in Partei.objects.order_by("nachname", "vorname", "firma", "pk")),
    ]


def _beschriften(objekt: models.Model) -> str | None:
    if isinstance(objekt, Vertrag):
        return _vertrag_text(objekt)
    if isinstance(objekt, Einheit):
        return _einheit_text(objekt)
    if isinstance(objekt, Partei):
        return _partei_text(objekt)
    return None


def registrieren() -> None:
    vorgaenge.bezug_erweitern_registrieren(Einheit, _vertraege_der_einheit)
    vorgaenge.bezug_erweitern_registrieren(Partei, _vertraege_der_partei)
    vorgaenge.bezug_suche_registrieren(
        vorgaenge.BezugSuche(suchen=_suchen, vorschlaege=_vorschlaege, beschriften=_beschriften)
    )
