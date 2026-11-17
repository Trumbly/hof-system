"""Seiten für Vorlagen, Schreiben und Serienmitteilungen.

Gesendet wird nur über die Seite eines Schreibens (oder einer Serie): Sie zeigt das
fertige PDF und trägt dessen Prüfsumme im Formular „Senden“. Ohne diesen Klick eines
angemeldeten Benutzers geht nichts hinaus (CLAUDE.md, Regel 8).
"""

from __future__ import annotations

import uuid
from itertools import groupby

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from apps.dokumente import bezuege, pdf, services, vorlagenarten
from apps.dokumente.forms import AbschlussForm, SerieForm, VorlageForm
from apps.dokumente.models import Schreiben, Vorlage
from apps.dokumente.platzhalter import PlatzhalterFehler
from apps.dokumente.versand import VersandFehler
from apps.kern.models import Benutzer

FEHLER = (services.SchreibenFehler, PlatzhalterFehler, VersandFehler, pdf.PdfNichtVerfuegbar)


def _benutzer(request: HttpRequest) -> Benutzer:
    benutzer = request.user
    if not isinstance(benutzer, Benutzer):  # login_required stellt das sicher
        raise Http404
    return benutzer


def _pdf_antwort(daten: bytes | None, datei: object, dateiname: str) -> HttpResponse:
    if daten is not None:
        antwort = HttpResponse(daten, content_type="application/pdf")
        antwort["Content-Disposition"] = f'inline; filename="{dateiname}"'
        return antwort
    return FileResponse(datei, content_type="application/pdf", filename=dateiname)


# --- Vorlagen ------------------------------------------------------------------------


@login_required
def vorlagen(request: HttpRequest) -> HttpResponse:
    alle = Vorlage.objects.order_by("art", "name", "-version")
    titel = dict(vorlagenarten.auswahl())
    gruppen = [
        (titel.get(art, art), list(eintraege))
        for art, eintraege in groupby(alle, key=lambda v: v.art)
    ]
    return render(request, "dokumente/vorlagen.html", {"gruppen": gruppen})


@login_required
def vorlage_neu(request: HttpRequest) -> HttpResponse:
    form = VorlageForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            vorlage = services.vorlage_anlegen(
                form.cleaned_data["name"],
                form.cleaned_data["art"],
                form.cleaned_data["text"],
                betreff=form.cleaned_data["betreff"],
                herkunft="hand",
            )
        except PlatzhalterFehler as fehler:
            form.add_error("text", str(fehler))
        else:
            messages.success(request, f"Vorlage #{vorlage.pk} als Entwurf angelegt.")
            return redirect("dokumente:vorlage", pk=vorlage.pk)
    return render(request, "dokumente/vorlage_form.html", {"form": form, "vorlage": None})


@login_required
def vorlage_bearbeiten(request: HttpRequest, pk: int) -> HttpResponse:
    vorlage = get_object_or_404(Vorlage, pk=pk)
    if vorlage.status != Vorlage.Status.ENTWURF:
        messages.error(request, "Nur Entwürfe lassen sich bearbeiten; sonst neue Version.")
        return redirect("dokumente:vorlage", pk=pk)
    form = VorlageForm(
        request.POST or None,
        initial={
            "name": vorlage.name,
            "art": vorlage.art,
            "betreff": vorlage.betreff,
            "text": vorlage.text,
        },
    )
    form.fields["name"].disabled = True
    if request.method == "POST" and form.is_valid():
        try:
            services.vorlage_aendern(
                vorlage,
                text=form.cleaned_data["text"],
                betreff=form.cleaned_data["betreff"],
                art=form.cleaned_data["art"],
            )
        except PlatzhalterFehler as fehler:
            form.add_error("text", str(fehler))
        else:
            messages.success(request, f"Vorlage #{vorlage.pk} gespeichert.")
            return redirect("dokumente:vorlage", pk=vorlage.pk)
    return render(request, "dokumente/vorlage_form.html", {"form": form, "vorlage": vorlage})


@login_required
def vorlage(request: HttpRequest, pk: int) -> HttpResponse:
    eintrag = get_object_or_404(Vorlage.objects.select_related("freigegeben_von"), pk=pk)
    art = vorlagenarten.holen(eintrag.art)
    return render(
        request,
        "dokumente/vorlage.html",
        {
            "vorlage": eintrag,
            "vorlagenart": art,
            "platzhalter": sorted(art.erlaubte_platzhalter()),
            "versionen": Vorlage.objects.filter(name=eintrag.name).order_by("-version"),
        },
    )


