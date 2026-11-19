"""Links an den Arbeitsposten der Stammdaten (Review 1, Punkt 8; ``apps.kern.verweise``).

Jeder Posten verlinkt auf seinen Bezug: Vertrag, Kaution, Checkliste oder Einheit. Die
Beschreibung nennt V-Nummer, Einheit und Mieter; sie entsteht beim Anzeigen und steht nur in
der Oberfläche, nie im Hinweistext des Postens.
"""

from __future__ import annotations

from django.urls import reverse

from apps.kern import verweise
from apps.stammdaten.models import Checkliste, Einheit, Kaution, Vertrag, Vertragspartei


def vertrag_text(vertrag: Vertrag) -> str:
    """„V-0005 · NW-G-0018 · Tom Beispiel“ (Mieter; ohne Mieter die erste Partei)."""
    einheiten = ", ".join(e.nw_id for e in vertrag.einheiten.all())
    parteien = list(vertrag.vertragsparteien.select_related("partei").order_by("rolle", "pk"))
    mieter = [vp for vp in parteien if vp.rolle == Vertragspartei.Rolle.MIETER] or parteien
    namen = ", ".join(vp.partei.anzeigename for vp in mieter if vp.partei.anzeigename)
    return " · ".join(t for t in (vertrag.nummer, einheiten, namen) if t)


def _vertrag(vertrag: Vertrag) -> str:
    return reverse("stammdaten:vertrag", args=[vertrag.pk])


def _kaution(kaution: Kaution) -> str:
    return reverse("stammdaten:vertrag", args=[kaution.vertrag_id]) + "#kaution"


def _checkliste(liste: Checkliste) -> str:
    if liste.vertrag_id:
        return reverse("stammdaten:vertrag", args=[liste.vertrag_id]) + "#checklisten"
    return reverse("stammdaten:einheit", args=[liste.einheit_id]) + "#checklisten"


def _checkliste_text(liste: Checkliste) -> str:
    if liste.vertrag is not None:
        return vertrag_text(liste.vertrag)
    return liste.einheit.nw_id if liste.einheit is not None else ""


verweise.registrieren(Vertrag, "Vertrag öffnen", _vertrag, beschreibung=vertrag_text)
verweise.registrieren(
    Kaution, "Kaution abrechnen", _kaution, beschreibung=lambda k: vertrag_text(k.vertrag)
)
verweise.registrieren(Checkliste, "Checkliste öffnen", _checkliste, beschreibung=_checkliste_text)
verweise.registrieren(
    Einheit,
    "Einheit öffnen",
    lambda e: reverse("stammdaten:einheit", args=[e.pk]),
    beschreibung=lambda e: e.nw_id,
)
