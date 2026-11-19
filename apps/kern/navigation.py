"""Navigation der neuen Gestaltung (Review 1, Punkt 13; Review 2, C1).

Gruppen: Heute · Verträge · Geld · Hof · System. Am Desktop als Seitenleiste, am Handy
unten eine Leiste mit Heute · Arbeitslisten · Zähler · Übergabe · Mehr; „Mehr“ öffnet alle
Gruppen. Ein Eintrag ist aktiv, wenn die aufgerufene Seite zu ihm gehört.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from django.http import HttpRequest
from django.urls import reverse

Pruefung = Callable[[str, str], bool]  # (view_name, app_name) → aktiv?


def _seite(*namen: str) -> Pruefung:
    return lambda view, app: view in namen


def _app(*apps: str) -> Pruefung:
    return lambda view, app: app in apps


def _schreiben(view: str, app: str) -> bool:
    return app == "dokumente" and any(t in view for t in ("schreiben", "vorlage", "serie"))


def _dokumente(view: str, app: str) -> bool:
    return app == "dokumente" and not _schreiben(view, app)


#: Seiten der Übergabe und Rückgabe (Liste und Assistent am Handy)
UEBERGABE_SEITEN = ("stammdaten:uebergaben", "stammdaten:uebergabe", "stammdaten:uebergabe_foto")


def _uebergabe(view: str, app: str) -> bool:
    return view in UEBERGABE_SEITEN


def _stammdaten(view: str, app: str) -> bool:
    return app == "stammdaten" and not _uebergabe(view, app)


def _konto(view: str, app: str) -> bool:
    """Die eigene Kontoseite und die Seiten von allauth (zweiter Faktor, Passwort)."""
    return view == "kern:konto" or (
        not app and view.startswith(("mfa_", "account_")) and view != "account_logout"
    )


@dataclass(frozen=True)
class Eintrag:
    titel: str
    url_name: str
    pruefung: Pruefung
    nur_verwaltung: bool = False
    #: Kennzeichen neben dem Titel, z. B. „Admin“ für die Django-Verwaltung.
    marke: str = ""


@dataclass(frozen=True)
class Gruppe:
    titel: str
    eintraege: tuple[Eintrag, ...]


GRUPPEN: tuple[Gruppe, ...] = (
    Gruppe(
        "Heute",
        (
            Eintrag("Start", "kern:startseite", _seite("kern:startseite")),
            Eintrag("Arbeitslisten", "kern:arbeitslisten", _seite("kern:arbeitslisten")),
        ),
    ),
    Gruppe(
        "Verträge",
        (
            Eintrag("Stammdaten", "stammdaten:uebersicht", _stammdaten),
            Eintrag("Übergaben", "stammdaten:uebergaben", _uebergabe),
            Eintrag("Offene Posten", "soll:liste", _app("soll")),
            Eintrag("Mahnwesen", "mahnwesen:faelle", _app("mahnwesen")),
        ),
    ),
    Gruppe(
        "Geld",
        (
            Eintrag("Bank", "bank:konten", _app("bank")),
            Eintrag("Kassenbuch", "kassenbuch:buchungen", _app("kassenbuch")),
        ),
    ),
    Gruppe(
        "Hof",
        (
            Eintrag("Zähler", "zaehler:uebersicht", _app("zaehler")),
            Eintrag("Fristen", "fristen:liste", _app("fristen")),
            Eintrag("Dokumente", "dokumente:ablage", _dokumente),
            Eintrag("Schreiben", "dokumente:schreiben_liste", _schreiben),
        ),
    ),
    Gruppe(
        "System",
        (
            Eintrag("Anbindungen", "anbindungen:uebersicht", _app("anbindungen")),
            Eintrag("Protokoll", "kern:protokoll", _seite("kern:protokoll")),
            Eintrag("Läufe", "kern:laeufe", _seite("kern:laeufe")),
            Eintrag("Verwaltung", "admin:index", _app("admin"), nur_verwaltung=True, marke="Admin"),
            Eintrag("Konto", "kern:konto", _konto),
        ),
    ),
)

#: Leiste unten am Handy (ohne „Mehr“, das öffnet alle Gruppen).
UNTEN: tuple[tuple[str, str, str, Pruefung], ...] = (
    ("Heute", "kern:startseite", "heute", _seite("kern:startseite")),
    ("Arbeitslisten", "kern:arbeitslisten", "listen", _seite("kern:arbeitslisten")),
    ("Zähler", "zaehler:uebersicht", "zaehler", _app("zaehler")),
    # Anstehende Übergaben und Rückgaben; von dort geht es in den Assistenten.
    ("Übergabe", "stammdaten:uebergaben", "uebergabe", _uebergabe),
)


@dataclass(frozen=True)
class Link:
    titel: str
    url: str
    aktiv: bool
    symbol: str = ""
    marke: str = ""


def _ort(request: HttpRequest) -> tuple[str, str]:
    treffer = getattr(request, "resolver_match", None)
    if treffer is None:
        return "", ""
    return treffer.view_name or "", treffer.app_name or ""


def gruppen(request: HttpRequest) -> list[tuple[str, list[Link]]]:
    """Gruppen mit ihren Links für die Seitenleiste und „Mehr“ am Handy."""
    view, app = _ort(request)
    verwaltung = bool(getattr(request.user, "is_staff", False))
    return [
        (
            gruppe.titel,
            [
                Link(e.titel, reverse(e.url_name), e.pruefung(view, app), marke=e.marke)
                for e in gruppe.eintraege
                if verwaltung or not e.nur_verwaltung
            ],
        )
        for gruppe in GRUPPEN
    ]


def unten(request: HttpRequest) -> list[Link]:
    view, app = _ort(request)
    return [
        Link(titel, reverse(name), pruefung(view, app), symbol=symbol)
        for titel, name, symbol, pruefung in UNTEN
    ]
