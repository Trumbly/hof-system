"""Seiten der Sollstellung: „Offene Posten“ als Gesamtliste mit Filter und je Vertrag.

Gespeichert wird über ``services``; die Seiten prüfen nur die Eingaben.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q, QuerySet
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.soll import services
from apps.soll.forms import FilterForm, PostenForm, SollregelForm, StornoForm
from apps.soll.models import Sollposten, Sollregel
from apps.stammdaten.models import Vertrag

#: Höchstens so viele Zeilen zeigt die Gesamtliste (danach filtern).
GRENZE = 500

OFFEN = (Sollposten.Status.OFFEN, Sollposten.Status.TEILWEISE)


def _gefiltert(form: FilterForm) -> QuerySet[Sollposten]:
    qs = Sollposten.objects.select_related("vertrag", "vertrag__mandant").prefetch_related(
        "vertrag__einheiten"
    )
    daten: dict[str, Any] = form.cleaned_data if form.is_valid() else {}
    status = daten.get("status") or "offen"
    if status in ("offen", "ueberfaellig"):
        qs = qs.filter(status__in=OFFEN)
        if status == "ueberfaellig":
            qs = qs.filter(faellig_am__lt=services.heute())
    elif status != "alle":
        qs = qs.filter(status=status)
    if daten.get("art"):
        qs = qs.filter(art=daten["art"])
    if daten.get("mandant"):
        qs = qs.filter(vertrag__mandant=daten["mandant"])
    if daten.get("faellig_bis"):
        qs = qs.filter(faellig_am__lte=daten["faellig_bis"])
    if daten.get("suche"):
        suche = daten["suche"]
        qs = qs.filter(
            Q(vertrag__nummer__icontains=suche) | Q(vertrag__einheiten__nw_id__icontains=suche)
        ).distinct()
    return qs.order_by("faellig_am", "vertrag__nummer", "art", "pk")


@login_required
def liste(request: HttpRequest) -> HttpResponse:
    form = FilterForm(request.GET or None)
    qs = _gefiltert(form)
    posten = list(qs[: GRENZE + 1])
    abgeschnitten = len(posten) > GRENZE
    posten = posten[:GRENZE]
    return render(
        request,
        "soll/liste.html",
        {
            "form": form,
            "posten": posten,
            "abgeschnitten": abgeschnitten,
            "grenze": GRENZE,
            "summe_offen": sum((p.offen for p in posten), Decimal("0.00")),
            "heute": services.heute(),
        },
    )


def _formular(request: HttpRequest, aktion: str, klasse: type[forms.Form], **kw: Any) -> Any:
    if request.method == "POST" and request.POST.get("aktion") == aktion:
        return klasse(request.POST, prefix=aktion, **kw)
    return klasse(prefix=aktion, **kw)


@login_required
def vertrag(request: HttpRequest, pk: int) -> HttpResponse:
    vertrag = get_object_or_404(Vertrag.objects.select_related("mandant"), pk=pk)
    regel = Sollregel.objects.filter(vertrag=vertrag).first()
    regel_form = _formular(
        request,
        "regel",
        SollregelForm,
        initial={
            "faelligkeit": regel.faelligkeit if regel else "",
            "faelligkeit_tag": regel.faelligkeit_tag if regel else None,
            "soll_ab": regel.soll_ab if regel else None,
        },
    )
    posten_form = _formular(request, "posten", PostenForm)

    if request.method == "POST":
        aktion = request.POST.get("aktion", "")
        try:
            if aktion == "neu_stellen":
                ergebnis = services.vertrag_geaendert(vertrag)
                messages.success(
                    request, f"Soll neu gestellt: {ergebnis.text() if ergebnis else ''}"
                )
                return redirect("soll:vertrag", vertrag.pk)
            if aktion == "regel":
                if regel_form.is_valid():
                    d = regel_form.cleaned_data
                    services.sollregel_setzen(
                        vertrag,
                        faelligkeit=d["faelligkeit"],
                        faelligkeit_tag=d["faelligkeit_tag"],
                        soll_ab=d["soll_ab"],
                        soll_ab_leeren=d["soll_ab"] is None,
                    )
                    messages.success(request, "Gespeichert; das Soll ist neu gestellt.")
                    return redirect("soll:vertrag", vertrag.pk)
            elif aktion == "posten":
                if posten_form.is_valid():
                    d = posten_form.cleaned_data
                    services.sollposten_anlegen(
                        vertrag,
                        d["art"],
                        von=d["von"],
                        netto=d["netto"],
                        steuersatz=d["steuersatz"] or Decimal("0"),
                        faellig_am=d["faellig_am"],
                        bezeichnung=d["bezeichnung"],
                    )
                    messages.success(request, "Posten angelegt.")
                    return redirect("soll:vertrag", vertrag.pk)
            else:
                raise Http404("Unbekannte Aktion")
        except services.SollFehler as fehler:
            messages.error(request, str(fehler))
        else:
            messages.error(request, "Bitte die markierten Felder prüfen.")

    alle = list(Sollposten.objects.filter(vertrag=vertrag).order_by("-von", "art", "-pk"))
    offen = [p for p in alle if p.status in OFFEN]
    return render(
        request,
        "soll/vertrag.html",
        {
            "vertrag": vertrag,
            "einheiten": vertrag.einheiten.all(),
            "offen": sorted(offen, key=lambda p: (p.faellig_am, p.art)),
            "erledigt": [p for p in alle if p.status not in OFFEN],
            "summe_offen": sum((p.offen for p in offen), Decimal("0.00")),
            "faelligkeit": services.faelligkeitsregel(vertrag),
            "soll_ab": services.soll_ab(vertrag),
            "regel_form": regel_form,
            "posten_form": posten_form,
            "storno_form": StornoForm(),
            "heute": services.heute(),
        },
    )


@login_required
@require_POST
def stornieren(request: HttpRequest, pk: int) -> HttpResponse:
    posten = get_object_or_404(Sollposten, pk=pk)
    form = StornoForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Ein Storno braucht einen Grund.")
    else:
        try:
            services.sollposten_stornieren(posten, form.cleaned_data["grund"])
        except services.SollFehler as fehler:
            messages.error(request, str(fehler))
        else:
            messages.success(request, f"Posten #{posten.pk} storniert.")
    return redirect("soll:vertrag", posten.vertrag_id)
