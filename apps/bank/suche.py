"""Suchquelle der Bank für die Suche in der Kopfzeile (Review 2, B1): Umsätze nach Betrag
(mit Komma oder Punkt), Gegenpartei und Verwendungszweck. Angemeldet in
``BankConfig.ready()``; der Treffer führt zum Umsatz im Kassenbuch."""

from __future__ import annotations

from collections.abc import Iterable

from django.db.models import Q
from django.urls import reverse

from apps.bank.models import Bankumsatz
from apps.kern import suche, zahlen
from apps.kern.suche import Anfrage, Treffer

#: Länge des Verwendungszwecks in der Zeile unter dem Treffer
ZWECK_LAENGE = 60
#: Ab so vielen Zeichen sucht ein Betrag auch im Zweck (kürzere Ziffern stehen überall).
ZIFFERN_IM_ZWECK = 4


def umsaetze(a: Anfrage, grenze: int) -> Iterable[Treffer]:
    bedingung = Q()
    if a.ist_betrag:
        betrag = Q()
        for von, bis in a.betraege:
            betrag |= Q(betrag__gte=von, betrag__lt=bis)
        bedingung = betrag
        if len(a.text) >= ZIFFERN_IM_ZWECK:
            # Ziffern können auch im Zweck stehen (Rechnungsnummer 4779)
            bedingung |= Q(verwendungszweck__icontains=a.text)
    else:
        for wort in a.text.split():
            bedingung &= Q(gegenpartei__icontains=wort) | Q(verwendungszweck__icontains=wort)
    abfrage = (
        Bankumsatz.objects.filter(bedingung)
        .select_related("konto")
        .order_by("-buchungsdatum", "-pk")[:grenze]
    )
    treffer = []
    for u in abfrage:
        vorzeichen = "−" if u.richtung == Bankumsatz.Richtung.AUSGANG else "+"
        zweck = " ".join(u.verwendungszweck.split())
        if len(zweck) > ZWECK_LAENGE:
            zweck = zweck[: ZWECK_LAENGE - 1] + "…"
        gegenpartei = u.gegenpartei or "ohne Gegenpartei"
        treffer.append(
            Treffer(
                titel=f"{vorzeichen}{zahlen.euro(u.betrag)} · {gegenpartei}",
                url=reverse("kassenbuch:umsatz", args=[u.pk]),
                unter=" · ".join(
                    t for t in (f"{u.buchungsdatum:%d.%m.%Y}", u.konto.bezeichnung, zweck) if t
                ),
            )
        )
    return treffer


def anmelden() -> None:
    suche.quelle_anmelden("umsaetze", "Umsätze", umsaetze, reihenfolge=40)
