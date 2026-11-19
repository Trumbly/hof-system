"""Suche in der Kopfzeile (Review 2, B1): eine Eingabe, Treffer gruppiert nach Art.

Jede App meldet ihre Suchquelle in ``AppConfig.ready()`` an::

    from apps.kern import suche

    suche.quelle_anmelden("einheiten", "Einheiten", einheiten_suchen, reihenfolge=10)

Eine Quelle bekommt die aufbereitete ``Anfrage`` (Text, mögliche Beträge) und liefert
``Treffer`` (Titel, Zeile darunter, Link). ``suchen(q)`` fragt alle Quellen in ihrer
Reihenfolge und gibt höchstens ``je_gruppe`` Treffer je Gruppe zurück; Gruppen ohne Treffer
fallen weg.

Die Suche liest nur. Sie zeigt in der Oberfläche Namen, E-Mail und Ort, weil Max sie zum
Arbeiten braucht; der Suchtext und die Treffer gehen nie in Logs oder Lauf-Meldungen.

Schnell genug für den Hof (einige tausend Umsätze): ``icontains`` (in Postgres
``UPPER(feld::text) LIKE UPPER('%…%')``) mit Trigramm-Indexen auf genau diesen Ausdrücken
(``pg_trgm``, Migration ``kern.0012_suche_trigramme``).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

#: Höchstens so viele Treffer je Gruppe in der Kopfzeile (Review 2, B1).
JE_GRUPPE = 5
#: Kürzere Eingaben suchen nicht (außer Beträgen und Ziffern): zu viele Treffer.
MINDESTLAENGE = 2


@dataclass(frozen=True)
class Anfrage:
    """Der Suchtext, aufbereitet für die Quellen."""

    text: str
    #: Beträge, die der Text meinen kann, als Bereiche [von, bis) (leer: kein Betrag).
    betraege: tuple[tuple[Decimal, Decimal], ...] = ()

    @property
    def ist_betrag(self) -> bool:
        return bool(self.betraege)


@dataclass(frozen=True)
class Treffer:
    titel: str
    url: str
    unter: str = ""
    #: Status-Schlüssel für ``{% badge %}`` (leer: kein Badge) und eigener Text dazu.
    status: str = ""
    status_text: str = ""


@dataclass(frozen=True)
class Quelle:
    schluessel: str
    titel: str
    suchen: Callable[[Anfrage, int], Iterable[Treffer]]
    reihenfolge: int = 100


@dataclass
class Gruppe:
    schluessel: str
    titel: str
    treffer: list[Treffer] = field(default_factory=list)
    #: Es gibt mehr Treffer, als die Gruppe zeigt.
    mehr: bool = False


_QUELLEN: dict[str, Quelle] = {}


def quelle_anmelden(
    schluessel: str,
    titel: str,
    suchen: Callable[[Anfrage, int], Iterable[Treffer]],
    *,
    reihenfolge: int = 100,
) -> None:
    """Meldet eine Suchquelle an. ``suchen(anfrage, grenze)`` liefert höchstens ``grenze``
    Treffer, die besten zuerst. Ein zweites Anmelden mit demselben Schlüssel ersetzt die
    Quelle (``ready()`` kann in Tests mehrfach laufen)."""
    _QUELLEN[schluessel] = Quelle(schluessel, titel, suchen, reihenfolge)


def quellen() -> list[Quelle]:
    return sorted(_QUELLEN.values(), key=lambda q: (q.reihenfolge, q.schluessel))


# --- Beträge ---------------------------------------------------------------------------

_BETRAG = re.compile(r"^[+-]?\d{1,3}(?:[.\s]\d{3})*(?:,\d{1,2})?$|^[+-]?\d+(?:[.,]\d{1,2})?$")
_BETRAG_EN = re.compile(r"^[+-]?\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?$")


def _dezimal(text: str) -> Decimal | None:
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def betraege(text: str) -> tuple[tuple[Decimal, Decimal], ...]:
    """Die Beträge, die ``text`` meinen kann, als Bereiche [von, bis).

    Komma oder Punkt als Dezimalzeichen, Tausenderpunkt, „€“ und Vorzeichen sind erlaubt
    (Beträge sind im System positiv, die Richtung steht daneben):

    - ``627,34`` und ``627.34`` → genau 627,34
    - ``1.250,00`` und ``1,250.00`` → genau 1.250,00
    - ``1.250`` → 1.250 (Tausenderpunkt) oder 1,25
    - ``627`` → 627,00 bis 627,99
    """
    roh = text.replace("€", "").replace("EUR", "").replace(" ", " ").strip()
    roh = roh.lstrip("+-").strip()
    if not roh or not re.fullmatch(r"[\d.,\s]+", roh) or not any(z.isdigit() for z in roh):
        return ()
    kandidaten: list[Decimal] = []
    cent = Decimal("0.01")
    if _BETRAG_EN.match(roh):
        kandidaten.append(Decimal(roh.replace(",", "")))
    elif _BETRAG.match(roh):
        if "," in roh:
            # deutsch: Punkt und Leerzeichen sind Tausender, Komma ist das Dezimalzeichen
            wert = _dezimal(roh.replace(".", "").replace(" ", "").replace(",", "."))
            if wert is not None:
                kandidaten.append(wert)
        elif "." in roh:
            nach = roh.rpartition(".")[2]
            if len(nach) == 3:
                # „1.250“: Tausenderpunkt, ganzer Betrag
                ganz = _dezimal(roh.replace(".", "").replace(" ", ""))
                if ganz is not None:
                    return ((ganz, ganz + 1),)
            wert = _dezimal(roh.replace(" ", ""))
            if wert is not None:
                kandidaten.append(wert)
        else:
            ganz = _dezimal(roh.replace(" ", ""))
            if ganz is not None:
                return ((ganz, ganz + 1),)
    return tuple((w, w + cent) for w in kandidaten if w >= 0)


def anfrage(q: str) -> Anfrage:
    text = " ".join(q.split())[:100]
    return Anfrage(text=text, betraege=betraege(text))


def suchen(q: str, je_gruppe: int = JE_GRUPPE) -> list[Gruppe]:
    """Treffer aller Quellen zu ``q``, gruppiert, höchstens ``je_gruppe`` je Gruppe."""
    a = anfrage(q)
    if len(a.text) < MINDESTLAENGE and not a.ist_betrag:
        return []
    gruppen: list[Gruppe] = []
    for quelle in quellen():
        treffer = list(quelle.suchen(a, je_gruppe + 1))
        if treffer:
            gruppen.append(
                Gruppe(
                    quelle.schluessel,
                    quelle.titel,
                    treffer[:je_gruppe],
                    mehr=len(treffer) > je_gruppe,
                )
            )
    return gruppen
