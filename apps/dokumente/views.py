"""Seiten für Ablage und Belege, Vorlagen, Schreiben und Serienmitteilungen.

Gesendet wird nur über die Seite eines Schreibens (oder einer Serie): Sie zeigt das
fertige PDF und trägt dessen Prüfsumme im Formular „Senden“. Ohne diesen Klick eines
angemeldeten Benutzers geht nichts hinaus (CLAUDE.md, Regel 8).
"""

from __future__ import annotations

import hashlib
import uuid
from itertools import groupby
from typing import Any

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from apps.bank.models import Bankumsatz
from apps.dokumente import (
    belegmappe,
    bezuege,
    dokumentarten,
    pdf,
    services,
    umwandeln,
    vorlagenarten,
)
from apps.dokumente.forms import (
    AbschlussForm,
    BelegBestaetigenForm,
    BelegFelder,
    BelegHochladenForm,
    BelegmappeForm,
    BelegteilFormset,
    BelegUmwidmenForm,
    LieferantForm,
    SerieForm,
    VorlageForm,
)
from apps.dokumente.models import (
    Beleg,
    Dokument,
    Eingangsrechnung,
    Lieferant,
    Schreiben,
    Vorlage,
)
from apps.dokumente.platzhalter import PlatzhalterFehler
from apps.dokumente.versand import MailserverNichtErreichbar, VersandFehler
from apps.dokumente.vorlagenarten import Versandweg
from apps.fristen import services as fristen_services
from apps.kassenbuch.models import Buchung
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
            "anwalt": services.braucht_anwalt(eintrag),
            "anwalt_hinweis": vorlagenarten.ANWALT_HINWEIS,
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
    """Aktion „Senden“: nur mit der Prüfsumme des PDFs aus der Vorschau; bei Kündigungen
    zusätzlich mit dem Haken „Der Anwalt hat das Schreiben gesehen“."""
    eintrag = get_object_or_404(Schreiben, pk=pk)
    try:
        services.schreiben_senden(
            eintrag,
            _benutzer(request),
            pruefsumme=request.POST.get("pruefsumme", ""),
            anwalt_gesehen=request.POST.get("anwalt_gesehen") == "1",
        )
    except MailserverNichtErreichbar as fehler:
        messages.error(request, str(fehler))
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
    """Serienmitteilung in zwei Schritten (Review 2, A8): erst die Empfängervorschau mit
    Einheit, Name und Versandweg (je Zeile umstellbar), dann „Schreiben erzeugen“. Wer
    nichts bekommt, steht namentlich mit Grund daneben; die Serie bricht nicht ab."""
    arten = bezuege.alle()
    schluessel = request.GET.get("bezug") or (arten[0].schluessel if arten else "")
    try:
        bezugsart = bezuege.holen(schluessel)
    except KeyError as fehler:
        raise Http404 from fehler
    objekte = {str(o.pk): o for o in bezugsart.auswahl()}
    form = SerieForm(
        request.POST or None,
        wahl=[(pk, bezugsart.bezeichnung(o)) for pk, o in objekte.items()],
    )
    plan: list[services.Serienempfaenger] | None = None
    marke = ""
    if request.method == "POST" and form.is_valid():
        daten = form.cleaned_data
        vorlage = daten["vorlage"]
        auswahl = sorted(daten["auswahl"], key=int)
        marke = _serie_marke(vorlage.pk, auswahl, daten["versandweg"], daten["ohne_mail"])
        try:
            plan = services.serie_planen(
                vorlage,
                [(objekte[pk], bezugsart.kontext(objekte[pk])) for pk in auswahl],
                daten["versandweg"] or None,
                ohne_mail=daten["ohne_mail"],
            )
        except FEHLER as fehler:
            form.add_error(None, str(fehler))
        if plan is not None and request.POST.get("schritt") == "erzeugen":
            if request.POST.get("marke") != marke:
                messages.warning(
                    request,
                    "Die Auswahl hat sich seit der Vorschau geändert. "
                    "Bitte die Empfänger noch einmal prüfen.",
                )
            else:
                plan = [_serie_umstellen(request, z) for z in plan]
                antwort = _serie_erzeugen(request, vorlage, plan)
                if antwort is not None:
                    return antwort
    return render(
        request,
        "dokumente/serie_neu.html",
        {
            "form": form,
            "arten": arten,
            "bezugsart": bezugsart,
            "plan": plan,
            "marke": marke,
            "wege": [*Versandweg.TITEL.items(), (services.AUSLASSEN, "auslassen")],
            "erzeugt": sum(1 for z in plan or [] if not z.ausgelassen),
        },
    )


