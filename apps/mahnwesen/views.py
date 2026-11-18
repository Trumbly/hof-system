"""Seiten des Mahnwesens: Mahnfälle (Liste und Detail mit Stufenverlauf und Sperren), Stufen
(bearbeiten), Sperren und die ordentliche Kündigung durch den Vermieter (H4.6).

Gespeichert wird über ``services``; die Seiten prüfen nur die Eingaben. Gesendet wird auf
der Seite des Schreibens (App dokumente), nach der Vorschau des PDFs.
"""

from __future__ import annotations

import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.dokumente import vorlagenarten
from apps.dokumente.models import Schreiben
from apps.dokumente.vorlagenarten import Versandweg
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


@login_required
@require_http_methods(["GET", "POST"])
def fall(request: HttpRequest, pk: int) -> HttpResponse:
    """Ein Mahnfall mit offenen Posten, Stufenverlauf und Sperren; POST belegt einen Schritt
    ohne Schreiben von Hand (Probebetrieb)."""
    eintrag = get_object_or_404(
        Mahnfall.objects.select_related("vertrag", "stufe").prefetch_related("vertrag__einheiten"),
        pk=pk,
    )
    belegen = BelegenForm(request.POST or None, initial={"am": services.heute()})
    if request.method == "POST":
        schritt = get_object_or_404(eintrag.schritte, pk=request.POST.get("schritt"))
        if belegen.is_valid():
            try:
                services.schritt_von_hand_belegen(
                    schritt,
                    am=belegen.cleaned_data["am"],
                    vermerk=belegen.cleaned_data["vermerk"],
                )
            except services.MahnwesenFehler as fehler:
                messages.error(request, str(fehler))
            else:
                messages.success(request, f"Mahnschritt #{schritt.pk} ist belegt.")
            return redirect("mahnwesen:fall", pk=eintrag.pk)
    schritte = list(
        eintrag.schritte.select_related("stufe", "schreiben", "arbeitsposten").order_by(
            "stichtag", "pk"
        )
    )
    return render(
        request,
        "mahnwesen/fall.html",
        {
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
            "belegen": belegen,
            "anwalt_hinweis": vorlagenarten.ANWALT_HINWEIS,
            "versandwege": Versandweg.TITEL,
        },
    )


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
            "gruppen": [(art, arten.get(art, art), liste) for art, liste in gruppen.items()],
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
                request, f"Stufe „{eintrag.titel}“ ({eintrag.vertragsart}) gespeichert."
            )
            return redirect("mahnwesen:stufen")
    return render(request, "mahnwesen/stufe.html", {"stufe": eintrag, "form": form})


@login_required
@require_http_methods(["GET", "POST"])
def sperren(request: HttpRequest) -> HttpResponse:
    """Gültige Sperren und eine neue Sperre (Rechtsfall, Ratenplan, Hand)."""
    heute = services.heute()
    form = SperreForm(request.POST or None, initial={"von": heute})
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
            messages.success(request, f"Mahnsperre #{sperre.pk} gesetzt.")
            return redirect("mahnwesen:sperren")
    gueltig = (
        services.gueltige_sperren(heute)
        .select_related("vertrag")
        .prefetch_related("vertrag__einheiten")
        .order_by("vertrag__nummer", "von")
    )
    spaeter = Mahnsperre.objects.filter(von__gt=heute, aufgehoben_am__isnull=True).select_related(
        "vertrag"
    )
    return render(
        request,
        "mahnwesen/sperren.html",
        {
            "form": form,
            "gueltig": gueltig,
            "spaeter": spaeter,
            "aufheben": AufhebenForm(initial={"am": heute}),
        },
    )


@login_required
@require_POST
def sperre_aufheben(request: HttpRequest, pk: int) -> HttpResponse:
    sperre = get_object_or_404(Mahnsperre, pk=pk)
    form = AufhebenForm(request.POST)
    if form.is_valid():
        try:
            services.sperre_aufheben(sperre, form.cleaned_data["am"])
        except (services.MahnwesenFehler, ValidationError) as fehler:
            messages.error(request, " ".join(getattr(fehler, "messages", [str(fehler)])))
        else:
            messages.success(request, f"Mahnsperre #{sperre.pk} aufgehoben.")
    else:
        messages.error(request, "Datum fehlt.")
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
                f"Kündigung als Schreiben #{schreiben.pk} bereit: {vorlagenarten.ANWALT_HINWEIS}.",
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
