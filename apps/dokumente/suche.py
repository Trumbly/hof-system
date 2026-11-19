"""Suchquelle der Dokumente für die Suche in der Kopfzeile (Review 2, B1): Belege nach
Rechnungsnummer (des Belegs oder seiner Eingangsrechnung), Betrag (mit Komma oder Punkt)
und Lieferant. Verworfene Belege stehen hinten. Angemeldet in ``DokumenteConfig.ready()``.
"""

from __future__ import annotations

from collections.abc import Iterable

from django.db.models import Case, Q, Value, When
from django.urls import reverse

from apps.dokumente.models import Beleg
from apps.kern import suche, zahlen
from apps.kern.suche import Anfrage, Treffer


def belege(a: Anfrage, grenze: int) -> Iterable[Treffer]:
    nummer = Q(rechnungsnummer__icontains=a.text) | Q(
        eingangsrechnung__rechnungsnummer__icontains=a.text
    )
    if a.ist_betrag:
        bedingung = nummer
        for von, bis in a.betraege:
            bedingung |= Q(betrag__gte=von, betrag__lt=bis)
            bedingung |= Q(eingangsrechnung__betrag__gte=von, eingangsrechnung__betrag__lt=bis)
    else:
        bedingung = Q()
        for wort in a.text.split():
            bedingung &= (
                Q(rechnungsnummer__icontains=wort)
                | Q(eingangsrechnung__rechnungsnummer__icontains=wort)
                | Q(lieferant__name__icontains=wort)
            )
    ids = Beleg.objects.filter(bedingung).values("pk")
    abfrage = (
        Beleg.objects.filter(pk__in=ids)
        .alias(verworfen=Case(When(status=Beleg.Status.VERWORFEN, then=Value(1)), default=Value(0)))
        .select_related("lieferant", "dokument")
        .order_by("verworfen", "-dokument__datum", "-pk")[:grenze]
    )
    treffer = []
    for b in abfrage:
        titel = f"Beleg #{b.pk}"
        if b.rechnungsnummer:
            titel += f" · Rechnung {b.rechnungsnummer}"
        unter = (
            b.lieferant.name if b.lieferant else "Lieferant offen",
            zahlen.euro(b.betrag) if b.betrag is not None else "",
            f"{b.dokument.datum:%d.%m.%Y}",
        )
        treffer.append(
            Treffer(
                titel=titel,
                url=reverse("dokumente:beleg", args=[b.pk]),
                unter=" · ".join(t for t in unter if t),
                status=b.status if b.status != Beleg.Status.BESTAETIGT else "",
            )
        )
    return treffer


def anmelden() -> None:
    suche.quelle_anmelden("belege", "Belege", belege, reihenfolge=50)