@login_required
@xframe_options_sameorigin
def vorlage_vorschau(request: HttpRequest, pk: int) -> HttpResponse:
    eintrag = get_object_or_404(Vorlage, pk=pk)
    try:
        daten = services.vorschau_pdf(eintrag)
    except FEHLER as fehler:
        return HttpResponse(
            f"Keine Vorschau: {fehler}", status=409, content_type="text/plain; charset=utf-8"
        )
    return _pdf_antwort(daten, None, f"vorschau_{eintrag.art}_v{eintrag.version}_{eintrag.pk}.pdf")


@login_required
@require_POST
def vorlage_freigeben(request: HttpRequest, pk: int) -> HttpResponse:
    eintrag = get_object_or_404(Vorlage.objects.select_for_update(), pk=pk)
    try:
        services.vorlage_freigeben(eintrag, _benutzer(request))
    except FEHLER as fehler:
        messages.error(request, f"Nicht freigegeben: {fehler}")
    else:
        messages.success(request, f"Vorlage #{eintrag.pk} (Version {eintrag.version}) freigegeben.")
    return redirect("dokumente:vorlage", pk=pk)


@login_required
@require_POST
def vorlage_neue_version(request: HttpRequest, pk: int) -> HttpResponse:
    eintrag = get_object_or_404(Vorlage, pk=pk)
    neu = services.vorlage_neue_version(eintrag)
    messages.success(request, f"Version {neu.version} als Entwurf angelegt.")
    return redirect("dokumente:vorlage_bearbeiten", pk=neu.pk)


# --- Schreiben -----------------------------------------------------------------------


@login_required
def schreiben_liste(request: HttpRequest) -> HttpResponse:
    alle = Schreiben.objects.select_related("bezug_typ")
    return render(
        request,
        "dokumente/schreiben_liste.html",
        {
            "bereit": alle.filter(status=Schreiben.Status.BEREIT).order_by("serie", "pk"),
            "unterwegs": alle.filter(status=Schreiben.Status.GESENDET),
            "erledigt": alle.filter(
                status__in=[Schreiben.Status.ZUGESTELLT, Schreiben.Status.VERWORFEN]
            )[:50],
        },
    )


@login_required
def schreiben(request: HttpRequest, pk: int) -> HttpResponse:
    eintrag = get_object_or_404(
        Schreiben.objects.select_related("vorlage", "bezug_typ", "gesendet_von"), pk=pk
    )
    return render(
        request,
        "dokumente/schreiben.html",
        {
            "schreiben": eintrag,
            "versandweg": vorlagenarten.Versandweg.TITEL.get(eintrag.versandweg),
            "abschluss": AbschlussForm(),
        },
    )


@login_required
@xframe_options_sameorigin
def schreiben_pdf(request: HttpRequest, pk: int) -> HttpResponse:
    eintrag = get_object_or_404(Schreiben, pk=pk)
    if not eintrag.pdf:
        raise Http404
    return _pdf_antwort(None, eintrag.pdf.open("rb"), eintrag.pdf.name.rsplit("/", 1)[-1])


@login_required
@xframe_options_sameorigin
def schreiben_nachweis(request: HttpRequest, pk: int) -> HttpResponse:
    eintrag = get_object_or_404(Schreiben, pk=pk)
    if not eintrag.zustellnachweis:
        raise Http404
    return FileResponse(
        eintrag.zustellnachweis.open("rb"),
        filename=eintrag.zustellnachweis.name.rsplit("/", 1)[-1],
    )


@login_required
@require_POST
def schreiben_senden(request: HttpRequest, pk: int) -> HttpResponse:
    """Aktion „Senden“: nur mit der Prüfsumme des PDFs aus der Vorschau."""
    eintrag = get_object_or_404(Schreiben, pk=pk)
    try:
        services.schreiben_senden(
            eintrag, _benutzer(request), pruefsumme=request.POST.get("pruefsumme", "")
        )
    except FEHLER as fehler:
        messages.error(request, f"Nicht gesendet: {fehler}")
    else:
        messages.success(request, f"Schreiben #{eintrag.pk} gesendet.")
    return redirect("dokumente:schreiben", pk=pk)


