"""Seite „Fristen“: Liste mit Filtern nach Zeitraum, Art und Quelle, Aktion „Erledigt“."""

from __future__ import annotations

import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import BooleanField, Case, Count, Q, QuerySet, Value, When
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.fristen import services
from apps.fristen.models import Frist

#: Zeiträume der Liste: Schlüssel, Titel. „offen“ zeigt alle offenen, auch überfällige.
ZEITRAEUME: tuple[tuple[str, str], ...] = (
    ("offen", "alle offenen"),
    ("ueberfaellig", "überfällig"),
    ("14", "offen, bis in 14 Tagen"),
    ("30", "offen, bis in 30 Tagen"),
    ("90", "offen, bis in 90 Tagen"),
    ("erledigt", "erledigt"),
    ("alle", "alle"),
)
STANDARD_ZEITRAUM = "offen"


def _ist_htmx(request: HttpRequest) -> bool:
    return request.headers.get("HX-Request") == "true"


def _mit_angaben(fristen: QuerySet[Frist]) -> QuerySet[Frist]:
    return fristen.select_related("bezug_typ").annotate(anzahl_verschoben=Count("verschiebungen"))


def sortiert(fristen: QuerySet[Frist], heute: datetime.date) -> QuerySet[Frist]:
    """Überfällige zuerst, dann aufsteigend nach Datum (Review 2, A5).

    Ausdrücklich sortiert: Mit ``annotate(Count(...))`` gilt ``Meta.ordering`` nicht.
    """
    return fristen.annotate(
        ist_ueberfaellig=Case(
            When(Q(erledigt_am__isnull=True, datum__lt=heute), then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        )
    ).order_by("-ist_ueberfaellig", "datum", "pk")


def _nach_zeitraum(
    fristen: QuerySet[Frist], zeitraum: str, heute: datetime.date
) -> QuerySet[Frist]:
    if zeitraum == "alle":
        return sortiert(fristen, heute)
    if zeitraum == "erledigt":
        return fristen.filter(erledigt_am__isnull=False).order_by("-erledigt_am", "-pk")
    fristen = fristen.filter(erledigt_am__isnull=True)
    if zeitraum == "ueberfaellig":
        return sortiert(fristen.filter(datum__lt=heute), heute)
    if zeitraum.isdigit():
        fristen = fristen.filter(datum__lte=heute + datetime.timedelta(days=int(zeitraum)))
    return sortiert(fristen, heute)


@login_required
def fristenliste(request: HttpRequest) -> HttpResponse:
    heute = timezone.localdate()
    zeitraum = request.GET.get("zeitraum", STANDARD_ZEITRAUM)
    if zeitraum not in dict(ZEITRAEUME):
        zeitraum = STANDARD_ZEITRAUM
    art = request.GET.get("art", "")
    if art not in Frist.Art.values:
        art = ""
    quelle = request.GET.get("quelle", "")
    if quelle not in Frist.Quelle.values:
        quelle = ""

    fristen = _mit_angaben(Frist.objects.all())
    if art:
        fristen = fristen.filter(art=art)
    if quelle:
        fristen = fristen.filter(quelle=quelle)
    fristen = _nach_zeitraum(fristen, zeitraum, heute)

    return render(
        request,
        "fristen/liste.html",
        {
            "fristen": list(fristen),
            "heute": heute,
            "zeitraeume": ZEITRAEUME,
            "arten": Frist.Art.choices,
            "quellen": Frist.Quelle.choices,
            "gewaehlt": {"zeitraum": zeitraum, "art": art, "quelle": quelle},
        },
    )


@login_required
@require_POST
def erledigt(request: HttpRequest, pk: int) -> HttpResponse:
    """Setzt das Erledigt-Datum auf heute (per HTMX oder als normales Formular)."""
    frist = get_object_or_404(Frist, pk=pk)
    if frist.ist_erledigt:
        return HttpResponseBadRequest("Die Frist ist schon erledigt.")
    frist = services.frist_erledigen(frist)
    if _ist_htmx(request):
        return render(
            request,
            "fristen/_frist.html",
            {
                "frist": _mit_angaben(Frist.objects.filter(pk=frist.pk)).get(),
                "heute": timezone.localdate(),
                "gerade_erledigt": True,
            },
        )
    messages.success(request, f"Frist #{frist.pk} erledigt.")
    zurueck = request.POST.get("zurueck", "")
    if not url_has_allowed_host_and_scheme(
        zurueck, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        zurueck = reverse("fristen:liste")
    return redirect(zurueck)
