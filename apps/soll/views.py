"""Seiten der Sollstellung: „Offene Posten“ als Gesamtliste mit Filter und je Vertrag.

Gespeichert wird über ``services``; die Seiten prüfen nur die Eingaben.

Neue Gestaltung (Review 2, C; R2-9): Fälligkeit, Posten von Hand und Stornieren sind Dialoge
(``apps.kern.htmx``); nach dem Speichern wird die Seite des Vertrags ersetzt, die Meldung sagt
mit Link, was passiert ist. Ohne JavaScript bleibt der alte Weg (POST, dann Umleitung).
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
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.kern import htmx, zahlen
from apps.kern.templatetags.bausteine import begriff_html
from apps.soll import services
from apps.soll.forms import FilterForm, PostenForm, SollregelForm, StornoForm
from apps.soll.models import Sollposten, Sollregel
from apps.stammdaten.models import Vertrag

#: Höchstens so viele Zeilen zeigt die Gesamtliste (danach filtern).
GRENZE = 500


def _gefiltert(form: FilterForm) -> QuerySet[Sollposten]:
    """Die Posten zum Filter. „offen“ sind genau die Posten aus ``services.offen()`` (Review 1,
    Punkt 1); „überfällig“, „nicht prüfbar“ und „noch nicht fällig“ die drei Teile davon wie
    in ``services.aufteilung()`` (Review 3, Punkt 1)."""
    daten: dict[str, Any] = form.cleaned_data if form.is_valid() else {}
    status = daten.get("status") or "offen"
    if status == "offen":
        qs = services.offen_abfrage()
    elif status in services.TEILE:
        qs = services.teil_abfrage(status)
    else:
        qs = Sollposten.objects.all()
        if status != "alle":
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
            vertrag__in=Vertrag.objects.filter(
                Q(nummer__icontains=suche) | Q(einheiten__nw_id__icontains=suche)
            )
        )
    return qs.select_related("vertrag", "vertrag__mandant").prefetch_related("vertrag__einheiten")


@login_required
def liste(request: HttpRequest) -> HttpResponse:
    form = FilterForm(request.GET or None)
    qs = _gefiltert(form)
    # Anzahl und Summe über alle Treffer des Filters, nicht nur die angezeigten.
    anzahl, summe = services.summen(qs)
    posten = list(qs.order_by(*services.REIHENFOLGE)[:GRENZE])
    return render(
        request,
        "soll/liste.html",
        {
            "form": form,
            "posten": posten,
            "anzahl": anzahl,
            "abgeschnitten": anzahl > len(posten),
            "grenze": GRENZE,
            "summe_offen": summe,
            "heute": services.heute(),
            "teile": _teile(),
        },
    )


def _teile() -> list[dict[str, Any]]:
    """Die drei Teile von „offen“ aller Verträge, wie in den Kacheln des Cockpits."""
    aufteilung = services.aufteilung()
    basis = reverse("soll:liste")
    return [
        {
            "titel": begriff_html(teil),
            "teil": getattr(aufteilung, teil),
            "link": f"{basis}?status={teil}",
            "art": "fehler" if teil == "ueberfaellig" and aufteilung.ueberfaellig.anzahl else "",
        }
        for teil in services.TEILE
    ]


def _formular(request: HttpRequest, aktion: str, klasse: type[forms.Form], **kw: Any) -> Any:
    if request.method == "POST" and request.POST.get("aktion") == aktion:
        return klasse(request.POST, prefix=aktion, **kw)
    return klasse(prefix=aktion, **kw)


#: Dialoge der Seite je Vertrag: Aktion → (Titel, Knopf, Hinweis)
DIALOGE: dict[str, tuple[str, str, str]] = {
    "regel": (
        "Fälligkeit und Beginn",
        "Speichern und neu stellen",
        "Gilt für diesen Vertrag statt des Standards. Offene Posten, die danach anders fällig "
        "sind, werden storniert und neu gestellt.",
    ),
    "posten": (
        "Posten von Hand",
        "Posten anlegen",
        "Kaution oder Sonstiges. Miete und Pauschale entstehen aus den Konditionen.",
    ),
}


def _seite_kontext(vertrag: Vertrag, *, oob: bool = False) -> dict[str, Any]:
    alle = list(Sollposten.objects.filter(vertrag=vertrag).order_by("-von", "art", "-pk"))
    stand = services.offen(vertrag)
    offen_ids = {p.sollposten.pk for p in stand.posten}
    # Offene Posten vor dem Beginn des Abgleichs gehören dem alten System (Vortrag).
    offen = [p for p in alle if p.pk in offen_ids]
    return {
        "vertrag": vertrag,
        "einheiten": vertrag.einheiten.all(),
        "offen": sorted(offen, key=lambda p: (p.faellig_am, p.art)),
        "erledigt": [p for p in alle if p.pk not in offen_ids],
        "summe_offen": stand.summe_offen,
        "vortrag": services.vortrag(vertrag),
        "faelligkeit": services.faelligkeitsregel(vertrag),
        "soll_ab": services.soll_ab(vertrag),
        "heute": services.heute(),
        "basis": reverse("soll:vertrag", args=[vertrag.pk]),
        "oob": oob,
    }


def _dialog(
    request: HttpRequest, vertrag: Vertrag, aktion: str, form: forms.Form, url: str = ""
) -> HttpResponse:
    titel, knopf, hinweis = DIALOGE.get(aktion, ("Posten stornieren", "Stornieren", ""))
    return render(
        request,
        "bausteine/dialog_formular.html",
        {
            "titel": f"{titel} · {vertrag.nummer}",
            "form": form,
            "url": url or reverse("soll:vertrag", args=[vertrag.pk]),
            "aktion": aktion if aktion in DIALOGE else "",
            "knopf": knopf,
            "hinweis": hinweis,
            "gefahr": aktion not in DIALOGE,
        },
    )


def _erfolg(
    request: HttpRequest, vertrag: Vertrag, text: str, anker: str = "offen"
) -> HttpResponse:
    url = reverse("soll:vertrag", args=[vertrag.pk])
    if htmx.ist_htmx(request):
        teil = render_to_string(
            "soll/_vertrag.html", _seite_kontext(vertrag, oob=True), request=request
        )
        return htmx.erfolg(request, [teil], text, link=f"{url}#{anker}", link_text="ansehen")
    messages.success(request, text)
    return redirect(url)


@login_required
def vertrag(request: HttpRequest, pk: int) -> HttpResponse:
    """Offene Posten eines Vertrags; „Soll neu stellen“, Fälligkeit und Posten von Hand
    (``?dialog=regel``, ``?dialog=posten``), Stornieren je Posten (``?dialog=storno&posten=``)."""
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
    posten_form = _formular(request, "posten", PostenForm, initial={"von": services.heute()})
    formulare: dict[str, forms.Form] = {"regel": regel_form, "posten": posten_form}

    if request.method == "GET" and (dialog := request.GET.get("dialog")):
        if dialog == "storno":
            posten = get_object_or_404(
                Sollposten, pk=request.GET.get("posten") or 0, vertrag=vertrag
            )
            return _dialog(
                request,
                vertrag,
                "storno",
                StornoForm(),
                reverse("soll:stornieren", args=[posten.pk]),
            )
        if dialog not in formulare:
            raise Http404("Diese Aktion gibt es für diesen Vertrag nicht.")
        return _dialog(request, vertrag, dialog, formulare[dialog])

    if request.method == "POST":
        aktion = request.POST.get("aktion", "")
        try:
            if aktion == "neu_stellen":
                ergebnis = services.vertrag_geaendert(vertrag)
                zusatz = f": {ergebnis.text()}" if ergebnis else ""
                return _erfolg(request, vertrag, f"Soll für {vertrag.nummer} neu gestellt{zusatz}")
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
                    return _erfolg(
                        request,
                        vertrag,
                        f"Fälligkeit für {vertrag.nummer} gespeichert; das Soll ist neu gestellt",
                    )
            elif aktion == "posten":
                if posten_form.is_valid():
                    d = posten_form.cleaned_data
                    neu = services.sollposten_anlegen(
                        vertrag,
                        d["art"],
                        von=d["von"],
                        netto=d["netto"],
                        steuersatz=d["steuersatz"] or Decimal("0"),
                        faellig_am=d["faellig_am"],
                        bezeichnung=d["bezeichnung"],
                    )
                    return _erfolg(
                        request,
                        vertrag,
                        f"Posten #{neu.pk} ({neu.get_art_display()}, "
                        f"{zahlen.euro(neu.betrag)}) für {vertrag.nummer} angelegt",
                        anker=f"posten-{neu.pk}",
                    )
            else:
                raise Http404("Unbekannte Aktion")
        except services.SollFehler as fehler:
            if htmx.ist_htmx(request) and aktion in formulare:
                formulare[aktion].add_error(None, str(fehler))
                return _dialog(request, vertrag, aktion, formulare[aktion])
            messages.error(request, str(fehler))
        else:
            if htmx.ist_htmx(request) and aktion in formulare:
                return _dialog(request, vertrag, aktion, formulare[aktion])
            messages.error(request, "Bitte die markierten Felder prüfen.")

    offen_formular = request.POST.get("aktion", "") if request.method == "POST" else ""
    return render(
        request,
        "soll/vertrag.html",
        _seite_kontext(vertrag)
        | {
            "offenes_formular": formulare.get(offen_formular),
            "offene_aktion": offen_formular,
            "offener_titel": DIALOGE.get(offen_formular, ("", "", ""))[0],
            "offener_knopf": DIALOGE.get(offen_formular, ("", "", ""))[1],
        },
    )


@login_required
@require_POST
def stornieren(request: HttpRequest, pk: int) -> HttpResponse:
    posten = get_object_or_404(Sollposten.objects.select_related("vertrag"), pk=pk)
    form = StornoForm(request.POST)
    if not form.is_valid():
        if htmx.ist_htmx(request):
            return _dialog(
                request,
                posten.vertrag,
                "storno",
                form,
                reverse("soll:stornieren", args=[posten.pk]),
            )
        messages.error(request, "Ein Storno braucht einen Grund.")
        return redirect("soll:vertrag", posten.vertrag_id)
    try:
        services.sollposten_stornieren(posten, form.cleaned_data["grund"])
    except services.SollFehler as fehler:
        if htmx.ist_htmx(request):
            form.add_error(None, str(fehler))
            return _dialog(
                request,
                posten.vertrag,
                "storno",
                form,
                reverse("soll:stornieren", args=[posten.pk]),
            )
        messages.error(request, str(fehler))
        return redirect("soll:vertrag", posten.vertrag_id)
    return _erfolg(
        request,
        posten.vertrag,
        f"Posten #{posten.pk} ({posten.get_art_display()} {posten.zeitraum_text}) storniert",
        anker="erledigt",
    )
