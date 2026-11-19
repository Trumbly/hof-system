"""Bezugsarten für Serienmitteilungen (H8.4): wofür sich Schreiben in Serie erzeugen lassen.

Eine App meldet ein Modell an, mit einer Funktion, die aus einem Objekt den Kontext
einer Vorlage macht (nur einfache Werte, siehe ``apps.dokumente.platzhalter``), und mit
der Auswahl, die die Seite „Serienmitteilung“ anbietet::

    bezuege.registrieren(
        Vertrag,
        titel="Verträge",
        kontext=lambda v: {"empfaenger": {...}, "vertrag": {...}},
        auswahl=lambda: Vertrag.objects.laufend(),
    )

Der Dokumente-Teil meldet ``kern.Objekt`` an (Aushang je Objekt); die Stammdaten melden
Verträge an (``apps.stammdaten.schreiben.vertrag_anmelden``, Mitteilung an Nutzer).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from django.db import models

Kontextfunktion = Callable[[Any], Mapping[str, Any]]
Auswahlfunktion = Callable[[], "models.QuerySet[Any]"]
Bezeichnungsfunktion = Callable[[Any], str]
#: (Auswahl, Objekt oder None, Typ der Einheit oder "") → eingegrenzte Auswahl (Review 2, B13)
Filterfunktion = Callable[["models.QuerySet[Any]", Any, str], "models.QuerySet[Any]"]


@dataclass(frozen=True)
class Bezugsart:
    modell: type[models.Model]
    titel: str
    kontext: Kontextfunktion
    auswahl: Auswahlfunktion
    #: Text in der Auswahlliste (Max' Oberfläche: mit Namen, Review 2, Klarstellung).
    bezeichnung: Bezeichnungsfunktion = str
    #: Eingrenzen nach Objekt und Typ der Einheit; ohne: nur nach Objekt, wenn möglich.
    filtern: Filterfunktion | None = None
    #: Lässt sich nach dem Typ der Einheit eingrenzen?
    nach_typ: bool = False

    def eingrenzen(
        self, auswahl: models.QuerySet[Any], objekt: Any, typ: str
    ) -> models.QuerySet[Any]:
        if self.filtern is not None:
            return self.filtern(auswahl, objekt, typ)
        return auswahl

    @property
    def schluessel(self) -> str:
        return self.modell._meta.label_lower


_BEZUGSARTEN: dict[str, Bezugsart] = {}


def registrieren(
    modell: type[models.Model],
    *,
    titel: str,
    kontext: Kontextfunktion,
    auswahl: Auswahlfunktion,
    bezeichnung: Bezeichnungsfunktion = str,
    filtern: Filterfunktion | None = None,
    nach_typ: bool = False,
) -> Bezugsart:
    art = Bezugsart(
        modell=modell,
        titel=titel,
        kontext=kontext,
        auswahl=auswahl,
        bezeichnung=bezeichnung,
        filtern=filtern,
        nach_typ=nach_typ,
    )
    _BEZUGSARTEN[art.schluessel] = art
    return art


def alle() -> list[Bezugsart]:
    return list(_BEZUGSARTEN.values())


def holen(schluessel: str) -> Bezugsart:
    return _BEZUGSARTEN[schluessel]


def fuer_objekt(objekt: models.Model) -> Bezugsart | None:
    return _BEZUGSARTEN.get(objekt._meta.label_lower)


# --- kern.Objekt: Aushang je Objekt ---------------------------------------------------


def _objekt_kontext(objekt: Any) -> dict[str, Any]:
    return {
        "objekt": {
            "kuerzel": objekt.kuerzel,
            "bezeichnung": objekt.bezeichnung,
            "anschrift": objekt.anschrift,
        },
        "empfaenger": {
            "name": f"An alle Nutzer: {objekt.bezeichnung}",
            "anrede": "Liebe Nutzerinnen und Nutzer",
            "anrede_brief": "Liebe Nutzerinnen und Nutzer,",
            "anschrift": objekt.anschrift,
            "email": "",
        },
    }


def objekt_anmelden() -> None:
    from apps.kern.models import Objekt

    registrieren(
        Objekt,
        titel="Objekte",
        kontext=_objekt_kontext,
        auswahl=lambda: Objekt.objects.filter(aktiv=True).order_by("kuerzel"),
        bezeichnung=lambda o: f"{o.kuerzel} · {o.bezeichnung}",
        filtern=lambda auswahl, objekt, _typ: auswahl.filter(pk=objekt.pk) if objekt else auswahl,
    )
