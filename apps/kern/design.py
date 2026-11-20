"""Designschalter: bisherige Gestaltung („standard“) oder Apple-Design („apple“).

Zwei Gestaltungen liegen nebeneinander: ``standard`` (werte.css, bausteine.css) und ``apple``
(zusätzlich die Dateien unter static/css/apple/, am ``<html data-design="apple">``; Liste in
``APPLE_CSS``, base.html lädt sie in dieser Reihenfolge).

- Voreingestellt ist ``settings.HOF_DESIGN_STANDARD``: in dev und E2E ``apple``, in Tests und
  prod ``standard``.
- Nur mit ``DEBUG`` lässt sich umschalten: ``?design=standard`` bzw. ``?design=apple`` setzt
  die Wahl und merkt sie im Cookie ``hof_design``; der Schalter steht unten in der
  Seitenleiste und im Blatt „Mehr“. Ohne ``DEBUG`` gibt es weder Schalter noch Parameter
  noch Cookie, es gilt allein ``HOF_DESIGN_STANDARD``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse

DESIGNS = ("standard", "apple")
COOKIE = "hof_design"
#: Ein Jahr: Die Wahl bleibt, bis man sie ändert.
COOKIE_DAUER = 365 * 24 * 60 * 60

#: Stylesheets des Apple-Designs in fester Reihenfolge, nach werte.css, hof.css und
#: bausteine.css: Werte und Rahmen, Bausteine, dann je Bereich eine Datei. Kein @import,
#: damit der Browser alle zugleich lädt.
APPLE_CSS = (
    "css/apple/basis.css",
    "css/apple/bausteine.css",
    "css/apple/arbeitslisten.css",
    "css/apple/stammdaten.css",
    "css/apple/geld.css",
    "css/apple/dokumente.css",
    "css/apple/system.css",
)


def voreinstellung() -> str:
    design = getattr(settings, "HOF_DESIGN_STANDARD", "standard")
    return design if design in DESIGNS else "standard"


def schalter_an() -> bool:
    return bool(settings.DEBUG)


class DesignMiddleware:
    """Legt ``request.hof_design`` fest und merkt eine Wahl aus ``?design=`` im Cookie."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        design = voreinstellung()
        gewaehlt = None
        if schalter_an():
            gewaehlt = request.GET.get("design")
            if gewaehlt in DESIGNS:
                design = gewaehlt
            else:
                gewaehlt = None
                if request.COOKIES.get(COOKIE) in DESIGNS:
                    design = request.COOKIES[COOKIE]
        request.hof_design = design  # type: ignore[attr-defined]
        antwort = self.get_response(request)
        if gewaehlt:
            antwort.set_cookie(COOKIE, gewaehlt, max_age=COOKIE_DAUER, samesite="Lax")
        return antwort


def design(request: HttpRequest) -> dict[str, Any]:
    """Context Processor: ``hof_design`` (Name der Gestaltung) und ``hof_design_schalter``
    (Schalter zeigen, nur mit ``DEBUG``)."""
    return {
        "hof_design": getattr(request, "hof_design", voreinstellung()),
        "hof_design_schalter": schalter_an(),
        "hof_designs": DESIGNS,
        "hof_apple_css": APPLE_CSS,
    }
