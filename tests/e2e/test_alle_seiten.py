"""Alle Seiten im neuen Gerüst (Review 2, C1; R2-5): Ab der Startseite folgt der Test jedem
Link der Oberfläche und prüft je Seite (eine Adresse je URL-Name) bei 1440 und 390 px
dasselbe wie der Smoke-Test: kein Serverfehler, keine waagrechte Scrollleiste, keine
englischen Standardtexte, am Handy Ziele mindestens 44 px.

Damit bleiben auch die Seiten lesbar, die noch altes Markup tragen (hof.css, Ebene „alt“).
Nur GET; Formulare werden nicht abgeschickt. Ergebnis: ``tests/e2e/artefakte/alle_seiten.jsonl``.
"""

from __future__ import annotations

import json
import re
from collections import deque
from typing import Any
from urllib.parse import urldefrag, urlsplit

import pytest
from django.urls import Resolver404, resolve
from playwright.sync_api import Page

from tests.e2e.conftest import DESKTOP, HANDY
from tests.e2e.server import ARTEFAKTE, Server
from tests.e2e.test_smoke import pruefen

pytestmark = pytest.mark.e2e

#: Nicht besuchen: Django-Verwaltung (eigenes Layout), Dateien, Schnittstellen, Abmelden.
AUSGENOMMEN = (
    "/verwaltung/",
    "/medien/",
    "/static/",
    "/api/",
    "/konto/logout/",
    "/konto/2fa/recovery-codes/download/",
)
DATEIEN = re.compile(r"\.(pdf|csv|zip|png|jpe?g|webp|json|txt)$", re.IGNORECASE)
HREF = re.compile(r'href="([^"#][^"]*)"')
#: Obergrenze an Seiten (URL-Namen), falls ein Link-Muster ins Uferlose führt
HOECHSTENS = 300


def _url_name(pfad: str) -> str | None:
    try:
        return resolve(urlsplit(pfad).path).view_name
    except Resolver404:
        return None


#: Seiten, zu denen kein einfacher Link führt (Formular, HTMX oder Konto)
WEITERE = ("/stil/", "/suche/?q=V-0005", "/konto/2fa/", "/konto/2fa/recovery-codes/")


def _seiten_sammeln(seite: Page, server: Server) -> dict[str, str]:
    """URL-Name → erste gefundene Adresse, über die Links ab der Startseite."""
    gefunden: dict[str, str] = {}
    gesehen: set[str] = set()
    offen: deque[str] = deque(["/", *server.adressen["seiten"].values(), *WEITERE])
    while offen and len(gefunden) < HOECHSTENS:
        pfad = offen.popleft()
        if pfad in gesehen:
            continue
        gesehen.add(pfad)
        name = _url_name(pfad)
        if name is None or name in gefunden:
            continue
        antwort = seite.request.get(server.url(pfad), max_redirects=0)
        if not antwort.headers.get("content-type", "").startswith("text/html"):
            continue
        gefunden[name] = pfad
        if antwort.status >= 300:
            continue
        for href in HREF.findall(antwort.text()):
            href = urldefrag(href.replace("&amp;", "&"))[0]
            if href.startswith(server.basis_url):
                href = href.removeprefix(server.basis_url)
            if (
                not href.startswith("/")
                or href.startswith("//")
                or href.startswith(AUSGENOMMEN)
                or DATEIEN.search(urlsplit(href).path)
            ):
                continue
            if href not in gesehen:
                offen.append(href)
    return gefunden


def test_alle_seiten_im_geruest(seite: Page, server: Server) -> None:
    """Review 2, C1 (R2-5): Jede erreichbare Seite ist im neuen Gerüst lesbar: kein
    Serverfehler, keine waagrechte Scrollleiste, kein „Choose file“, am Handy Knöpfe und
    Haken mindestens 44 px."""
    seiten = _seiten_sammeln(seite, server)
    assert len(seiten) > 40, f"Zu wenige Seiten gefunden: {sorted(seiten)}"
    ergebnis: list[dict[str, Any]] = []
    befunde_alle: list[str] = []
    for name, pfad in sorted(seiten.items()):
        for breite, groesse in (("1440", DESKTOP), ("390", HANDY)):
            seite.set_viewport_size(groesse)
            antwort = seite.goto(pfad, wait_until="networkidle")
            status = antwort.status if antwort is not None else 0
            befunde = [f"Status {status}"] if status >= 500 else []
            befunde += pruefen(seite, handy=breite == "390")
            ergebnis.append({"name": name, "pfad": pfad, "breite": breite, "befunde": befunde})
            if befunde:
                befunde_alle.append(f"{name} ({pfad}) bei {breite} px: {'; '.join(befunde)}")
    (ARTEFAKTE / "alle_seiten.jsonl").write_text(
        "\n".join(json.dumps(z, ensure_ascii=False) for z in ergebnis) + "\n", encoding="utf-8"
    )
    assert not befunde_alle, f"{len(befunde_alle)} Befunde:\n- " + "\n- ".join(befunde_alle)