def _serie_marke(vorlage: int, auswahl: list[str], versandweg: str, ohne_mail: str) -> str:
    """Kennung der gezeigten Vorschau: Erzeugt wird nur, was die Vorschau gezeigt hat."""
    roh = f"{vorlage}|{','.join(auswahl)}|{versandweg}|{ohne_mail}"
    return hashlib.sha256(roh.encode()).hexdigest()[:16]


def _serie_umstellen(
    request: HttpRequest, zeile: services.Serienempfaenger
) -> services.Serienempfaenger:
    """Übernimmt den in der Vorschau gewählten Versandweg einer Zeile. Fehlt dafür etwas,
    wird die Zeile ausgelassen (mit Grund), statt still umgestellt."""
    weg = request.POST.get(f"weg_{zeile.bezug.pk}", "")
    if not weg or weg == zeile.versandweg:
        return zeile
    if weg != services.AUSLASSEN and weg not in Versandweg.TITEL:
        return zeile
    return services.serienempfaenger(zeile.bezug, zeile.kontext, weg, ohne_mail=services.AUSLASSEN)


def _serie_erzeugen(
    request: HttpRequest, vorlage: Vorlage, plan: list[services.Serienempfaenger]
) -> HttpResponse | None:
    try:
        serie = services.serie_aus_plan(vorlage, plan)
    except FEHLER as fehler:
        messages.error(request, f"Keine Schreiben erzeugt: {fehler}")
        return None
    for zeile in serie.ausgelassen:
        wer = " · ".join(t for t in (zeile.einheit, zeile.name) if t) or f"Bezug #{zeile.bezug.pk}"
        messages.warning(request, f"Ausgelassen: {wer}. {zeile.hinweis}")
    if not serie.schreiben:
        messages.error(request, "Keine Schreiben erzeugt: Alle Empfänger sind ausgelassen.")
        return None
    briefe = sum(1 for s in serie.schreiben if s.versandweg != Versandweg.MAIL)
    teile = [f"{len(serie.schreiben)} Schreiben erzeugt und bereit"]
    if briefe:
        teile.append(f"davon {briefe} nicht per Mail")
    if serie.ausgelassen:
        teile.append(f"{len(serie.ausgelassen)} ausgelassen")
    messages.success(request, " · ".join(teile) + ". Bitte prüfen und senden.")
    return redirect("dokumente:serie", serie=serie.kennung)


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


# --- Ablage und Belege (Schritt 3a) --------------------------------------------------

ABLAGE_FEHLER = (services.AblageFehler, umwandeln.DateiUngueltig)
#: Felder des Belegs in der Reihenfolge der Seite (auch für den Vorschlag per HTMX).
BELEG_FELDER = (
    "lieferant",
    "lieferant_neu",
    "datum",
    "betrag",
    "objekt",
    "kategorie",
    "leistungsjahr",
    "umlagefaehiger_anteil",
    "faellig_am",
    "rechnungsnummer",
)
ABLAGE_ANZAHL = 100


def _vorschlag_per_htmx(form: forms.Form) -> None:
    """Die Wahl des Lieferanten holt den Vorschlag aus früheren Belegen (HTMX)."""
    form.fields["lieferant"].widget.attrs.update(
        {
            "hx-get": reverse("dokumente:beleg_vorschlag"),
            "hx-trigger": "change",
            "hx-target": "#beleg-felder",
            "hx-include": "closest form",
            "hx-swap": "outerHTML",
        }
    )


def _lieferant_aus(daten: dict[str, Any]) -> Lieferant | None:
    """Gewählter Lieferant oder ein neuer aus „oder neuer Lieferant“."""
    neu = (daten.get("lieferant_neu") or "").strip()
    if neu:
        return services.lieferant_anlegen(neu)
    lieferant: Lieferant | None = daten.get("lieferant")
    return lieferant


@login_required
def ablage(request: HttpRequest) -> HttpResponse:
    """Offene Belege und die zuletzt abgelegten Dokumente."""
    art = request.GET.get("art", "")
    dokumente = Dokument.objects.order_by("-eingegangen_am", "-pk")
    if art in dokumentarten.DOKUMENTARTEN:
        dokumente = dokumente.filter(art=art)
    offen = (
        Beleg.objects.filter(status=Beleg.Status.OFFEN)
        .select_related("dokument", "lieferant")
        .order_by("dokument__datum", "pk")
    )
    return render(
        request,
        "dokumente/ablage.html",
        {
            "offen": offen,
            "dokumente": dokumente[:ABLAGE_ANZAHL],
            "arten": dokumentarten.auswahl(),
            "art": art,
        },
    )


