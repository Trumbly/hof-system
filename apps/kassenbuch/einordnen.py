"""Vorschlag zum Einordnen eines Umsatzes (Review 2, B3). Liest nur, schreibt nichts.

Woher der Vorschlag kommt, in dieser Reihenfolge:

1. **Regel:** die eindeutige Regel, bei gleichrangigen die erste.
2. **Rückbuchung:** Kategorie und Objekt des Eingangs, den ein Ausgang zurückbucht.
3. **Verlauf:** die häufigste Kategorie und das häufigste Objekt früherer Buchungen derselben
   Gegenpartei (Gegen-IBAN, ohne IBAN der Name) in derselben Richtung.
4. **Zweck und Gegenpartei:** eine Kategorie, deren Name im Zweck oder in der Gegenpartei
   steht („Material“ in „Material Baumarkt“); das Objekt, auf das das Konto in dieser
   Richtung meist bucht.

Die Kategorien sind nach Richtung gefiltert: Bei einer Ausgabe stehen keine Einnahmen zur Wahl.
„Als Regel merken“ ist vorgeschlagen, wenn die Gegenpartei schon einmal vorkam und noch keine
Regel auf den Umsatz passt.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from django.db.models import Count, Q, QuerySet

from apps.bank.models import Bankumsatz
from apps.kassenbuch import services
from apps.kassenbuch.models import Kategorie, Zuordnungsregel
from apps.kern.models import Mandant, Objekt

#: Kategorie-Arten je Richtung des Umsatzes
ARTEN_JE_RICHTUNG: dict[str, tuple[str, ...]] = {
    Bankumsatz.Richtung.AUSGANG: (
        Kategorie.Art.AUSGABE,
        Kategorie.Art.ENTNAHME,
        Kategorie.Art.UMBUCHUNG,
    ),
    Bankumsatz.Richtung.EINGANG: (Kategorie.Art.EINNAHME, Kategorie.Art.UMBUCHUNG),
}
#: Wörter im Namen einer Kategorie, die nichts über den Umsatz sagen
FUELLWOERTER = frozenset({"und", "sonstige", "zwischen", "durchlaufend", "einnahmen"})


def kategorien_fuer(richtung: str, *, dazu: Kategorie | None = None) -> QuerySet[Kategorie]:
    """Aktive Kategorien, die zur Richtung passen (``dazu``: eine weitere, etwa der Vorschlag
    einer Rückbuchung)."""
    arten = ARTEN_JE_RICHTUNG.get(richtung, tuple(Kategorie.Art.values))
    bedingung = Q(aktiv=True, art__in=arten)
    if dazu is not None:
        bedingung |= Q(pk=dazu.pk)
    return Kategorie.objects.filter(bedingung).order_by("art", "bezeichnung")


def aktive_objekte() -> QuerySet[Objekt]:
    return Objekt.objects.filter(aktiv=True).order_by("kuerzel")


@dataclass(frozen=True)
class Vorschlag:
    kategorie: Kategorie | None = None
    objekt: Objekt | None = None
    mandant: Mandant | None = None
    #: Woher der Vorschlag kommt, als kurzer Satzteil („Regel #12“, „wie 4 frühere …“)
    quelle: str = ""
    regel: Zuordnungsregel | None = None
    regel_merken: bool = False
    #: Gleichrangige Regeln (mehrdeutig), sonst leer
    gleichauf: tuple[Zuordnungsregel, ...] = ()

    @property
    def vollstaendig(self) -> bool:
        return self.kategorie is not None and self.objekt is not None


def _gegenpartei(umsatz: Bankumsatz) -> tuple[str, str]:
    """Schlüssel der Gegenpartei: Gegen-IBAN, sonst der Name (ohne Groß und Klein)."""
    if umsatz.gegen_iban:
        return ("iban", umsatz.gegen_iban)
    return ("name", umsatz.gegenpartei.strip().casefold())


def _stamm(wort: str) -> str:
    return wort[: max(5, len(wort) - 2)]


def _stichworte(kategorie: Kategorie) -> list[str]:
    woerter = re.findall(r"[^\W\d_]+", kategorie.bezeichnung.casefold())
    return [_stamm(w) for w in woerter if len(w) >= 4 and w not in FUELLWOERTER]


def kategorie_aus_text(text: str, kategorien: Iterable[Kategorie]) -> Kategorie | None:
    """Die Kategorie, deren Stichwort am längsten im Text steht (sonst None)."""
    text = text.casefold()
    beste: tuple[int, Kategorie] | None = None
    for kategorie in kategorien:
        for wort in _stichworte(kategorie):
            if wort in text and (beste is None or len(wort) > beste[0]):
                beste = (len(wort), kategorie)
    return beste[1] if beste else None


def _haeufigste(zaehler: Counter[int]) -> int | None:
    if not zaehler:
        return None
    # Bei Gleichstand die kleinere ID: stabil von Lauf zu Lauf.
    return min(zaehler, key=lambda i: (-zaehler[i], i))


@dataclass
class _Verlauf:
    anzahl: int
    kategorie: int | None
    objekt: int | None
    mandant: int | None


def _verlauf(umsaetze: Sequence[Bankumsatz]) -> dict[tuple[str, str, str], _Verlauf]:
    """Frühere gültige Buchungen je (Gegenpartei, Richtung): Anzahl der Umsätze und die
    häufigste Kategorie, das häufigste Objekt und den häufigsten Mandanten."""
    ibans = {u.gegen_iban for u in umsaetze if u.gegen_iban}
    namen = {u.gegenpartei.strip() for u in umsaetze if not u.gegen_iban and u.gegenpartei.strip()}
    if not ibans and not namen:
        return {}
    zeilen = (
        services.gueltige_buchungen()
        .filter(Q(umsatz__gegen_iban__in=ibans) | Q(umsatz__gegenpartei__in=namen))
        .exclude(umsatz__in=[u.pk for u in umsaetze])
        .values_list(
            "umsatz_id",
            "umsatz__gegen_iban",
            "umsatz__gegenpartei",
            "umsatz__richtung",
            "kategorie_id",
            "objekt_id",
            "mandant_id",
        )
    )
    je: dict[tuple[str, str, str], list[tuple[int, int, int, int]]] = defaultdict(list)
    for umsatz_id, iban, name, richtung, kategorie, objekt, mandant in zeilen:
        schluessel = ("iban", iban) if iban else ("name", name.strip().casefold())
        je[(*schluessel, richtung)].append((umsatz_id, kategorie, objekt, mandant))
    ergebnis = {}
    for schluessel, eintraege in je.items():
        ergebnis[schluessel] = _Verlauf(
            anzahl=len({e[0] for e in eintraege}),
            kategorie=_haeufigste(Counter(e[1] for e in eintraege)),
            objekt=_haeufigste(Counter(e[2] for e in eintraege)),
            mandant=_haeufigste(Counter(e[3] for e in eintraege)),
        )
    return ergebnis


def _objekt_des_kontos(umsaetze: Sequence[Bankumsatz]) -> dict[tuple[int, str], int]:
    """Das Objekt, auf das jedes Konto je Richtung am häufigsten bucht."""
    konten = {u.konto_id for u in umsaetze}
    zeilen = (
        services.gueltige_buchungen()
        .filter(umsatz__konto__in=konten)
        .values("umsatz__konto_id", "richtung", "objekt_id")
        .annotate(anzahl=Count("pk"))
    )
    je: dict[tuple[int, str], Counter[int]] = defaultdict(Counter)
    for zeile in zeilen:
        je[(zeile["umsatz__konto_id"], zeile["richtung"])][zeile["objekt_id"]] += zeile["anzahl"]
    return {k: v for k, z in je.items() if (v := _haeufigste(z)) is not None}


def _vorkommen(umsaetze: Sequence[Bankumsatz]) -> Counter[tuple[str, str]]:
    """Wie viele Umsätze es je Gegenpartei gibt (alle, auch gebuchte)."""
    ibans = {u.gegen_iban for u in umsaetze if u.gegen_iban}
    namen = {u.gegenpartei.strip() for u in umsaetze if not u.gegen_iban and u.gegenpartei.strip()}
    zaehler: Counter[tuple[str, str]] = Counter()
    for iban, anzahl in (
        Bankumsatz.objects.filter(gegen_iban__in=ibans)
        .values_list("gegen_iban")
        .annotate(n=Count("pk"))
    ):
        zaehler[("iban", iban)] = anzahl
    for name, anzahl in (
        Bankumsatz.objects.filter(gegen_iban="", gegenpartei__in=namen)
        .values_list("gegenpartei")
        .annotate(n=Count("pk"))
    ):
        zaehler[("name", name.strip().casefold())] += anzahl
    return zaehler


def vorschlaege(umsaetze: Sequence[Bankumsatz]) -> dict[int, Vorschlag]:
    """Je Umsatz der Vorschlag (siehe Moduldoku). Für viele Umsätze auf einmal gebaut: Die
    Regeln und der Verlauf werden nur einmal gelesen."""
    if not umsaetze:
        return {}
    regeln = services.aktive_regeln()
    verlauf = _verlauf(umsaetze)
    konto_objekt = _objekt_des_kontos(umsaetze)
    vorkommen = _vorkommen(umsaetze)
    kategorien = {k.pk: k for k in Kategorie.objects.filter(aktiv=True)}
    objekte = {o.pk: o for o in Objekt.objects.select_related("mandant")}
    mandanten = {m.pk: m for m in Mandant.objects.all()}
    je_richtung = {
        richtung: [k for k in kategorien.values() if k.art in arten]
        for richtung, arten in ARTEN_JE_RICHTUNG.items()
    }

    ergebnis: dict[int, Vorschlag] = {}
    for umsatz in umsaetze:
        partei = _gegenpartei(umsatz)
        kam_vor = vorkommen.get(partei, 0) > 1 and bool(partei[1])
        einordnung = services.einordnen(umsatz, regeln)
        regel = einordnung.regel or (einordnung.gleichauf[0] if einordnung.gleichauf else None)
        if regel is not None:
            gleichauf = tuple(einordnung.gleichauf) if einordnung.mehrdeutig else ()
            quelle = f"Regel #{regel.pk}"
            if gleichauf:
                andere = ", ".join(f"#{r.pk}" for r in gleichauf if r.pk != regel.pk)
                quelle += f", gleichauf mit {andere}"
            ergebnis[umsatz.pk] = Vorschlag(
                kategorie=regel.kategorie,
                objekt=regel.objekt,
                mandant=regel.mandant,
                quelle=quelle,
                regel=regel,
                gleichauf=gleichauf,
            )
            continue
        zurueck = services.rueckbuchung_zu(umsatz)
        if zurueck is not None:
            vorlage = services.gueltige_buchungen().filter(umsatz=zurueck).first()
            if vorlage is not None:
                ergebnis[umsatz.pk] = Vorschlag(
                    kategorie=vorlage.kategorie,
                    objekt=vorlage.objekt,
                    mandant=vorlage.mandant,
                    quelle=f"wie der zurückgebuchte Umsatz #{zurueck.pk}",
                )
                continue
        frueher = verlauf.get((*partei, umsatz.richtung))
        if frueher is not None and frueher.kategorie in kategorien:
            ergebnis[umsatz.pk] = Vorschlag(
                kategorie=kategorien[frueher.kategorie],
                objekt=objekte.get(frueher.objekt or 0),
                mandant=mandanten.get(frueher.mandant or 0),
                quelle=(
                    "wie der frühere Umsatz dieser Gegenpartei"
                    if frueher.anzahl == 1
                    else f"wie {frueher.anzahl} frühere Umsätze dieser Gegenpartei"
                ),
                regel_merken=kam_vor,
            )
            continue
        text = f"{umsatz.verwendungszweck} {umsatz.gegenpartei}"
        kategorie = kategorie_aus_text(text, je_richtung.get(umsatz.richtung, []))
        objekt = objekte.get(konto_objekt.get((umsatz.konto_id, umsatz.richtung), 0))
        teile = []
        if kategorie is not None:
            teile.append("Kategorie aus dem Zweck")
        if objekt is not None:
            teile.append("Objekt wie meist auf diesem Konto")
        ergebnis[umsatz.pk] = Vorschlag(
            kategorie=kategorie,
            objekt=objekt,
            quelle=", ".join(teile),
            regel_merken=kam_vor,
        )
    return ergebnis


def vorschlag(umsatz: Bankumsatz) -> Vorschlag:
    return vorschlaege([umsatz])[umsatz.pk]


def gegenpartei_schluessel(umsatz: Bankumsatz) -> tuple[str, str]:
    """Öffentlich für die Gruppen der Arbeitsliste: gleiche Gegenpartei, gleicher Schlüssel."""
    return _gegenpartei(umsatz)
