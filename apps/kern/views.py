"""Seiten des Kerns: Arbeitslisten und Gesundheits-Endpunkt.

Protokoll und Läufe stehen in ``protokoll_seite`` und ``laeufe_seite``.
"""

from __future__ import annotations

from itertools import groupby

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import DatabaseError, connection, transaction
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from apps.kern import aktionen, vorgaenge
from apps.kern.models import Arbeitsposten, Benutzer

PROTOKOLL_ANZAHL = 50
LAEUFE_ANZAHL = 50


# --- Arbeitslisten -------------------------------------------------------------------


def _ist_htmx(request: HttpRequest) -> bool:
    return request.headers.get("HX-Request") == "true"


@login_required
def arbeitslisten(request: HttpRequest) -> HttpResponse:
    """Alle offenen Arbeitsposten, gruppiert nach Art; zurückgestellte darunter.

    ``?art=…`` zeigt nur eine Art (Links aus dem Cockpit und dem Hof-Backlog).
    """
    art_filter = request.GET.get("art", "")
    offen = (
        Arbeitsposten.objects.filter(status=Arbeitsposten.Status.OFFEN)
        .select_related("bezug_typ", "lauf")
        .order_by("art", "faellig_am", "angelegt_am", "pk")
    )
    zurueckgestellt = (
        Arbeitsposten.objects.filter(status=Arbeitsposten.Status.ZURUECKGESTELLT)
        .select_related("bezug_typ", "lauf")
        .order_by("art", "pk")
    )
    if art_filter:
        offen = offen.filter(art=art_filter)
        zurueckgestellt = zurueckgestellt.filter(art=art_filter)
    gruppen = [(art, list(posten)) for art, posten in groupby(offen, key=lambda p: p.art)]
    return render(
        request,
        "kern/arbeitslisten.html",
        {"gruppen": gruppen, "zurueckgestellt": zurueckgestellt, "art_filter": art_filter},
    )


@login_required
@require_POST
def arbeitsposten_aktion(request: HttpRequest, pk: int, aktion: str) -> HttpResponse:
    """Führt eine Aktion an einem Arbeitsposten aus (per HTMX oder als normales Formular)."""
    posten = get_object_or_404(Arbeitsposten.objects.select_for_update(), pk=pk)
    if not aktionen.erlaubt(posten, aktion):
        return HttpResponseBadRequest("Diese Aktion ist für den Arbeitsposten nicht möglich.")
    ausfuehrung = aktionen.holen(aktion)
    if ausfuehrung.seite is not None:
        # Aktion mit Eingabe: weiter auf ihre Seite (bei HTMX als ganze Seite).
        ziel = ausfuehrung.seite(posten)
        if _ist_htmx(request):
            weiter = HttpResponse(status=200)
            weiter["HX-Redirect"] = ziel
            return weiter
        return redirect(ziel)
    benutzer = request.user
    if not isinstance(benutzer, Benutzer):  # login_required stellt das sicher
        return HttpResponseBadRequest("Nicht angemeldet.")
    with vorgaenge.vorgang(f"Arbeitsposten #{posten.pk}: {ausfuehrung.titel}"):
        ausfuehrung.ausfuehren(posten, benutzer)
    posten.refresh_from_db()
    if _ist_htmx(request):
        return render(
            request,
            "kern/_arbeitsposten.html",
            {"posten": posten, "erledigte_aktion": ausfuehrung},
        )
    messages.success(request, f"Arbeitsposten #{posten.pk}: {ausfuehrung.titel}.")
    return redirect("kern:arbeitslisten")


# --- Gesundheit ----------------------------------------------------------------------


@transaction.non_atomic_requests
@never_cache
@require_GET
def gesund(request: HttpRequest) -> HttpResponse:
    """Gesundheits-Endpunkt für deploy.sh und die Healthchecks der Container.

    Ohne Anmeldung, ohne Daten: nur „ok“ und die Version (``HOF_VERSION``). Erreichbar ist
    er nur über Tailscale, weil Caddy und die Firewall nichts anderes durchlassen. Kann die
    Datenbank nicht antworten, kommt 503 mit „fehler“ und wieder nur der Version.
    """
    version = settings.HOF_VERSION
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except DatabaseError:
        return HttpResponse(
            f"fehler {version}\n", status=503, content_type="text/plain; charset=utf-8"
        )
    return HttpResponse(f"ok {version}\n", content_type="text/plain; charset=utf-8")
