"""Anstehende Übergaben und Rückgaben (Review 2, C1): Ziel des Knopfs „Übergabe“ unten am
Handy.

Drei Listen:
- begonnen: Erfassungen vor Ort, die noch nicht abgeschlossen sind (weiter, wo es aufhörte),
- Übergaben: Verträge im Entwurf, nach Beginn (die Übergabe macht den Vertrag aktiv),
- Rückgaben: gekündigte Verträge, nach Rückgabetermin bzw. Ende.

Nur lesend; „beginnen“ geht an ``stammdaten:uebergabe_starten`` (Schreibweg über services).
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any

from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.kern import zeit
from apps.stammdaten.models import Kuendigung, Uebergabe, Vertrag, Vertragspartei


@dataclass(frozen=True)
class Zeile:
    vertrag: Vertrag
    art: str  # uebergabe oder rueckgabe
    datum: datetime.date | None
    mieter: str
    einheiten: str
    begonnen: Uebergabe | None = None

    @property
    def art_titel(self) -> str:
        return "Übergabe" if self.art == Uebergabe.Art.UEBERGABE else "Rückgabe"


def _vertraege() -> Prefetch[Any, Any, Any]:
    return Prefetch(
        "vertragsparteien",
        queryset=Vertragspartei.objects.filter(rolle=Vertragspartei.Rolle.MIETER).select_related(
            "partei"
        ),
        to_attr="mieter",
    )


def _zeile(
    vertrag: Vertrag,
    art: str,
    datum: datetime.date | None,
    begonnen: Uebergabe | None = None,
) -> Zeile:
    return Zeile(
        vertrag=vertrag,
        art=art,
        datum=datum,
        mieter=", ".join(vp.partei.anzeigename for vp in getattr(vertrag, "mieter", [])),
        einheiten=", ".join(e.nw_id for e in vertrag.einheiten.all()),
        begonnen=begonnen,
    )


def listen() -> dict[str, list[Zeile]]:
    begonnen_qs = (
        Uebergabe.objects.filter(status=Uebergabe.Status.BEGONNEN)
        .prefetch_related(
            Prefetch(
                "vertrag",
                queryset=Vertrag.objects.prefetch_related("einheiten", _vertraege()),
            )
        )
        .order_by("datum", "pk")
    )
    begonnen = [_zeile(u.vertrag, u.art, u.datum, begonnen=u) for u in begonnen_qs]
    schon = {(z.vertrag.pk, z.art) for z in begonnen}

    entwuerfe = (
        Vertrag.objects.filter(status=Vertrag.Status.ENTWURF)
        .prefetch_related("einheiten", _vertraege())
        .order_by("beginn", "nummer")
    )
    uebergaben = [
        _zeile(v, Uebergabe.Art.UEBERGABE, v.beginn)
        for v in entwuerfe
        if (v.pk, Uebergabe.Art.UEBERGABE) not in schon
    ]

    gekuendigt = Vertrag.objects.filter(status=Vertrag.Status.GEKUENDIGT).prefetch_related(
        "einheiten",
        _vertraege(),
        Prefetch(
            "kuendigungen",
            queryset=Kuendigung.objects.order_by("-zugang_am", "-pk"),
            to_attr="kuendigungen_neu",
        ),
    )
    rueckgaben = []
    for v in gekuendigt:
        if (v.pk, Uebergabe.Art.RUECKGABE) in schon:
            continue
        kuendigung = v.kuendigungen_neu[0] if v.kuendigungen_neu else None  # type: ignore[attr-defined]
        termin = kuendigung.rueckgabetermin if kuendigung else v.ende
        rueckgaben.append(_zeile(v, Uebergabe.Art.RUECKGABE, termin))
    rueckgaben.sort(key=lambda z: (z.datum or datetime.date.max, z.vertrag.nummer))
    return {"begonnen": begonnen, "uebergaben": uebergaben, "rueckgaben": rueckgaben}


@login_required
@require_GET
def uebergaben(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "stammdaten/uebergaben.html",
        {**listen(), "heute": zeit.heute()},
    )
