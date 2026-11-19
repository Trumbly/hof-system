"""Seiten des Mahnwesens: Mahnfälle (Liste und Detail mit Stufenverlauf und Sperren), Stufen
(bearbeiten), Sperren und die ordentliche Kündigung durch den Vermieter (H4.6).

Gespeichert wird über ``services``; die Seiten prüfen nur die Eingaben. Gesendet wird auf
der Seite des Schreibens (App dokumente), nach der Vorschau des PDFs.

Neue Gestaltung (Review 2, C; R2-9): „Sperre setzen“, „Aufheben“ und „Von Hand belegen“ sind
Dialoge (``apps.kern.htmx``); ohne JavaScript bleibt der alte Weg (POST, dann Umleitung).
Meldungen sagen, was passiert ist, mit Link.
"""

from __future__ import annotations

import datetime
from typing import Any

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import format_html
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.dokumente import vorlagenarten
from apps.dokumente.models import Schreiben
from apps.dokumente.vorlagenarten import Versandweg
from apps.kern import htmx
from apps.mahnwesen import services
from apps.mahnwesen.forms import (
    AufhebenForm,
    BelegenForm,
    KuendigungForm,
    SperreForm,
    StufeForm,
)
from apps.mahnwesen.models import Mahnfall, Mahnsperre, Mahnstufe
from apps.stammdaten.models import Vertrag


@login_required
@require_GET
def faelle(request: HttpRequest) -> HttpResponse:
    """Mahnfälle: offene und gesperrte, darunter die zuletzt erledigten."""
    alle = Mahnfall.objects.select_related("vertrag", "stufe").prefetch_related(
        "vertrag__einheiten"
    )
    return render(
        request,
        "mahnwesen/faelle.html",
        {
            "offen": alle.filter(status=Mahnfall.Status.OFFEN).order_by("vertrag__nummer"),
            "gesperrt": alle.filter(status=Mahnfall.Status.GESPERRT).order_by("vertrag__nummer"),
            "erledigt": alle.filter(status=Mahnfall.Status.ERLEDIGT).order_by(
                "-geschlossen_am", "-pk"
            )[:30],
            "kachel": services.kachel_mahnfaelle(),
        },
    )


class BelegenDialogForm(BelegenForm):
    schritt = forms.IntegerField(widget=forms.HiddenInput)


def _fall_kontext(eintrag: Mahnfall, *, oob: bool = False) -> dict[str, Any]:
    schritte = list(
        eintrag.schritte.select_related("stufe", "schreiben", "arbeitsposten").order_by(
            "stichtag", "pk"
        )
    )
    return {
        "fall": eintrag,
        "posten": eintrag.offene_posten.order_by("faellig_am", "von", "pk"),
        "schritte": schritte,
        "sperren": eintrag.vertrag.mahnsperren.order_by("-von", "-pk"),
        "kuendigungen": Schreiben.objects.filter(
            bezug_typ__app_label="stammdaten",
            bezug_typ__model="vertrag",
            bezug_id=eintrag.vertrag_id,
            art="kuendigung",
        ),
        "anwalt_hinweis": vorlagenarten.ANWALT_HINWEIS,
        "versandwege": Versandweg.TITEL,
        "oob": oob,
    }


