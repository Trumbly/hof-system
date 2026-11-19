"""Seiten des Kerns: Arbeitslisten, Protokoll, Läufe, Gesundheits-Endpunkt."""

from __future__ import annotations

import datetime
import uuid
from itertools import groupby

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import DatabaseError, connection, transaction
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.utils.http import urlencode
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from apps.kern import aktionen, services, vorgaenge
from apps.kern.models import Arbeitsposten, Benutzer, Lauf
from apps.kern.protokoll import protokollierte_modelle

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


# --- Protokoll -----------------------------------------------------------------------


def _modellwahl() -> list[tuple[str, str]]:
    return sorted(
        ((m._meta.label, str(m._meta.verbose_name_plural)) for m in protokollierte_modelle()),
        key=lambda eintrag: eintrag[1],
    )


def _datum(text: str) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(text) if text else None
    except ValueError:
        return None


@login_required
def protokoll(request: HttpRequest) -> HttpResponse:
    """Die letzten Vorgänge (Review 2, A4 und B14): je Nutzeraktion oder Lauf eine Zeile mit
    Titel, Zeit und Zahl der Änderungen, die Einzelheiten zum Aufklappen.

    Filter: ``modell`` (Label), ``bezug`` (``app.modell:ID``, etwa ein Vertrag, eine
    Einheit oder eine Partei), ``von``/``bis`` (Datum), ``seite``.
    """
    modellwahl = _modellwahl()
    gewaehlt = request.GET.get("modell", "")
    if gewaehlt not in dict(modellwahl):
        gewaehlt = ""
    bezug_text = request.GET.get("bezug", "")
    bezug = vorgaenge.bezug_aus_text(bezug_text) if bezug_text else None
    von = _datum(request.GET.get("von", ""))
    bis = _datum(request.GET.get("bis", ""))
    try:
        seite = max(1, int(request.GET.get("seite", "1")))
    except ValueError:
        seite = 1
    zeilen = vorgaenge.vorgaenge_suchen(
        modell=gewaehlt,
        bezug=bezug,
        von=von,
        bis=bis,
        anzahl=PROTOKOLL_ANZAHL + 1,
        versatz=(seite - 1) * PROTOKOLL_ANZAHL,
    )
    weiter = len(zeilen) > PROTOKOLL_ANZAHL
    filter_ohne_seite = {
        k: v
        for k, v in {
            "modell": gewaehlt,
            "bezug": bezug_text if bezug else "",
            "von": von.isoformat() if von else "",
            "bis": bis.isoformat() if bis else "",
        }.items()
        if v
    }
    return render(
        request,
        "kern/protokoll.html",
        {
            "zeilen": zeilen[:PROTOKOLL_ANZAHL],
            "modellwahl": modellwahl,
            "gewaehlt": gewaehlt,
            "bezug": bezug,
            "bezug_text": bezug_text if bezug else "",
            "bezug_titel": vorgaenge.objekt_titel(
                type(bezug),
                {f.attname: getattr(bezug, f.attname) for f in type(bezug)._meta.concrete_fields},
            )
            if bezug is not None
            else "",
            "von": von,
            "bis": bis,
            "seite": seite,
            "weiter_url": "?" + urlencode({**filter_ohne_seite, "seite": seite + 1})
            if weiter
            else "",
            "zurueck_url": "?" + urlencode({**filter_ohne_seite, "seite": seite - 1})
            if seite > 1
            else "",
        },
    )


@login_required
def vorgang(request: HttpRequest, pk: uuid.UUID) -> HttpResponse:
    """Ein Vorgang mit allen Einzelheiten und der Vorschau seiner Rücknahme.

    Lässt er sich nicht zurücknehmen, sagt die Seite warum (mit Link zur Ursache, etwa zum
    versandten Schreiben).
    """
    zeile = vorgaenge.vorgang_lesen(pk)
    if zeile is None:
        raise Http404("Diesen Vorgang gibt es nicht.")
    vorschau: services.Ruecknahmebericht | None = None
    sperren: list[services.Sperre] = []
    if zeile.zuruecknehmbar or zeile.zurueckgenommen_durch:
        try:
            vorschau = services.ruecknahme_vorschau(pk)
        except services.RuecknahmeGesperrt as fehler:
            sperren = fehler.sperren
        except services.RuecknahmeNichtMoeglich as fehler:
            sperren = [services.Sperre(str(fehler))]
    elif zeile.ruecknahme_von is not None or zeile.art == vorgaenge.Art.RUECKNAHME:
        sperren = [
            services.Sperre(
                "Eine Rücknahme lässt sich nicht zurücknehmen. Bitte die ursprüngliche Aktion "
                "neu ausführen, etwa die Kündigung neu erfassen."
            )
        ]
    elif zeile.testhof:
        sperren = [
            services.Sperre(
                "Den Aufbau des Testhofs nimmt man nicht zurück; „hof testhof“ baut ihn neu auf."
            )
        ]
    return render(
        request,
        "kern/vorgang.html",
        {
            "v": zeile,
            "vorschau": vorschau,
            "sperren": sperren,
            "ruecknahme_von_titel": vorgaenge.titel_von(zeile.ruecknahme_von)
            if zeile.ruecknahme_von
            else "",
        },
    )


@login_required
@require_POST
def vorgang_zuruecknehmen(request: HttpRequest, pk: uuid.UUID) -> HttpResponse:
    """Nimmt einen ganzen Vorgang zurück. Die Meldung sagt, was passiert ist, mit Link."""
    try:
        bericht = services.vorgang_zuruecknehmen(pk)
    except services.RuecknahmeNichtMoeglich as fehler:
        messages.error(request, f"Nicht zurückgenommen: {fehler}")
        if vorgaenge.vorgang_lesen(pk) is None:
            return redirect("kern:protokoll")
        return redirect("kern:vorgang", pk=pk)
    kurz = vorgaenge.zusammenfassen(s.text for s in bericht.schritte)
    schritte = " · ".join(kurz[:8])
    if len(kurz) > 8:
        schritte += f" · und {len(kurz) - 8} weitere"
    messages.success(
        request,
        format_html(
            '„{}“ zurückgenommen{}{} → <a href="{}">Rücknahme ansehen</a>',
            bericht.titel,
            ": " if schritte else ".",
            schritte,
            reverse("kern:vorgang", args=[bericht.ruecknahme_id]),
        ),
    )
    return redirect("kern:protokoll")


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
