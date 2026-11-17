"""Seiten des Kerns: Start, Arbeitslisten, Protokoll, Läufe, Gesundheits-Endpunkt."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import groupby
from typing import Any

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import DatabaseError, connection, transaction
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST
from pghistory.models import Events

from apps.kern import aktionen, services
from apps.kern.models import Arbeitsposten, Benutzer, Lauf, Objekt
from apps.kern.protokoll import ART_TITEL, protokollierte_modelle

PROTOKOLL_ANZAHL = 100
LAEUFE_ANZAHL = 50


@login_required
def startseite(request: HttpRequest) -> HttpResponse:
    """Startseite. Wird in Schritt 9 zum Hof-Cockpit."""
    return render(
        request,
        "kern/startseite.html",
        {
            "offene_posten": Arbeitsposten.objects.filter(
                status=Arbeitsposten.Status.OFFEN
            ).count(),
            "letzter_lauf": Lauf.objects.first(),
            "objekte": Objekt.objects.filter(aktiv=True).select_related("mandant"),
        },
    )


# --- Arbeitslisten -------------------------------------------------------------------


def _ist_htmx(request: HttpRequest) -> bool:
    return request.headers.get("HX-Request") == "true"


@login_required
def arbeitslisten(request: HttpRequest) -> HttpResponse:
    """Alle offenen Arbeitsposten, gruppiert nach Art; zurückgestellte darunter."""
    offen = (
        Arbeitsposten.objects.filter(status=Arbeitsposten.Status.OFFEN)
        .select_related("bezug_typ", "lauf")
        .order_by("art", "faellig_am", "angelegt_am", "pk")
    )
    gruppen = [(art, list(posten)) for art, posten in groupby(offen, key=lambda p: p.art)]
    zurueckgestellt = (
        Arbeitsposten.objects.filter(status=Arbeitsposten.Status.ZURUECKGESTELLT)
        .select_related("bezug_typ", "lauf")
        .order_by("art", "pk")
    )
    return render(
        request,
        "kern/arbeitslisten.html",
        {"gruppen": gruppen, "zurueckgestellt": zurueckgestellt},
    )


@login_required
@require_POST
def arbeitsposten_aktion(request: HttpRequest, pk: int, aktion: str) -> HttpResponse:
    """Führt eine Aktion an einem Arbeitsposten aus (per HTMX oder als normales Formular)."""
    posten = get_object_or_404(Arbeitsposten.objects.select_for_update(), pk=pk)
    if not aktionen.erlaubt(posten, aktion):
        return HttpResponseBadRequest("Diese Aktion ist für den Arbeitsposten nicht möglich.")
    ausfuehrung = aktionen.holen(aktion)
    benutzer = request.user
    if not isinstance(benutzer, Benutzer):  # login_required stellt das sicher
        return HttpResponseBadRequest("Nicht angemeldet.")
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


# --- Protokoll -----------------------------------------------------------------------


@dataclass
class Protokollzeile:
    ereignis_id: str
    zeit: Any
    art: str
    art_titel: str
    modell_titel: str
    objekt_id: str | None
    aenderungen: list[tuple[str, Any, Any]]
    kontext: str
    ruecknahme_von: str | None
    zurueckgenommen: bool


def _modellwahl() -> list[tuple[str, str]]:
    return sorted(
        ((m._meta.label, str(m._meta.verbose_name_plural)) for m in protokollierte_modelle()),
        key=lambda eintrag: eintrag[1],
    )


def _kontext_text(kontext: dict[str, Any] | None, benutzer: dict[int, str]) -> str:
    if not kontext:
        return "ohne Kontext"
    teile = []
    if kontext.get("lauf"):
        teile.append(f"Lauf #{kontext['lauf']} {kontext.get('lauf_name', '')}".strip())
    if kontext.get("user"):
        teile.append(benutzer.get(int(kontext["user"]), f"Benutzer #{kontext['user']}"))
    if kontext.get("ruecknahme_von"):
        teile.append(f"Rücknahme von {kontext['ruecknahme_von']}")
    return " · ".join(teile) or "ohne Kontext"


@login_required
def protokoll(request: HttpRequest) -> HttpResponse:
    """Letzte Änderungen, filterbar nach Modell, mit Knopf „Zurücknehmen“."""
    modellwahl = _modellwahl()
    gewaehlt = request.GET.get("modell", "")
    ereignisse = Events.objects.order_by("-pgh_created_at", "-pgh_id")
    if gewaehlt in dict(modellwahl):
        ereignisse = ereignisse.filter(pgh_obj_model=gewaehlt)
    else:
        gewaehlt = ""
    ereignisse = list(ereignisse[:PROTOKOLL_ANZAHL])

    ids = [e.pgh_slug for e in ereignisse]
    zurueckgenommen = set(
        Events.objects.filter(pgh_context__ruecknahme_von__in=ids).values_list(
            "pgh_context__ruecknahme_von", flat=True
        )
    )
    benutzer_ids = {
        int(e.pgh_context["user"]) for e in ereignisse if (e.pgh_context or {}).get("user")
    }
    benutzer = {b.pk: b.email for b in get_user_model().objects.filter(pk__in=benutzer_ids)}
    titel = {label: name for label, name in modellwahl}

    zeilen = []
    for e in ereignisse:
        kontext = e.pgh_context or {}
        aenderungen = [
            (feld, werte[0], werte[1])
            for feld, werte in sorted((e.pgh_diff or {}).items())
            if e.pgh_label == "update"
        ]
        zeilen.append(
            Protokollzeile(
                ereignis_id=e.pgh_slug,
                zeit=e.pgh_created_at,
                art=e.pgh_label,
                art_titel=ART_TITEL.get(e.pgh_label, e.pgh_label),
                modell_titel=titel.get(e.pgh_obj_model, e.pgh_obj_model),
                objekt_id=e.pgh_obj_id,
                aenderungen=aenderungen,
                kontext=_kontext_text(kontext, benutzer),
                ruecknahme_von=kontext.get("ruecknahme_von"),
                zurueckgenommen=e.pgh_slug in zurueckgenommen,
            )
        )
    return render(
        request,
        "kern/protokoll.html",
        {"zeilen": zeilen, "modellwahl": modellwahl, "gewaehlt": gewaehlt},
    )


@login_required
@require_POST
def zuruecknehmen(request: HttpRequest) -> HttpResponse:
    ereignis_id = request.POST.get("ereignis", "")
    try:
        ereignis = services.zuruecknehmen(ereignis_id)
    except services.RuecknahmeNichtMoeglich as fehler:
        messages.error(request, f"Nicht zurückgenommen: {fehler}")
    else:
        messages.success(
            request,
            f"Zurückgenommen: {ereignis.modell._meta.verbose_name} #{ereignis.objekt_id} "
            f"({ART_TITEL.get(ereignis.art, ereignis.art)}).",
        )
    ziel = reverse("kern:protokoll")
    modell = request.POST.get("modell")
    if modell:
        ziel += "?" + urlencode({"modell": modell})
    return redirect(ziel)


# --- Läufe ---------------------------------------------------------------------------


@login_required
def laeufe(request: HttpRequest) -> HttpResponse:
    """Letzte Läufe mit Status und Zählern."""
    return render(request, "kern/laeufe.html", {"laeufe": Lauf.objects.all()[:LAEUFE_ANZAHL]})


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
