"""Client für die Website (H1.6): Welche NW-IDs führt die Website?

Die Website liest Katalog und Bilder aus dem Hof-System (``apps.anbindungen.api``). Für den
ID-Abgleich fragt das Hof-System umgekehrt die Website nach ihren Einheiten. Den Endpunkt
dafür gibt es auf der Website noch nicht; er ist in ``docs/website-anbindung.md``
beschrieben:

    GET {HOF_WEBSITE_URL}/api/hof/einheiten
    Authorization: Bearer {HOF_WEBSITE_TOKEN}

    {"einheiten": [{"nw_id": "NW-G-0001", "slug": "garage-1"}, …]}

Austauschbar über ``HOF_WEBSITE_CLIENT``; in Entwicklung und Tests ``FakeWebsite``.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from django.conf import settings
from django.utils.module_loading import import_string

PFAD_EINHEITEN = "/api/hof/einheiten"
ZEITLIMIT_SEKUNDEN = 20


class WebsiteFehler(Exception):
    """Die Website antwortet nicht wie erwartet. Die Meldung nennt keine Werte."""


class WebsiteNichtEingerichtet(WebsiteFehler):
    """Adresse oder Token der Website fehlen in der Umgebung."""


@dataclass(frozen=True)
class WebsiteEinheit:
    """Eine Einheit, wie die Website sie führt."""

    nw_id: str
    slug: str = ""


class WebsiteClient(Protocol):
    def einheiten(self) -> list[WebsiteEinheit]: ...


def einheiten_aus_antwort(daten: Any) -> list[WebsiteEinheit]:
    """Liest die Antwort der Website. Fehler nennen nur Position und Feld."""
    if not isinstance(daten, dict) or not isinstance(daten.get("einheiten"), list):
        raise WebsiteFehler("Website: Antwort ohne Liste „einheiten“.")
    ergebnis = []
    for nummer, eintrag in enumerate(daten["einheiten"]):
        if not isinstance(eintrag, dict) or not isinstance(eintrag.get("nw_id"), str):
            raise WebsiteFehler(f"Website: einheiten[{nummer}].nw_id fehlt.")
        slug = eintrag.get("slug") or ""
        if not isinstance(slug, str):
            raise WebsiteFehler(f"Website: einheiten[{nummer}].slug ist kein Text.")
        ergebnis.append(WebsiteEinheit(nw_id=eintrag["nw_id"], slug=slug))
    return ergebnis


class WebsiteHttpClient:
    """Fragt den Endpunkt ``/api/hof/einheiten`` der Website ab (nur lesend)."""

    def __init__(self) -> None:
        self.url = settings.HOF_WEBSITE_URL.rstrip("/")
        self.token = settings.HOF_WEBSITE_TOKEN
        if not self.url or not self.token:
            raise WebsiteNichtEingerichtet(
                "Website ist nicht eingerichtet (HOF_WEBSITE_URL und HOF_WEBSITE_TOKEN fehlen)."
            )

    def einheiten(self) -> list[WebsiteEinheit]:
        anfrage = urllib.request.Request(  # noqa: S310  (Adresse aus der Einstellung)
            self.url + PFAD_EINHEITEN,
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(anfrage, timeout=ZEITLIMIT_SEKUNDEN) as antwort:  # noqa: S310
                daten = json.loads(antwort.read())
        except urllib.error.HTTPError as fehler:
            raise WebsiteFehler(f"Website: HTTP {fehler.code}.") from fehler
        except (urllib.error.URLError, TimeoutError) as fehler:
            raise WebsiteFehler("Website: nicht erreichbar.") from fehler
        except ValueError as fehler:
            raise WebsiteFehler("Website: Antwort ist kein JSON.") from fehler
        return einheiten_aus_antwort(daten)


def _fake_erlaubt(name: str) -> None:
    if os.environ.get("DJANGO_ENV", "").strip().lower() == "prod":
        raise WebsiteNichtEingerichtet(
            f"{name} ist ein Fake für Entwicklung und Tests und in der Produktion gesperrt."
        )


class FakeWebsite:
    """Website ohne Netz, für Entwicklung und Tests.

    Ohne Angaben führt der Fake genau die Einheiten, die das Hof-System auf der Website
    vermarktet (nicht aufgelöst, kein Wohnraum), mit ihrem Slug. ``einheiten`` gibt die
    Liste vor; ``umbenennen`` ersetzt einzelne NW-IDs (Hof-ID -> ID auf der Website), etwa
    für die absichtliche Abweichung im Testhof.
    """

    def __init__(
        self,
        einheiten: list[WebsiteEinheit] | None = None,
        umbenennen: dict[str, str] | None = None,
        fehler: bool = False,
    ) -> None:
        _fake_erlaubt("FakeWebsite")
        self._einheiten = einheiten
        self._umbenennen = umbenennen or {}
        self._fehler = fehler

    def einheiten(self) -> list[WebsiteEinheit]:
        if self._fehler:
            raise WebsiteFehler("Website (Fake): nicht erreichbar.")
        if self._einheiten is not None:
            return list(self._einheiten)
        from apps.stammdaten.models import Einheit

        vermarktet = (
            Einheit.objects.filter(
                vermarktung=Einheit.Vermarktung.WEBSITE, aufgeloest_am__isnull=True
            )
            .exclude(typ=Einheit.Typ.WOHNUNG)
            .order_by("nw_id")
        )
        return [
            WebsiteEinheit(nw_id=self._umbenennen.get(e.nw_id, e.nw_id), slug=e.website_slug)
            for e in vermarktet
        ]


def website_client() -> WebsiteClient:
    klasse = import_string(settings.HOF_WEBSITE_CLIENT)
    client: WebsiteClient = klasse()
    return client
