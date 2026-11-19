"""Suchquellen der Stammdaten für die Suche in der Kopfzeile (Review 2, B1).

Einheiten (NW-ID, Bezeichnung, Objekt), Verträge (V-Nummer, Einheit, Name der Mieter) und
Parteien (Name, Firma, Ort, PLZ, Straße, E-Mail und Telefon). Angemeldet in
``StammdatenConfig.ready()``.
"""

from __future__ import annotations

from collections.abc import Iterable

from django.db.models import Case, F, Prefetch, Q, Value, When
from django.urls import reverse

from apps.kern import suche
from apps.kern.suche import Anfrage, Treffer
from apps.stammdaten.models import LAUFEND, Einheit, Partei, Vertrag, Vertragspartei


def _woerter(a: Anfrage) -> list[str]:
    return a.text.split()


def _alle(woerter: list[str], felder: tuple[str, ...]) -> Q:
    """Jedes Wort steht in mindestens einem der Felder („Tom Bohn“ findet Tom Bohnbach)."""
    bedingung = Q()
    for wort in woerter:
        je_wort = Q()
        for name in felder:
            je_wort |= Q(**{f"{name}__icontains": wort})
        bedingung &= je_wort
    return bedingung


def _namensfelder(praefix: str = "") -> tuple[str, ...]:
    return tuple(f"{praefix}{f}" for f in ("vorname", "nachname", "firma"))


PARTEI_FELDER = (
    *_namensfelder(),
    "ort",
    "plz",
    "strasse",
    "kontaktwege__wert",
)


def einheiten(a: Anfrage, grenze: int) -> Iterable[Treffer]:
    if a.ist_betrag and not a.text.replace(" ", "").isdigit():
        return []
    felder = ("nw_id", "bezeichnung", "objekt__kuerzel", "objekt__bezeichnung", "objekt__anschrift")
    abfrage = (
        Einheit.objects.filter(_alle(_woerter(a), felder))
        .select_related("objekt")
        .order_by(F("aufgeloest_am").asc(nulls_first=True), "nw_id")[:grenze]
    )
    return [
        Treffer(
            titel=e.nw_id,
            url=reverse("stammdaten:einheit", args=[e.pk]),
            unter=" · ".join(
                t for t in (e.get_typ_display(), e.objekt.kuerzel, e.bezeichnung) if t
            ),
            status="beendet" if e.aufgeloest_am else "",
            status_text="aufgelöst" if e.aufgeloest_am else "",
        )
        for e in abfrage
    ]


def _mieter(v: Vertrag) -> str:
    return ", ".join(vp.partei.anzeigename for vp in getattr(v, "mieter", []))


def vertraege(a: Anfrage, grenze: int) -> Iterable[Treffer]:
    if a.ist_betrag and not a.text.replace(" ", "").isdigit():
        return []
    felder = ("nummer", "einheiten__nw_id", *_namensfelder("vertragsparteien__partei__"))
    ids = Vertrag.objects.filter(_alle(_woerter(a), felder)).values("pk").distinct()
    # laufende zuerst, dann Entwürfe, beendete zuletzt
    rang = Case(
        When(status__in=LAUFEND, then=Value(0)),
        When(status=Vertrag.Status.ENTWURF, then=Value(1)),
        default=Value(2),
    )
    liste = (
        Vertrag.objects.filter(pk__in=ids)
        .alias(rang=rang)
        .order_by("rang", "nummer")
        .prefetch_related(
            "einheiten",
            Prefetch(
                "vertragsparteien",
                queryset=Vertragspartei.objects.filter(
                    rolle=Vertragspartei.Rolle.MIETER
                ).select_related("partei"),
                to_attr="mieter",
            ),
        )[:grenze]
    )
    return [
        Treffer(
            titel=v.nummer,
            url=reverse("stammdaten:vertrag", args=[v.pk]),
            unter=" · ".join(
                t for t in (", ".join(e.nw_id for e in v.einheiten.all()), _mieter(v)) if t
            ),
            status=v.status,
        )
        for v in liste
    ]


def parteien(a: Anfrage, grenze: int) -> Iterable[Treffer]:
    if a.ist_betrag:
        return []
    ids = Partei.objects.filter(_alle(_woerter(a), PARTEI_FELDER)).values("pk").distinct()
    abfrage = (
        Partei.objects.filter(pk__in=ids)
        .prefetch_related("kontaktwege")
        .order_by("nachname", "firma", "vorname", "pk")[:grenze]
    )
    treffer = []
    for p in abfrage:
        kontakt = next((k.wert for k in p.kontaktwege.all() if k.art == "email"), "")
        ort = " ".join(t for t in (p.plz, p.ort) if t)
        treffer.append(
            Treffer(
                titel=p.anzeigename or str(p),
                url=reverse("stammdaten:partei", args=[p.pk]),
                unter=" · ".join(t for t in (ort, kontakt) if t),
            )
        )
    return treffer


def anmelden() -> None:
    suche.quelle_anmelden("einheiten", "Einheiten", einheiten, reihenfolge=10)
    suche.quelle_anmelden("vertraege", "Verträge", vertraege, reihenfolge=20)
    suche.quelle_anmelden("parteien", "Parteien", parteien, reihenfolge=30)
