"""Testhof-Modul der Zähler (Schritt 8): 20 Zähler mit Ständen über zwei Jahre.

- **Zähler:** fünf Hauptzähler an den Objekten (HOF Strom und Wasser, HALLE Strom und
  Wärme, FELD Wasser) und 14 Zähler an Einheiten (alle Hallen, Lager, Gärten, Garagen und
  die beiden Einheiten mit Rückgabe aus dem Stammdaten-Modul), dazu der Nachfolger aus
  einem Zählerwechsel: 20 Zähler, davon 19 eingebaut.
- **Stände:** Einbau, 31.12.2024, 30.06.2025, 31.12.2025 und 30.06.2026; der Verbrauch
  wächst je Zähler gleichmäßig, die Stände steigen also immer. Stichtagsstände mit Foto.
- **Zählerwechsel:** der Stromzähler der ersten Halle am 15.06.2025 (Ausbau- und
  Einbaustand in einem Vorgang); der Nachfolger beginnt bei 0.
- **Lücken für den Datenstand:** ein fehlender Stand zum Stichtag 31.12.2025, ein
  Stichtagsstand ohne Foto, ein geschätzter Stand (Kennzeichen, mit Vermerk), zwei Zähler
  ohne Eichfrist, einer ohne Einbaudatum.
- **Übergabe:** Die Rückgaben aus dem Stammdaten-Modul bekommen ihre Zählerstände
  („erfasst bei Übergabe“).

Alle Daten rechnen von festen Daten aus, nie von ``heute``. Geschrieben wird über
``apps.zaehler.services``.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.core.files.base import ContentFile

from apps.fristen.models import Frist, Fristverschiebung
from apps.kern.models import Arbeitsposten, Objekt
from apps.stammdaten.models import Einheit, Uebergabe, Vertrag
from apps.zaehler import services
from apps.zaehler.models import Zaehler, Zaehlerstand
from testhof import register
from testhof.aufbau import Kontext, TesthofFehler
from testhof.stammdaten import StammdatenDaten

NAME = "zaehler"
D = datetime.date

ANZAHL_EINGEBAUT = 19
STICHTAGE = (D(2024, 12, 31), D(2025, 12, 31))
ZWISCHENSTAENDE = (D(2025, 6, 30), D(2026, 6, 30))
WECHSEL_AM = D(2025, 6, 15)
#: Gültigkeit der Eichung in Jahren (Annahme nach MessEV, Anlage 7), endet am Jahresende.
EICHJAHRE = {"strom": 8, "wasser": 6, "gas": 8, "waerme": 5}
#: Verbrauch je Tag (von, bis) nach Art und Ort („objekt“ = Hauptzähler).
VERBRAUCH: dict[tuple[str, str], tuple[float, float]] = {
    ("strom", "objekt"): (40, 120),
    ("strom", Einheit.Typ.HALLE): (20, 60),
    ("strom", Einheit.Typ.LAGER): (2, 6),
    ("strom", "einheit"): (0.3, 1.2),
    ("wasser", "objekt"): (0.5, 2),
    ("wasser", "einheit"): (0.02, 0.2),
    ("waerme", "objekt"): (50, 150),
}
KURZ = {"strom": "S", "wasser": "W", "gas": "G", "waerme": "WM"}

#: Kleinstes gültiges PNG (1×1 Pixel) als Foto eines Zählers.
PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00"
    b"\x00\x00\x00IEND\xaeB`\x82"
)

# Lücken (Index in der Reihenfolge der Anlage)
OHNE_EICHFRIST = {4, 11}
OHNE_EINBAUDATUM = {12}
OHNE_STICHTAGSSTAND = 9  # kein Stand zum 31.12.2025
OHNE_FOTO = 13  # Stand zum 31.12.2025 ohne Foto
GESCHAETZT = 15  # Stand zum 31.12.2025 geschätzt, ohne Foto


@dataclass
class ZaehlerDaten:
    zaehler: dict[str, Zaehler]  # nach Nummer, alle 20
    wechsel: services.Wechsel
    ohne_stichtagsstand: Zaehler
    ohne_foto: Zaehler
    geschaetzt: Zaehlerstand
    bei_rueckgabe: list[Zaehlerstand]

    @property
    def eingebaut(self) -> list[Zaehler]:
        return [z for z in self.zaehler.values() if z.ist_eingebaut]


@dataclass
class _Plan:
    ort: Einheit | Objekt
    art: str
    bezeichnung: str
    beginn: D | None
    start: Decimal
    je_tag: Decimal

    def stand_am(self, datum: D) -> Decimal:
        tage = (datum - (self.beginn or D(2019, 1, 1))).days
        return (self.start + self.je_tag * tage).quantize(Decimal("0.1"))


def _foto() -> ContentFile[bytes]:
    return ContentFile(PNG, name="zaehler.png")


def _rueckgaben(stammdaten: StammdatenDaten) -> list[Uebergabe]:
    vertraege: list[Vertrag] = [
        stammdaten.sonderfaelle["vertrag_monatsmitte"][1],
        *stammdaten.sonderfaelle["kuendigung_rueckgabe_kaution"],
    ]
    return [
        u
        for v in vertraege
        for u in v.uebergaben.filter(art=Uebergabe.Art.RUECKGABE).order_by("pk")
    ]


def _orte(
    kontext: Kontext, stammdaten: StammdatenDaten, rueckgaben: list[Uebergabe]
) -> list[tuple[Einheit | Objekt, str, str]]:
    """(Ort, Art, Lage) der 19 eingebauten Zähler, fest sortiert."""
    objekte: dict[str, Objekt] = kontext.ablage["kern"].objekte
    einheiten = sorted(
        (e for e in stammdaten.einheiten.values() if not e.ist_wohnraum), key=lambda e: e.nw_id
    )

    def vom_typ(typ: str) -> list[Einheit]:
        return [e for e in einheiten if e.typ == typ]

    orte: list[tuple[Einheit | Objekt, str, str]] = [
        (objekte["HOF"], "strom", "Hauptzähler Garagenzeile"),
        (objekte["HOF"], "wasser", "Hauptwasserzähler"),
        (objekte["HALLE"], "strom", "Hauptzähler Halle"),
        (objekte["HALLE"], "waerme", "Wärmemengenzähler Heizung"),
        (objekte["FELD"], "wasser", "Brunnen"),
    ]
    kandidaten: list[tuple[Einheit, str, str]] = []
    for uebergabe in rueckgaben:
        for e in uebergabe.vertrag.einheiten.order_by("nw_id"):
            kandidaten.append((e, "wasser" if e.typ == Einheit.Typ.GARTEN else "strom", ""))
    kandidaten += [(e, "strom", "Unterverteilung") for e in vom_typ(Einheit.Typ.HALLE)]
    kandidaten += [(e, "wasser", "Wasseranschluss") for e in vom_typ(Einheit.Typ.HALLE)[:2]]
    kandidaten += [(e, "strom", "") for e in vom_typ(Einheit.Typ.LAGER)[:1]]
    kandidaten += [(e, "wasser", "Standrohr") for e in vom_typ(Einheit.Typ.GARTEN)[:2]]
    kandidaten += [(e, "strom", "") for e in vom_typ(Einheit.Typ.GARAGE)]
    gesehen: set[tuple[int, str]] = set()
    for einheit, art, lage in kandidaten:
        if len(orte) == ANZAHL_EINGEBAUT:
            break
        if (einheit.pk, art) in gesehen:
            continue
        gesehen.add((einheit.pk, art))
        orte.append((einheit, art, lage))
    return orte


def _plan(kontext: Kontext, index: int, ort: Einheit | Objekt, art: str, lage: str) -> _Plan:
    z = kontext.zufall
    if isinstance(ort, Objekt):
        schluessel = (art, "objekt")
    else:
        schluessel = (art, ort.typ) if (art, ort.typ) in VERBRAUCH else (art, "einheit")
    von, bis = VERBRAUCH[schluessel]
    je_tag = Decimal(str(round(z.uniform(von, bis), 3)))
    start = Decimal(z.randrange(1000, 20000)) if isinstance(ort, Objekt) else Decimal(0)
    beginn = D(z.randrange(2019, 2024), z.randrange(1, 13), 1)
    return _Plan(
        ort=ort,
        art=art,
        bezeichnung=lage,
        beginn=None if index in OHNE_EINBAUDATUM else beginn,
        start=start,
        je_tag=je_tag,
    )


def _eichfrist(plan: _Plan, index: int) -> D | None:
    if index in OHNE_EICHFRIST:
        return None
    jahr = (plan.beginn or D(2019, 1, 1)).year + EICHJAHRE[plan.art]
    # Keine Eichfrist wird im Testhof vor Ende 2027 fällig: Die Fristen der anderen Module
    # am Stichtag des Testhofs bleiben so, wie ihre Tests sie erwarten.
    return D(max(jahr, 2027), 12, 31)


def aufbauen(kontext: Kontext) -> ZaehlerDaten:
    stammdaten: StammdatenDaten = kontext.ablage["stammdaten"]
    rueckgaben = _rueckgaben(stammdaten)
    orte = _orte(kontext, stammdaten, rueckgaben)
    plaene = [_plan(kontext, i, ort, art, lage) for i, (ort, art, lage) in enumerate(orte)]

    alle: dict[str, Zaehler] = {}
    zaehler_je_index: list[Zaehler] = []
    for i, plan in enumerate(plaene):
        nummer = f"TH-{KURZ[plan.art]}-{i + 1:04d}"
        zaehler = services.zaehler_anlegen(
            nummer,
            plan.art,
            einheit=plan.ort if isinstance(plan.ort, Einheit) else None,
            objekt=plan.ort if isinstance(plan.ort, Objekt) else None,
            bezeichnung=plan.bezeichnung,
            einbaudatum=plan.beginn,
            eichfrist_bis=_eichfrist(plan, i),
            einbau_stand=plan.start if plan.beginn else None,
        )
        alle[nummer] = zaehler
        zaehler_je_index.append(zaehler)

    # Zählerwechsel: der Stromzähler der ersten Halle (erster Zähler an einer Halle).
    wechsel_index = next(
        i
        for i, p in enumerate(plaene)
        if isinstance(p.ort, Einheit) and p.ort.typ == Einheit.Typ.HALLE and p.art == "strom"
    )
    luecken = {OHNE_STICHTAGSSTAND, OHNE_FOTO, GESCHAETZT, *OHNE_EICHFRIST, *OHNE_EINBAUDATUM}
    if wechsel_index in luecken:
        raise TesthofFehler("Zählerwechsel und Lücken des Datenstands treffen denselben Zähler.")
    plan_je_zaehler = {z.pk: p for z, p in zip(zaehler_je_index, plaene, strict=True)}
    termine = sorted({*STICHTAGE, *ZWISCHENSTAENDE})
    geschaetzt: Zaehlerstand | None = None
    wechsel: services.Wechsel | None = None
    for i, (plan, zaehler) in enumerate(zip(plaene, zaehler_je_index, strict=True)):
        for datum in termine:
            if i == wechsel_index and datum > WECHSEL_AM:
                continue
            if i == OHNE_STICHTAGSSTAND and datum == STICHTAGE[1]:
                continue
            stichtag = datum in STICHTAGE
            art = Zaehlerstand.Art.ABGELESEN
            foto = _foto() if stichtag else None
            vermerk = ""
            if datum == STICHTAGE[1] and i in (OHNE_FOTO, GESCHAETZT):
                foto = None
                if i == GESCHAETZT:
                    art, vermerk = Zaehlerstand.Art.GESCHAETZT, "linear geschätzt (Testhof)"
            stand = services.stand_erfassen(
                zaehler,
                datum,
                plan.stand_am(datum),
                art=art,
                foto=foto,
                vermerk=vermerk,
            )
            if art == Zaehlerstand.Art.GESCHAETZT:
                geschaetzt = stand
        if i == wechsel_index:
            alter = zaehler_je_index[i]
            neu_plan = _Plan(
                plan.ort, plan.art, plan.bezeichnung, WECHSEL_AM, Decimal(0), plan.je_tag
            )
            wechsel = services.zaehler_wechseln(
                alter,
                WECHSEL_AM,
                plan.stand_am(WECHSEL_AM),
                neue_nummer=f"TH-{KURZ[plan.art]}-{len(plaene) + 1:04d}",
                einbau_stand=Decimal(0),
                eichfrist_bis=D(WECHSEL_AM.year + EICHJAHRE[plan.art], 12, 31),
                ausbau_foto=_foto(),
                einbau_foto=_foto(),
            )
            alle[wechsel.neuer.nummer] = wechsel.neuer
            plan_je_zaehler[wechsel.neuer.pk] = neu_plan
            for datum in (d for d in termine if d > WECHSEL_AM):
                services.stand_erfassen(
                    wechsel.neuer,
                    datum,
                    neu_plan.stand_am(datum),
                    foto=_foto() if datum in STICHTAGE else None,
                )

    # Zählerstände der Rückgaben aus dem Stammdaten-Modul („erfasst bei Übergabe“).
    bei_rueckgabe = []
    for uebergabe in rueckgaben:
        for zaehler in services.zaehler_der_uebergabe(uebergabe):
            bei_rueckgabe.append(
                services.stand_erfassen(
                    zaehler,
                    uebergabe.datum,
                    plan_je_zaehler[zaehler.pk].stand_am(uebergabe.datum),
                    foto=_foto(),
                    uebergabe=uebergabe,
                )
            )

    if wechsel is None or geschaetzt is None:
        raise TesthofFehler("Zählerwechsel oder geschätzter Stand fehlt.")
    for zaehler in alle.values():
        zaehler.refresh_from_db()
    return ZaehlerDaten(
        zaehler=alle,
        wechsel=wechsel,
        ohne_stichtagsstand=zaehler_je_index[OHNE_STICHTAGSSTAND],
        ohne_foto=zaehler_je_index[OHNE_FOTO],
        geschaetzt=geschaetzt,
        bei_rueckgabe=bei_rueckgabe,
    )


def leeren() -> None:
    """Löscht Zähler und Stände des Testhofs samt Fristen und Posten (nur erfundene Daten)."""
    typen = ContentType.objects.filter(app_label="zaehler")
    Arbeitsposten.objects.filter(bezug_typ__in=typen).delete()
    fristen = Frist.objects.filter(bezug_typ__in=typen)
    Arbeitsposten.objects.filter(
        bezug_typ=ContentType.objects.get_for_model(Frist), bezug_id__in=fristen.values("pk")
    ).delete()
    Fristverschiebung.objects.filter(frist__in=fristen).delete()
    fristen.delete()
    Zaehlerstand.objects.all().delete()
    Zaehler.objects.update(nachfolger=None)
    Zaehler.objects.all().delete()


register.registrieren(NAME, abhaengig_von=("kern", "stammdaten"), aufbauen=aufbauen, leeren=leeren)
