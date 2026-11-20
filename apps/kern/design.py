"""Designschalter für das Experiment „Apple-Design“ (prompts/15, nur Gestaltung).

Zwei Gestaltungen liegen nebeneinander: ``standard`` (werte.css, bausteine.css) und ``apple``
(zusätzlich static/css/design-apple.css, am ``<html data-design="apple">``).

- Voreingestellt ist ``settings.HOF_DESIGN``: in dev ``apple``, in Tests, E2E und prod
  ``standard``.
- Nur mit ``DEBUG`` lässt sich umschalten: ``?design=standard`` bzw. ``?design=apple`` setzt
  die Wahl und merkt sie im Cookie ``hof_design``; der Schalter steht unten in der
  Seitenleiste und im Blatt „Mehr“. Ohne ``DEBUG`` gibt es weder Schalter noch Parameter
  noch Cookie, es gilt allein ``HOF_DESIGN``.
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


def voreinstellung() -> str:
    design = getattr(settings, "HOF_DESIGN", "standard")
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
    }