@login_required
@require_POST
def schreiben_verwerfen(request: HttpRequest, pk: int) -> HttpResponse:
    eintrag = get_object_or_404(Schreiben, pk=pk)
    try:
        services.schreiben_verwerfen(eintrag)
    except services.SchreibenFehler as fehler:
        messages.error(request, str(fehler))
    else:
        messages.success(request, f"Schreiben #{eintrag.pk} verworfen.")
    return redirect("dokumente:schreiben", pk=pk)


@login_required
@require_POST
def schreiben_abschliessen(request: HttpRequest, pk: int) -> HttpResponse:
    eintrag = get_object_or_404(Schreiben, pk=pk)
    form = AbschlussForm(request.POST, request.FILES)
    if not form.is_valid():
        return HttpResponseBadRequest("Angaben zum Abschluss unvollständig.")
    try:
        services.schreiben_abschliessen(
            eintrag,
            zugestellt_am=form.cleaned_data["zugestellt_am"],
            nachweis=form.cleaned_data["nachweis"],
            vermerk=form.cleaned_data["vermerk"],
        )
    except services.SchreibenFehler as fehler:
        messages.error(request, f"Nicht abgeschlossen: {fehler}")
    else:
        messages.success(request, f"Schreiben #{eintrag.pk} ist erledigt.")
    return redirect("dokumente:schreiben", pk=pk)


@login_required
@require_POST
def schreiben_versandstand(request: HttpRequest, pk: int) -> HttpResponse:
    eintrag = get_object_or_404(Schreiben, pk=pk)
    try:
        geaendert = services.versandstand_abrufen(eintrag)
    except FEHLER as fehler:
        messages.error(request, f"Versandstand nicht abgerufen: {fehler}")
    else:
        messages.success(request, "Versandstand aktualisiert." if geaendert else "Nichts Neues.")
    return redirect("dokumente:schreiben", pk=pk)


# --- Serienmitteilung ----------------------------------------------------------------


@login_required
def serie_neu(request: HttpRequest) -> HttpResponse:
    arten = bezuege.alle()
    schluessel = request.GET.get("bezug") or (arten[0].schluessel if arten else "")
    try:
        bezugsart = bezuege.holen(schluessel)
    except KeyError as fehler:
        raise Http404 from fehler
    objekte = {str(o.pk): o for o in bezugsart.auswahl()}
    form = SerieForm(request.POST or None, wahl=[(pk, str(o)) for pk, o in objekte.items()])
    if request.method == "POST" and form.is_valid():
        gewaehlt = [objekte[pk] for pk in form.cleaned_data["auswahl"]]
        try:
            serie = services.serie_erzeugen(
                form.cleaned_data["vorlage"],
                [(objekt, bezugsart.kontext(objekt)) for objekt in gewaehlt],
                form.cleaned_data["versandweg"] or None,
            )
        except FEHLER as fehler:
            form.add_error(None, str(fehler))
        else:
            messages.success(request, f"{len(serie)} Schreiben erzeugt; bitte prüfen und senden.")
            return redirect("dokumente:serie", serie=serie[0].serie)
    return render(
        request,
        "dokumente/serie_neu.html",
        {"form": form, "arten": arten, "bezugsart": bezugsart},
    )


@login_required
def serie(request: HttpRequest, serie: uuid.UUID) -> HttpResponse:
    alle = list(Schreiben.objects.filter(serie=serie).order_by("pk"))
    if not alle:
        raise Http404
    bereit = [s for s in alle if s.status == Schreiben.Status.BEREIT]
    return render(
        request,
        "dokumente/serie.html",
        {
            "serie": serie,
            "schreiben": alle,
            "bereit": bereit,
            "pruefsumme": services.serie_pruefsumme(bereit) if bereit else "",
        },
    )


@login_required
@require_POST
def serie_senden(request: HttpRequest, serie: uuid.UUID) -> HttpResponse:
    try:
        ergebnis = services.serie_senden(
            serie, _benutzer(request), pruefsumme=request.POST.get("pruefsumme", "")
        )
    except FEHLER as fehler:
        messages.error(request, f"Nicht gesendet: {fehler}")
    else:
        messages.success(request, f"{len(ergebnis.gesendet)} Schreiben gesendet.")
        for pk, grund in ergebnis.fehler.items():
            messages.error(request, f"Schreiben #{pk} nicht gesendet: {grund}")
    return redirect(reverse("dokumente:serie", kwargs={"serie": serie}))
