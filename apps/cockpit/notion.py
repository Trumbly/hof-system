"""Client für die Datenbank „Hof-Backlog“ in Notion (austauschbar, mit Fake für Tests).

Der echte Client spricht die offizielle REST-API von Notion (https://api.notion.com/v1)
mit httpx, Header ``Notion-Version`` laut aktueller Doku (``NOTION_VERSION``). Er legt
Seiten an (``POST /v1/pages``) und ändert ihre Eigenschaften (``PATCH /v1/pages/{id}``).
Der Inhalt steht in Eigenschaften, nicht im Seitentext: So lässt er sich mit einem Aufruf
aktualisieren.

Einstellungen (``hof/settings/base.py``, nur auf dem Server gesetzt):

- ``HOF_NOTION_TOKEN``: Token der Integration (nur auf dem Server).
- ``HOF_NOTION_DATENBANK_ID``: ID der Datenbank „Hof-Backlog“. Mit ``HOF_NOTION_PARENT``
  = ``data_source_id`` ist es stattdessen die ID der Datenquelle.
- ``HOF_NOTION_FELDER``: Namen der Eigenschaften in Notion (Titel, Status, Fällig, Link,
  Text) und die Werte für offen und erledigt. Max gibt die echten Namen; bis dahin gelten
  die Standardwerte.

Ohne Token oder Datenbank-ID meldet der echte Client „nicht eingerichtet“, und der Lauf
``aufgaben_abgleich`` endet ohne Fehler.
"""

from __future__ import annotations

import datetime
import itertools
import os
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

import httpx
from django.conf import settings
from django.utils.module_loading import import_string

NOTION_URL = "https://api.notion.com/v1"
#: Aktuelle Version der API laut https://developers.notion.com/reference/versioning
#: (2026-03-11: ``in_trash`` statt ``archived``; sonst gleich für Seiten).
NOTION_VERSION = "2026-03-11"
#: Notion nimmt höchstens 2000 Zeichen je Textstück.
TEXT_GRENZE = 2000


class NotionFehler(Exception):
    """Notion hat nicht wie erwartet geantwortet. Die Meldung nennt nur Status und Art."""


class NotionNichtEingerichtet(NotionFehler):
    """Token oder Datenbank-ID fehlen."""


@dataclass(frozen=True)
class AufgabenInhalt:
    """Was in Notion steht: nur Titel, Text, Fälligkeit, Link und ob erledigt."""

    titel: str
    text: str
    faellig_am: datetime.date | None
    link: str
    erledigt: bool = False


class NotionClient(Protocol):
    def anlegen(self, inhalt: AufgabenInhalt) -> str:
        """Legt eine Aufgabe an und gibt die Page-ID zurück."""

    def aktualisieren(self, page_id: str, inhalt: AufgabenInhalt) -> None:
        """Setzt Titel, Text, Fälligkeit, Link und Status einer Aufgabe."""


def felder() -> dict[str, str]:
    werte: dict[str, str] = settings.HOF_NOTION_FELDER
    return werte


def eigenschaften(inhalt: AufgabenInhalt) -> dict[str, Any]:
    """Die Eigenschaften der Seite im Format der Notion-API."""
    f = felder()
    status_wert = f["erledigt_wert"] if inhalt.erledigt else f["offen_wert"]
    status_typ = f.get("status_typ", "status")
    daten: dict[str, Any] = {
        f["titel"]: {"title": [{"text": {"content": inhalt.titel[:TEXT_GRENZE]}}]},
        f["status"]: {status_typ: {"name": status_wert}},
        f["faellig"]: {
            "date": {"start": inhalt.faellig_am.isoformat()} if inhalt.faellig_am else None
        },
        f["link"]: {"url": inhalt.link or None},
    }
    if f.get("text"):
        daten[f["text"]] = {"rich_text": [{"text": {"content": inhalt.text[:TEXT_GRENZE]}}]}
    return daten


