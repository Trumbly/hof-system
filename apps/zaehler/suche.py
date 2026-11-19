"""Suchquelle der Zähler für die Suche in der Kopfzeile (Review 2, B1; R2-9): Zählernummer und
Lage. Angemeldet in ``ZaehlerConfig.ready()``; der Treffer führt zur Seite des Zählers."""

from __future__ import annotations

from collections.abc import Iterable

from django.db.models import Q
from django.urls import reverse

from apps.kern import suche, zahlen
from apps.kern.suche import Anfrage, Treffer
from apps.zaehler.models import Zaehler


def zaehler(a: Anfrage, grenze: int) -> Iterable[Treffer]:
    bedingung = Q()
    for wort in a.text.split():
        bedingung &= Q(nummer__icontains=wort) | Q(bezeichnung__icontains=wort)
    abfrage = (
        Zaehler.objects.filter(bedingung)
        .select_related("einheit", "objekt", "einheit__objekt")
        .order_by("ausgebaut_am", "nummer")[:grenze]
    )
    treffer = []
    for z in abfrage:
        letzter = z.letzter_stand()
        unter = [z.ort, z.bezeichnung]
        if letzter is not None:
            unter.append(f"Stand {zahlen.zahl(letzter.stand_text)} am {letzter.datum:%d.%m.%Y}")
        treffer.append(
            Treffer(
                titel=f"{z.get_art_display()} {z.nummer}",
                url=reverse("zaehler:zaehler", args=[z.pk]),
                unter=" · ".join(t for t in unter if t),
                status="" if z.ist_eingebaut else "neutral",
                status_text="" if z.ist_eingebaut else "ausgebaut",
            )
        )
    return treffer


def anmelden() -> None:
    suche.quelle_anmelden("zaehler", "Zähler", zaehler, reihenfolge=60)