@login_required
def dokument(request: HttpRequest, pk: int) -> HttpResponse:
    eintrag = get_object_or_404(Dokument, pk=pk)
    bezuege = [
        (b.bezug_typ.name, b.bezug_id, verweise_url(b.bezug))
        for b in eintrag.bezuege.select_related("bezug_typ").order_by("pk")
    ]
    return render(
        request,
        "dokumente/dokument.html",
        {
            "dokument": eintrag,
            "bezuege": bezuege,
            "belege": eintrag.belege.select_related("lieferant", "objekt", "kategorie"),
        },
    )


def verweise_url(bezug: Any) -> str:
    """Link zu einem Bezug, wenn seine App eine Seite angemeldet hat."""
    if bezug is None:
        return ""
    if isinstance(bezug, Beleg):
        return reverse("dokumente:beleg", args=[bezug.pk])
    if isinstance(bezug, Lieferant):
        return reverse("dokumente:lieferant", args=[bezug.pk])
    if isinstance(bezug, Buchung):
        return reverse("kassenbuch:umsatz", args=[bezug.umsatz_id])
    return ""


@login_required
@xframe_options_sameorigin
def dokument_pdf(request: HttpRequest, pk: int) -> HttpResponse:
    eintrag = get_object_or_404(Dokument, pk=pk)
    if not eintrag.datei:
        raise Http404
    return _pdf_antwort(None, eintrag.datei.open("rb"), eintrag.dateiname)


@login_required
def dokument_original(request: HttpRequest, pk: int) -> HttpResponse:
    eintrag = get_object_or_404(Dokument, pk=pk)
    if not eintrag.original:
        raise Http404
    return FileResponse(
        eintrag.original.open("rb"),
        content_type=eintrag.dateityp,
        filename=eintrag.original.name.rsplit("/", 1)[-1],
    )


@login_required
def beleg_hochladen(request: HttpRequest) -> HttpResponse:
    """Upload am Handy (H6.2): Foto oder PDF, die Felder von Hand."""
    form = BelegHochladenForm(request.POST or None, request.FILES or None)
    _vorschlag_per_htmx(form)
    if request.method == "POST" and form.is_valid():
        daten = form.cleaned_data
        inhalt = daten["datei"].read()
        try:
            with transaction.atomic():
                lieferant = _lieferant_aus(daten)
                if daten["art"] in dokumentarten.AM_LIEFERANTEN:
                    assert lieferant is not None  # noqa: S101  (prüft das Formular)
                    ablage_ = services.lieferantendokument_ablegen(
                        inhalt,
                        art=daten["art"],
                        lieferant=lieferant,
                        datum=daten["datum"],
                        bemerkung=daten["bemerkung"],
                    )
                    messages.success(request, f"Dokument #{ablage_.dokument.pk} abgelegt.")
                    return redirect("dokumente:dokument", pk=ablage_.dokument.pk)
                ergebnis = services.beleg_hochladen(
                    inhalt,
                    datum=daten["datum"],
                    lieferant=lieferant,
                    betrag=daten["betrag"],
                    objekt=daten["objekt"],
                    kategorie=daten["kategorie"],
                    leistungsjahr=daten["leistungsjahr"],
                    umlagefaehiger_anteil=daten["umlagefaehiger_anteil"],
                    bemerkung=daten["bemerkung"],
                    faellig_am=daten["faellig_am"],
                    rechnungsnummer=daten["rechnungsnummer"],
                )
        except ABLAGE_FEHLER as fehler:
            form.add_error(None, str(fehler))
        else:
            if not ergebnis.neu:
                messages.warning(
                    request,
                    f"Diese Datei liegt schon in der Ablage (Dokument #{ergebnis.dokument.pk}).",
                )
                return redirect("dokumente:dokument", pk=ergebnis.dokument.pk)
            assert ergebnis.beleg is not None  # noqa: S101
            if ergebnis.beleg.status == Beleg.Status.BESTAETIGT:
                messages.success(request, f"Beleg #{ergebnis.beleg.pk} gespeichert.")
            else:
                messages.warning(
                    request, f"Beleg #{ergebnis.beleg.pk} abgelegt; es fehlen noch Felder."
                )
            return redirect("dokumente:beleg", pk=ergebnis.beleg.pk)
    return render(request, "dokumente/beleg_hochladen.html", {"form": form, "felder": BELEG_FELDER})