def _belegen_dialog(
    request: HttpRequest, eintrag: Mahnfall, schritt: Any, form: forms.Form
) -> HttpResponse:
    return render(
        request,
        "bausteine/dialog_formular.html",
        {
            "titel": f"{schritt.stufe.titel} von Hand belegen · {eintrag.vertrag.nummer}",
            "form": form,
            "url": reverse("mahnwesen:fall", args=[eintrag.pk]),
            "hinweis": "Probebetrieb: Der Schritt gilt als belegt (gesendet oder zugestellt), "
            "ohne dass das Hof-System ein Schreiben verschickt.",
            "knopf": "Belegt",
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def fall(request: HttpRequest, pk: int) -> HttpResponse:
    """Ein Mahnfall mit offenen Posten, Stufenverlauf und Sperren; „Von Hand belegen“ (POST)
    belegt einen Schritt ohne Schreiben (Probebetrieb), im Dialog oder ohne JavaScript."""
    eintrag = get_object_or_404(
        Mahnfall.objects.select_related("vertrag", "stufe").prefetch_related("vertrag__einheiten"),
        pk=pk,
    )
    if request.method == "GET" and request.GET.get("dialog") == "belegen":
        schritt = get_object_or_404(eintrag.schritte, pk=request.GET.get("schritt") or 0)
        form = BelegenDialogForm(initial={"am": services.heute(), "schritt": schritt.pk})
        return _belegen_dialog(request, eintrag, schritt, form)
    if request.method == "POST":
        schritt = get_object_or_404(eintrag.schritte, pk=request.POST.get("schritt") or 0)
        belegen = BelegenDialogForm(request.POST)
        if belegen.is_valid():
            try:
                services.schritt_von_hand_belegen(
                    schritt,
                    am=belegen.cleaned_data["am"],
                    vermerk=belegen.cleaned_data["vermerk"],
                )
            except services.MahnwesenFehler as fehler:
                belegen.add_error(None, str(fehler))
            else:
                text = (
                    f"{schritt.stufe.titel} für {eintrag.vertrag.nummer} am "
                    f"{belegen.cleaned_data['am']:%d.%m.%Y} belegt"
                )
                link = f"{reverse('mahnwesen:fall', args=[eintrag.pk])}#schritt-{schritt.pk}"
                if htmx.ist_htmx(request):
                    eintrag.refresh_from_db()
                    teile = [
                        render_to_string(
                            "mahnwesen/_fall_kopf.html",
                            _fall_kontext(eintrag, oob=True),
                            request=request,
                        ),
                        render_to_string(
                            "mahnwesen/_stufenverlauf.html",
                            _fall_kontext(eintrag, oob=True),
                            request=request,
                        ),
                    ]
                    return htmx.erfolg(request, teile, text, link=link)
                messages.success(request, text)
                return redirect("mahnwesen:fall", pk=eintrag.pk)
        if htmx.ist_htmx(request):
            return _belegen_dialog(request, eintrag, schritt, belegen)
        fehler = belegen.non_field_errors() or ["Bitte das Datum angeben."]
        messages.error(request, " ".join(fehler))
        return redirect("mahnwesen:fall", pk=eintrag.pk)
    return render(request, "mahnwesen/fall.html", _fall_kontext(eintrag))


@login_required
@require_GET
def stufen(request: HttpRequest) -> HttpResponse:
    """Die Stufenfolge je Vertragsart."""
    arten = dict(Vertrag.Art.choices)
    gruppen: dict[str, list[Mahnstufe]] = {}
    for stufe in Mahnstufe.objects.order_by("vertragsart", "reihenfolge"):
        gruppen.setdefault(stufe.vertragsart, []).append(stufe)
    return render(
        request,
        "mahnwesen/stufen.html",
        {
            "stufengruppen": [(art, arten.get(art, art), liste) for art, liste in gruppen.items()],
            "versandwege": Versandweg.TITEL,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def stufe(request: HttpRequest, pk: int) -> HttpResponse:
    eintrag = get_object_or_404(Mahnstufe, pk=pk)
    anfang = {feld: getattr(eintrag, feld) for feld in services.STUFE_FELDER}
    form = StufeForm(request.POST or None, initial=anfang)
    if request.method == "POST" and form.is_valid():
        try:
            services.stufe_aendern(eintrag, **form.cleaned_data)
        except (services.MahnwesenFehler, ValidationError) as fehler:
            form.add_error(None, " ".join(getattr(fehler, "messages", [str(fehler)])))
        else:
            messages.success(
                request,
                format_html(
                    'Stufe „{}“ ({}) gespeichert · <a href="{}">Stufen ansehen</a>',
                    eintrag.titel,
                    eintrag.get_vertragsart_display(),
                    f"{reverse('mahnwesen:stufen')}#art-{eintrag.vertragsart}",
                ),
            )
            return redirect(f"{reverse('mahnwesen:stufen')}#art-{eintrag.vertragsart}")
    return render(request, "mahnwesen/stufe.html", {"stufe": eintrag, "form": form})


def _sperren_kontext(*, oob: bool = False) -> dict[str, Any]:
    heute = services.heute()
    gueltig = (
        services.gueltige_sperren(heute)
        .select_related("vertrag")
        .prefetch_related("vertrag__einheiten")
        .order_by("vertrag__nummer", "von")
    )
    spaeter = (
        Mahnsperre.objects.filter(von__gt=heute, aufgehoben_am__isnull=True)
        .select_related("vertrag")
        .order_by("von", "pk")
    )
    return {"gueltig": gueltig, "spaeter": spaeter, "oob": oob}


def _sperre_text(sperre: Mahnsperre) -> str:
    text = f"Mahnsperre für {sperre.vertrag.nummer} gesetzt ({sperre.get_grund_display()}"
    text += f", ab {sperre.von:%d.%m.%Y}"
    return text + (f" bis {sperre.bis:%d.%m.%Y})" if sperre.bis else ")")


def _dialog(
    request: HttpRequest, titel: str, form: forms.Form, url: str, knopf: str
) -> HttpResponse:
    return render(
        request,
        "bausteine/dialog_formular.html",
        {"titel": titel, "form": form, "url": url, "knopf": knopf},
    )


@login_required
@require_http_methods(["GET", "POST"])
def sperren(request: HttpRequest) -> HttpResponse:
    """Gültige Sperren; „Sperre setzen“ (Rechtsfall, Ratenplan, Hand) und „Aufheben“ als
    Dialoge (``?dialog=sperre``, ``?dialog=aufheben&sperre=<ID>``)."""
    heute = services.heute()
    form = SperreForm(request.POST or None, initial={"von": heute})
    url = reverse("mahnwesen:sperren")
    if request.method == "GET":
        if request.GET.get("dialog") == "sperre":
            return _dialog(request, "Sperre setzen", form, url, "Sperre setzen")
        if request.GET.get("dialog") == "aufheben":
            sperre = get_object_or_404(Mahnsperre, pk=request.GET.get("sperre") or 0)
            return _dialog(
                request,
                f"Sperre für {sperre.vertrag.nummer} aufheben",
                AufhebenForm(initial={"am": heute}),
                reverse("mahnwesen:sperre_aufheben", args=[sperre.pk]),
                "Aufheben",
            )
    if request.method == "POST" and form.is_valid():
        daten = form.cleaned_data
        try:
            sperre = services.sperre_setzen(
                daten["vertrag"],
                daten["grund"],
                von=daten["von"],
                bis=daten["bis"],
                hinweis=daten["hinweis"],
            )
        except services.MahnwesenFehler as fehler:
            form.add_error(None, str(fehler))
        else:
            text = _sperre_text(sperre)
            link = f"{url}#sperre-{sperre.pk}"
            if htmx.ist_htmx(request):
                teil = render_to_string(
                    "mahnwesen/_sperren.html", _sperren_kontext(oob=True), request=request
                )
                return htmx.erfolg(request, [teil], text, link=link)
            messages.success(request, format_html('{} · <a href="{}">ansehen</a>', text, link))
            return redirect(link)
    if request.method == "POST" and htmx.ist_htmx(request):
        return _dialog(request, "Sperre setzen", form, url, "Sperre setzen")
    return render(
        request,
        "mahnwesen/sperren.html",
        _sperren_kontext() | {"form": form, "formular_offen": request.method == "POST"},
    )


@login_required
@require_POST
def sperre_aufheben(request: HttpRequest, pk: int) -> HttpResponse:
    sperre = get_object_or_404(Mahnsperre.objects.select_related("vertrag"), pk=pk)
    form = AufhebenForm(request.POST)
    if form.is_valid():
        try:
            services.sperre_aufheben(sperre, form.cleaned_data["am"])
        except (services.MahnwesenFehler, ValidationError) as fehler:
            form.add_error(None, " ".join(getattr(fehler, "messages", [str(fehler)])))
        else:
            text = (
                f"Mahnsperre für {sperre.vertrag.nummer} zum "
                f"{form.cleaned_data['am']:%d.%m.%Y} aufgehoben"
            )
            if htmx.ist_htmx(request):
                teil = render_to_string(
                    "mahnwesen/_sperren.html", _sperren_kontext(oob=True), request=request
                )
                return htmx.erfolg(
                    request,
                    [teil],
                    text,
                    link=reverse("mahnwesen:faelle"),
                    link_text="Mahnfälle ansehen",
                )
            messages.success(request, text)
            return redirect("mahnwesen:sperren")
    if htmx.ist_htmx(request):
        return _dialog(
            request,
            f"Sperre für {sperre.vertrag.nummer} aufheben",
            form,
            reverse("mahnwesen:sperre_aufheben", args=[sperre.pk]),
            "Aufheben",
        )
    fehler = form.non_field_errors() or ["Bitte das Datum angeben."]
    messages.error(request, " ".join(fehler))
    return redirect("mahnwesen:sperren")


@login_required
@require_http_methods(["GET", "POST"])
def kuendigen(request: HttpRequest, pk: int) -> HttpResponse:
    """Ordentliche Kündigung durch den Vermieter (H4.6): Fristende je Vertragsart, Schreiben
    aus der Vorlage. Gesendet wird nach der Vorschau und der Bestätigung des Anwalts."""
    vertrag = get_object_or_404(Vertrag.objects.prefetch_related("einheiten"), pk=pk)
    vorschlag = services.heute() + datetime.timedelta(days=services.ZUGANG_TAGE)
    daten = request.POST if request.method == "POST" else (request.GET or None)
    form = KuendigungForm(daten, initial={"zugang_am": vorschlag})
    zugang = form.cleaned_data["zugang_am"] if form.is_valid() else vorschlag
    if request.method == "POST" and form.is_valid():
        try:
            schreiben = services.ordentliche_kuendigung_erzeugen(vertrag, zugang)
        except services.MahnwesenFehler as fehler:
            messages.error(request, str(fehler))
        else:
            messages.success(
                request,
                f"Kündigung {vertrag.nummer} als Schreiben #{schreiben.pk} bereit "
                f"(Fristende {services.kuendigungsfrist(vertrag, zugang).fristende:%d.%m.%Y}): "
                f"{vorlagenarten.ANWALT_HINWEIS}.",
            )
            return redirect("dokumente:schreiben", pk=schreiben.pk)
    return render(
        request,
        "mahnwesen/kuendigen.html",
        {
            "vertrag": vertrag,
            "form": form,
            "frist": services.kuendigungsfrist(vertrag, zugang),
            "anwalt_hinweis": vorlagenarten.ANWALT_HINWEIS,
        },
    )
