"""Die Vorlagenarten als Konfiguration (H8.1 bis H8.3).

Eine Vorlagenart legt fest,
- welche Platzhalter eine Vorlage dieser Art verwenden darf (``platzhalter``, dazu immer
  ``GEMEINSAME_PLATZHALTER``),
- welcher Versandweg vorgeschlagen wird (``versandweg``),
- ob es eine einseitige Erklärung ist (``einseitige_erklaerung``): Dann gilt ein Schreiben
  erst mit Zustellnachweis als erledigt (H8.3),
- ob die Vorlage der Briefkopf ist (``briefkopf``): Er umrahmt jedes Schreiben,
- ob vor dem Senden bestätigt werden muss, dass der Anwalt das Schreiben gesehen hat
  (``anwalt_bestaetigung``, Kündigungen im Mahnwesen und H4.6): Ohne den Haken geht nichts
  hinaus (``services.schreiben_senden(..., anwalt_gesehen=True)``).

Neue Arten kommen in ``VORLAGENARTEN`` dazu (eine Stelle für alles) oder werden von
einer App beim Laden mit ``registrieren()`` angemeldet. Platzhalter sind Pfade mit
Punkt, z. B. ``vertrag.beginn``. Ein Eintrag erlaubt auch alles darunter
(``betrag`` erlaubt ``betrag.offen``) und den Weg dorthin (``vertrag`` für
``vertrag.beginn``).

Im Kontext stehen nur einfache Werte (Text, Zahl, Decimal, Datum, Listen, Wörterbücher),
nie Modell-Objekte: Was eine Vorlage lesen kann, bestimmt allein der Aufrufer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


class Versandweg:
    """Schlüssel der Versandwege (Werte von ``Schreiben.versandweg``)."""

    MAIL = "mail"
    BRIEF = "brief"
    EINWURF_EINSCHREIBEN = "einwurf_einschreiben"
    SELBST = "selbst"

    TITEL: ClassVar[dict[str, str]] = {
        MAIL: "Mail",
        BRIEF: "Brief",
        EINWURF_EINSCHREIBEN: "Einwurf-Einschreiben",
        SELBST: "selbst (drucken und übergeben)",
    }


#: Platzhalter, die jede Vorlage verwenden darf.
GEMEINSAME_PLATZHALTER: tuple[str, ...] = (
    "absender.name",
    "absender.anschrift",
    "absender.email",
    "absender.telefon",
    "absender.bank",
    "empfaenger.name",
    "empfaenger.anrede",
    "empfaenger.anschrift",
    "empfaenger.email",
    "datum",
    "zeichen",
)

#: Platzhalter, die der Briefkopf verwenden darf.
BRIEFKOPF_PLATZHALTER: tuple[str, ...] = (
    "absender.name",
    "absender.anschrift",
    "absender.email",
    "absender.telefon",
    "absender.bank",
    "absender.steuernummer",
)

_VERTRAG = (
    "vertrag.nummer",
    "vertrag.beginn",
    "vertrag.ende",
    "vertrag.kuendigungsfrist",
    "einheit.kuerzel",
    "einheit.bezeichnung",
    "objekt.kuerzel",
    "objekt.bezeichnung",
    "objekt.anschrift",
)

#: Weitere Werte aus dem Vertragsstamm (``apps.stammdaten.schreiben.kontext_vertrag``).
_STAMMDATEN = (
    "vertrag.art",
    "vertrag.art_titel",
    "vertrag.sonderabreden",
    "einheit.nw_id",
    "einheit.typ",
    "einheit.flaeche_qm",
    "einheiten",
    "mandant.bezeichnung",
    "mieter",
    "zahler",
    "buergen",
    "kondition",
    "staffel",
    "kaution",
)


#: Hinweis an jedem Schreiben einer Art mit ``anwalt_bestaetigung`` (Mahnwesen, H4.3, H4.6).
ANWALT_HINWEIS = "vor Versand zum Anwalt"

#: Werte des Mahnwesens (``apps.mahnwesen.services.kontext_mahnung``): offener Betrag und
#: Monatsmiete, Aufstellung je Posten, Frist, Stufe; bei Kündigungen auch die Fristen.
_MAHNUNG = ("betrag", "forderung", "frist", "mahnung")


@dataclass(frozen=True)
class Vorlagenart:
    schluessel: str
    titel: str
    versandweg: str = Versandweg.MAIL
    einseitige_erklaerung: bool = False
    platzhalter: tuple[str, ...] = ()
    briefkopf: bool = False
    anwalt_bestaetigung: bool = False

    def erlaubte_platzhalter(self) -> frozenset[str]:
        if self.briefkopf:
            return frozenset(BRIEFKOPF_PLATZHALTER)
        return frozenset(GEMEINSAME_PLATZHALTER) | frozenset(self.platzhalter)


VORLAGENARTEN: tuple[Vorlagenart, ...] = (
    Vorlagenart("briefkopf", "Briefkopf", briefkopf=True),
    Vorlagenart("mitteilung", "Mitteilung", platzhalter=(*_VERTRAG, "betrag")),
    Vorlagenart(
        "aushang",
        "Aushang",
        versandweg=Versandweg.SELBST,
        platzhalter=("objekt.kuerzel", "objekt.bezeichnung", "objekt.anschrift"),
    ),
    Vorlagenart(
        "vertrag",
        "Vertrag",
        versandweg=Versandweg.SELBST,
        platzhalter=(*_VERTRAG, *_STAMMDATEN, "betrag"),
    ),
    Vorlagenart(
        "kuendigungsbestaetigung",
        "Kündigungsbestätigung",
        platzhalter=(*_VERTRAG, *_STAMMDATEN, "kuendigung"),
    ),
    # Stammdaten (Schritt 4): Kautionsabrechnung, Übergabe- und Rückgabeprotokoll
    Vorlagenart(
        "kautionsabrechnung",
        "Kautionsabrechnung",
        versandweg=Versandweg.BRIEF,
        platzhalter=(*_VERTRAG, *_STAMMDATEN, "abrechnung"),
    ),
    Vorlagenart(
        "uebergabeprotokoll",
        "Übergabeprotokoll",
        versandweg=Versandweg.SELBST,
        platzhalter=(*_VERTRAG, *_STAMMDATEN, "uebergabe"),
    ),
    Vorlagenart(
        "rueckgabeprotokoll",
        "Rückgabeprotokoll",
        versandweg=Versandweg.SELBST,
        platzhalter=(*_VERTRAG, *_STAMMDATEN, "uebergabe"),
    ),
    Vorlagenart(
        "kuendigung",
        "Kündigung durch den Hof",
        versandweg=Versandweg.EINWURF_EINSCHREIBEN,
        einseitige_erklaerung=True,
        platzhalter=(*_VERTRAG, *_STAMMDATEN, *_MAHNUNG, "kuendigung"),
        anwalt_bestaetigung=True,
    ),
    Vorlagenart(
        "zahlungserinnerung",
        "Zahlungserinnerung",
        platzhalter=(*_VERTRAG, *_STAMMDATEN, *_MAHNUNG),
    ),
    Vorlagenart(
        "mahnung",
        "Mahnung mit Fristsetzung",
        versandweg=Versandweg.EINWURF_EINSCHREIBEN,
        einseitige_erklaerung=True,
        platzhalter=(*_VERTRAG, *_STAMMDATEN, *_MAHNUNG),
    ),
    # Mahnwesen (Schritt 7, H4.2): Abmahnung mit Kündigungsandrohung, als Brief.
    Vorlagenart(
        "abmahnung",
        "Abmahnung mit Kündigungsandrohung",
        versandweg=Versandweg.EINWURF_EINSCHREIBEN,
        einseitige_erklaerung=True,
        platzhalter=(*_VERTRAG, *_STAMMDATEN, *_MAHNUNG),
    ),
)

_ARTEN: dict[str, Vorlagenart] = {art.schluessel: art for art in VORLAGENARTEN}


class UnbekannteVorlagenart(KeyError):
    pass


def registrieren(art: Vorlagenart) -> None:
    """Meldet eine weitere Vorlagenart an (z. B. aus dem Mahnwesen)."""
    vorhanden = _ARTEN.get(art.schluessel)
    if vorhanden is not None and vorhanden != art:
        raise ValueError(f"Vorlagenart {art.schluessel} ist schon anders angemeldet.")
    if art.versandweg not in Versandweg.TITEL:
        raise ValueError(f"Unbekannter Versandweg {art.versandweg}.")
    _ARTEN[art.schluessel] = art


def holen(schluessel: str) -> Vorlagenart:
    try:
        return _ARTEN[schluessel]
    except KeyError as fehler:
        raise UnbekannteVorlagenart(schluessel) from fehler


def alle() -> list[Vorlagenart]:
    return list(_ARTEN.values())


def auswahl() -> list[tuple[str, str]]:
    """(Schlüssel, Titel) für Auswahlfelder."""
    return [(art.schluessel, art.titel) for art in _ARTEN.values()]


def finden(text: str) -> Vorlagenart | None:
    """Sucht eine Art nach Schlüssel oder Titel (ohne Groß- und Kleinschreibung)."""
    suche = text.strip().casefold()
    for art in _ARTEN.values():
        if suche in (art.schluessel.casefold(), art.titel.casefold()):
            return art
    return None