@login_required
def beleg_vorschlag(request: HttpRequest) -> HttpResponse:
    """HTMX: die Felder des Belegs, leere Felder mit dem Vorschlag des Lieferanten gefüllt."""
    eingabe = BelegFelder(request.GET)
    eingabe.is_valid()
    werte = {k: v for k, v in eingabe.cleaned_data.items() if v not in (None, "")}
    lieferant = werte.get("lieferant")
    treffer = services.vorschlag(lieferant, werte.get("datum") or timezone.localdate())
    if treffer is not None:
        werte.setdefault("objekt", treffer.objekt)
        werte.setdefault("kategorie", treffer.kategorie)
        werte.setdefault("leistungsjahr", treffer.leistungsjahr)
        werte.setdefault("umlagefaehiger_anteil", treffer.umlagefaehiger_anteil)
    form = BelegFelder(initial=werte)
    _vorschlag_per_htmx(form)
    return render(
        request,
        "dokumente/_beleg_felder.html",
        {"form": form, "felder": BELEG_FELDER, "vorschlag": treffer},
    )


def _rechnung_zu(eintrag: Beleg) -> Eingangsrechnung | None:
    return Eingangsrechnung.objects.filter(beleg__dokument=eintrag.dokument_id).first()


def _beleg_initial(eintrag: Beleg) -> dict[str, Any]:
    rechnung = _rechnung_zu(eintrag)
    return {
        "lieferant": eintrag.lieferant,
        "datum": eintrag.dokument.datum,
        "betrag": eintrag.betrag,
        "objekt": eintrag.objekt,
        "kategorie": eintrag.kategorie,
        "leistungsjahr": eintrag.leistungsjahr,
        "umlagefaehiger_anteil": eintrag.umlagefaehiger_anteil,
        "faellig_am": rechnung.faellig_am if rechnung else None,
        "rechnungsnummer": rechnung.rechnungsnummer if rechnung else "",
    }


@login_required
def beleg(request: HttpRequest, pk: int) -> HttpResponse:
    """Seite eines Belegs: Felder bestätigen, Buchung zuordnen, aufteilen, umwidmen."""
    eintrag = get_object_or_404(
        Beleg.objects.select_related("dokument", "lieferant", "objekt", "kategorie", "buchung"),
        pk=pk,
    )
    form = BelegBestaetigenForm(request.POST or None, initial=_beleg_initial(eintrag))
    _vorschlag_per_htmx(form)
    if request.method == "POST" and form.is_valid():
        daten = form.cleaned_data
        try:
            with transaction.atomic():
                services.beleg_bestaetigen(
                    eintrag,
                    lieferant=_lieferant_aus(daten),
                    betrag=daten["betrag"],
                    objekt=daten["objekt"],
                    kategorie=daten["kategorie"],
                    leistungsjahr=daten["leistungsjahr"],
                    umlagefaehiger_anteil=daten["umlagefaehiger_anteil"],
                    datum=daten["datum"],
                    faellig_am=daten["faellig_am"],
                    rechnungsnummer=daten["rechnungsnummer"],
                )
        except ABLAGE_FEHLER as fehler:
            form.add_error(None, str(fehler))
        else:
            messages.success(request, f"Beleg #{eintrag.pk} bestätigt.")
            return redirect("dokumente:beleg", pk=eintrag.pk)
    kandidaten = (
        services.buchung_kandidaten(eintrag)
        if eintrag.status == Beleg.Status.BESTAETIGT and eintrag.buchung_id is None
        else []
    )
    return render(
        request,
        "dokumente/beleg.html",
        {
            "beleg": eintrag,
            "form": form,
            "felder": BELEG_FELDER,
            "kandidaten": kandidaten,
            "geschwister": eintrag.dokument.belege.exclude(pk=eintrag.pk).order_by("pk"),
            "umwidmen": BelegUmwidmenForm(initial={"lieferant": eintrag.lieferant}),
            "vorschlag": services.vorschlag(eintrag.lieferant, eintrag.dokument.datum),
            "rechnung": _rechnung_zu(eintrag),
        },
    )