class NotionHttpClient:
    """Echter Client über die REST-API von Notion."""

    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        self.token: str = settings.HOF_NOTION_TOKEN
        self.datenbank_id: str = settings.HOF_NOTION_DATENBANK_ID
        self.parent: str = settings.HOF_NOTION_PARENT
        if not self.token or not self.datenbank_id:
            raise NotionNichtEingerichtet(
                "Notion ist nicht eingerichtet (HOF_NOTION_TOKEN und HOF_NOTION_DATENBANK_ID)."
            )
        if self.parent not in ("database_id", "data_source_id"):
            raise NotionNichtEingerichtet(
                "HOF_NOTION_PARENT muss database_id oder data_source_id sein."
            )
        self._client = httpx.Client(
            base_url=NOTION_URL,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
            timeout=20.0,
            transport=transport,
        )

    def _senden(self, methode: str, pfad: str, daten: dict[str, Any]) -> dict[str, Any]:
        try:
            antwort = self._client.request(methode, pfad, json=daten)
        except httpx.HTTPError as fehler:
            raise NotionFehler(f"Notion nicht erreichbar ({type(fehler).__name__})") from fehler
        if antwort.status_code >= 400:
            # Nur Status und Fehlercode der API, nie den gesendeten Inhalt.
            try:
                code = str(antwort.json().get("code", ""))
            except ValueError:
                code = ""
            raise NotionFehler(f"Notion antwortet {antwort.status_code} {code}".strip())
        ergebnis: dict[str, Any] = antwort.json()
        return ergebnis

    def anlegen(self, inhalt: AufgabenInhalt) -> str:
        daten = {
            "parent": {"type": self.parent, self.parent: self.datenbank_id},
            "properties": eigenschaften(inhalt),
        }
        ergebnis = self._senden("POST", "/pages", daten)
        page_id = ergebnis.get("id")
        if not isinstance(page_id, str) or not page_id:
            raise NotionFehler("Notion hat keine Page-ID geliefert")
        return page_id

    def aktualisieren(self, page_id: str, inhalt: AufgabenInhalt) -> None:
        self._senden("PATCH", f"/pages/{page_id}", {"properties": eigenschaften(inhalt)})


class FakeNotion:
    """Notion ohne Netz, für Entwicklung und Tests.

    Merkt sich die Seiten in ``FakeNotion.seiten`` (Page-ID → Eigenschaften im Format der
    API) und jeden Aufruf in ``FakeNotion.aufrufe``. ``FakeNotion.scheitern = True``
    simuliert einen Ausfall. In der Produktion gesperrt.
    """

    seiten: ClassVar[dict[str, dict[str, Any]]] = {}
    aufrufe: ClassVar[list[tuple[str, str]]] = []
    scheitern: ClassVar[bool] = False
    _nummern: ClassVar[itertools.count[int]] = itertools.count(1)

    def __init__(self) -> None:
        if os.environ.get("DJANGO_ENV", "").strip().lower() == "prod":
            raise NotionNichtEingerichtet("FakeNotion ist in der Produktion gesperrt.")

    @classmethod
    def zuruecksetzen(cls) -> None:
        cls.seiten.clear()
        cls.aufrufe.clear()
        cls.scheitern = False

    def _pruefen(self) -> None:
        if FakeNotion.scheitern:
            raise NotionFehler("Notion antwortet 503 service_unavailable")

    def anlegen(self, inhalt: AufgabenInhalt) -> str:
        self._pruefen()
        page_id = f"fake-{next(FakeNotion._nummern):04d}"
        FakeNotion.seiten[page_id] = eigenschaften(inhalt)
        FakeNotion.aufrufe.append(("anlegen", page_id))
        return page_id

    def aktualisieren(self, page_id: str, inhalt: AufgabenInhalt) -> None:
        self._pruefen()
        if page_id not in FakeNotion.seiten:
            raise NotionFehler("Notion antwortet 404 object_not_found")
        FakeNotion.seiten[page_id] = eigenschaften(inhalt)
        FakeNotion.aufrufe.append(("aktualisieren", page_id))


def notion_client() -> NotionClient:
    klasse = import_string(settings.HOF_NOTION_CLIENT)
    client: NotionClient = klasse()
    return client
