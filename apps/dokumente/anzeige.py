"""Schreiben lesbar anzeigen (Review 2, B13): „Zahlungserinnerung · V-0005 · Tom Bohnbach“
statt „zahlungserinnerung“, Bezug als V-Nummer statt „Vertrag #91“.

Nur für Max' Oberfläche: Titel mit Namen gehen nie in Logs, Notion oder den Status-MCP.
Der Bezug wird über seine Felder gelesen (V-Nummer am Vertrag, ``vertrag`` an Kündigung,
Kaution, Übergabe oder Mahnfall, Kürzel am Objekt), damit dieses Modul die Apps darüber
nicht kennen muss.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.urls import NoReverseMatch, reverse

from apps.dokumente import vorlagenarten
from apps.dokumente.models import Schreiben


@dataclass(frozen=True)
class Bezug:
    text: str
    url: str = ""
    #: Empfänger, wie Max ihn kennt (Mieter des Vertrags), sonst leer
    name: str = ""


def _url(name: str, pk: int) -> str:
    try:
        return reverse(name, args=[pk])
    except NoReverseMatch:
        return ""


def _vertrag(objekt: Any) -> Any:
    if objekt is None:
        return None
    if objekt._meta.model_name == "vertrag":
        return objekt
    return getattr(objekt, "vertrag", None)


def _mieter(vertrag: Any) -> str:
    try:
        parteien = vertrag.vertragsparteien.select_related("partei").order_by("pk")
    except AttributeError:
        return ""
    return ", ".join(vp.partei.anzeigename for vp in parteien if vp.rolle == "mieter")


def bezug(schreiben: Schreiben) -> Bezug:
    objekt = schreiben.bezug
    vertrag = _vertrag(objekt)
    if vertrag is not None and getattr(vertrag, "nummer", ""):
        return Bezug(vertrag.nummer, _url("stammdaten:vertrag", vertrag.pk), _mieter(vertrag))
    if objekt is not None and getattr(objekt, "kuerzel", ""):
        return Bezug(f"Objekt {objekt.kuerzel}", "", f"alle Nutzer: {objekt.kuerzel}")
    return Bezug(f"{schreiben.bezug_typ.name} #{schreiben.bezug_id}")


def art_titel(schreiben: Schreiben) -> str:
    try:
        return vorlagenarten.holen(schreiben.art).titel
    except vorlagenarten.UnbekannteVorlagenart:
        return schreiben.art


def titel(schreiben: Schreiben, zu: Bezug | None = None) -> str:
    """„Zahlungserinnerung · V-0005 · Tom Bohnbach“."""
    zu = zu or bezug(schreiben)
    return " · ".join(t for t in (art_titel(schreiben), zu.text, zu.name) if t)


def vorbereiten(liste: list[Schreiben]) -> list[Schreiben]:
    """Hängt ``titel_lesbar`` und ``bezug_lesbar`` an jedes Schreiben (für Listen)."""
    for schreiben in liste:
        zu = bezug(schreiben)
        schreiben.bezug_lesbar = zu  # type: ignore[attr-defined]
        schreiben.titel_lesbar = titel(schreiben, zu)  # type: ignore[attr-defined]
    return liste