@login_required
@require_POST
def beleg_buchung(request: HttpRequest, pk: int) -> HttpResponse:
    eintrag = get_object_or_404(Beleg, pk=pk)
    buchung = get_object_or_404(Buchung, pk=request.POST.get("buchung") or 0)
    try:
        services.beleg_an_buchung(eintrag, buchung)
    except ABLAGE_FEHLER as fehler:
        messages.error(request, str(fehler))
    else:
        messages.success(request, f"Beleg #{eintrag.pk} hängt an Buchung #{buchung.pk}.")
    return redirect("dokumente:beleg", pk=pk)


@login_required
@require_POST
def beleg_verwerfen(request: HttpRequest, pk: int) -> HttpResponse:
    eintrag = get_object_or_404(Beleg, pk=pk)
    try:
        services.beleg_verwerfen(eintrag)
    except ABLAGE_FEHLER as fehler:
        messages.error(request, str(fehler))
    else:
        messages.success(request, f"Beleg #{eintrag.pk} verworfen; das Dokument bleibt.")
    return redirect("dokumente:beleg", pk=pk)


@login_required
@require_POST
def beleg_umwidmen(request: HttpRequest, pk: int) -> HttpResponse:
    """Kein Beleg, sondern Mahnung, Rücklastschrift oder Vollstreckung (H6.8)."""
    eintrag = get_object_or_404(Beleg, pk=pk)
    form = BelegUmwidmenForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Bitte Art und Lieferant wählen.")
        return redirect("dokumente:beleg", pk=pk)
    try:
        ergebnis = services.beleg_umwidmen(
            eintrag, art=form.cleaned_data["art"], lieferant=form.cleaned_data["lieferant"]
        )
    except ABLAGE_FEHLER as fehler:
        messages.error(request, str(fehler))
        return redirect("dokumente:beleg", pk=pk)
    messages.success(request, f"Dokument #{ergebnis.pk} liegt jetzt am Lieferanten.")
    return redirect("dokumente:dokument", pk=ergebnis.pk)


@login_required
def beleg_aufteilen(request: HttpRequest, pk: int) -> HttpResponse:
    """Sammelrechnung aufteilen (H6.4): ein Beleg je Objekt oder Kostenart."""
    eintrag = get_object_or_404(Beleg.objects.select_related("dokument"), pk=pk)
    vorgabe = {
        "objekt": eintrag.objekt,
        "kategorie": eintrag.kategorie,
        "leistungsjahr": eintrag.leistungsjahr or eintrag.dokument.datum.year,
    }
    teile = BelegteilFormset(request.POST or None, initial=[vorgabe, vorgabe], prefix="teil")
    if request.method == "POST" and teile.is_valid():
        try:
            with transaction.atomic():
                services.beleg_aufteilen(
                    eintrag,
                    [
                        services.Belegteil(
                            betrag=t["betrag"],
                            objekt=t["objekt"],
                            kategorie=t["kategorie"],
                            leistungsjahr=t["leistungsjahr"],
                            umlagefaehiger_anteil=t.get("umlagefaehiger_anteil"),
                        )
                        for t in teile.cleaned_data
                        if t
                    ],
                )
        except ABLAGE_FEHLER as fehler:
            messages.error(request, str(fehler))
        else:
            messages.success(request, f"Beleg #{eintrag.pk} aufgeteilt.")
            return redirect("dokumente:beleg", pk=pk)
    return render(request, "dokumente/beleg_aufteilen.html", {"beleg": eintrag, "teile": teile})


@login_required
def lieferanten(request: HttpRequest) -> HttpResponse:
    form = LieferantForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            neu = services.lieferant_anlegen(
                form.cleaned_data["name"],
                iban=form.cleaned_data["iban"],
                email=form.cleaned_data["email"],
            )
        except ABLAGE_FEHLER as fehler:
            form.add_error(None, str(fehler))
        else:
            messages.success(request, f"Lieferant #{neu.pk} angelegt.")
            return redirect("dokumente:lieferanten")
    return render(
        request,
        "dokumente/lieferanten.html",
        {"form": form, "lieferanten": Lieferant.objects.order_by("-aktiv", "name")},
    )


@login_required
def lieferant(request: HttpRequest, pk: int) -> HttpResponse:
    eintrag = get_object_or_404(Lieferant, pk=pk)
    form = LieferantForm(
        request.POST or None,
        initial={
            "name": eintrag.name,
            "email": eintrag.email,
            "iban": eintrag.iban,
            "aktiv": eintrag.aktiv,
        },
    )
    if request.method == "POST" and form.is_valid():
        try:
            services.lieferant_aendern(
                eintrag,
                name=form.cleaned_data["name"],
                email=form.cleaned_data["email"],
                iban=form.cleaned_data["iban"],
                aktiv=form.cleaned_data["aktiv"],
            )
        except ABLAGE_FEHLER as fehler:
            form.add_error(None, str(fehler))
        else:
            messages.success(request, f"Lieferant #{eintrag.pk} gespeichert.")
            return redirect("dokumente:lieferant", pk=pk)
    return render(
        request,
        "dokumente/lieferant.html",
        {
            "lieferant": eintrag,
            "form": form,
            "dokumente": services.dokumente_zu(eintrag).order_by("-datum", "-pk"),
        },
    )


@login_required
def belegmappe_seite(request: HttpRequest) -> HttpResponse:
    """Belegmappe je Objekt und Jahr (H6.9): Summen prüfen, dann das PDF auf Knopfdruck."""
    form = BelegmappeForm(request.GET or None)
    mappe = None
    if form.is_valid():
        mappe = belegmappe.daten(form.cleaned_data["objekt"], form.cleaned_data["jahr"])
    return render(
        request,
        "dokumente/belegmappe.html",
        {"form": form, "mappe": mappe, "hinweise": mappe.hinweise() if mappe else []},
    )


@login_required
@xframe_options_sameorigin
def belegmappe_pdf(request: HttpRequest) -> HttpResponse:
    form = BelegmappeForm(request.GET)
    if not form.is_valid():
        return HttpResponseBadRequest("Objekt und Leistungsjahr angeben.")
    mappe = belegmappe.daten(form.cleaned_data["objekt"], form.cleaned_data["jahr"])
    try:
        daten = belegmappe.erzeugen(mappe)
    except pdf.PdfNichtVerfuegbar as fehler:
        return HttpResponse(
            f"Keine Belegmappe: {fehler}", status=409, content_type="text/plain; charset=utf-8"
        )
    return _pdf_antwort(daten, None, mappe.dateiname)


# --- Eingangsrechnungen (Schritt 3b) -------------------------------------------------


@login_required
def rechnungen(request: HttpRequest) -> HttpResponse:
    """Offene und strittige Eingangsrechnungen nach Fälligkeit, darunter die bezahlten."""
    alle = Eingangsrechnung.objects.select_related("beleg__lieferant", "beleg__dokument")
    return render(
        request,
        "dokumente/rechnungen.html",
        {
            "offen": alle.exclude(status=Eingangsrechnung.Status.BEZAHLT).order_by(
                "faellig_am", "pk"
            ),
            "bezahlt": alle.filter(status=Eingangsrechnung.Status.BEZAHLT).order_by(
                "-bezahlt_am", "-pk"
            )[:ABLAGE_ANZAHL],
            "heute": timezone.localdate(),
        },
    )


@login_required
def rechnung(request: HttpRequest, pk: int) -> HttpResponse:
    eintrag = get_object_or_404(
        Eingangsrechnung.objects.select_related(
            "beleg__lieferant", "beleg__dokument", "bezahlt_durch"
        ),
        pk=pk,
    )
    return render(
        request,
        "dokumente/rechnung.html",
        {
            "rechnung": eintrag,
            "kandidaten": (
                services.abbuchung_kandidaten(eintrag)
                if eintrag.status != Eingangsrechnung.Status.BEZAHLT
                else []
            ),
            "fristen": fristen_services.fristen_zu(eintrag),
            "heute": timezone.localdate(),
        },
    )


@login_required
@require_POST
def rechnung_verbinden(request: HttpRequest, pk: int) -> HttpResponse:
    """Max wählt die Abbuchung (bei einem unsicheren Treffer)."""
    eintrag = get_object_or_404(Eingangsrechnung, pk=pk)
    umsatz = get_object_or_404(Bankumsatz, pk=request.POST.get("umsatz") or 0)
    try:
        services.rechnung_bezahlt(eintrag, umsatz)
    except ABLAGE_FEHLER as fehler:
        messages.error(request, str(fehler))
    else:
        messages.success(request, f"Eingangsrechnung #{eintrag.pk} ist bezahlt.")
    return redirect("dokumente:rechnung", pk=pk)


@login_required
@require_POST
def rechnung_strittig(request: HttpRequest, pk: int) -> HttpResponse:
    eintrag = get_object_or_404(Eingangsrechnung, pk=pk)
    try:
        services.rechnung_strittig(eintrag, request.POST.get("strittig") == "1")
    except ABLAGE_FEHLER as fehler:
        messages.error(request, str(fehler))
    return redirect("dokumente:rechnung", pk=pk)
