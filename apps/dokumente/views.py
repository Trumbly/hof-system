"""Seiten für Ablage und Belege, Vorlagen, Schreiben und Serienmitteilungen.

Gesendet wird nur über die Seite eines Schreibens (oder einer Serie): Sie zeigt das
fertige PDF und trägt dessen Prüfsumme im Formular „Senden“. Ohne diesen Klick eines
angemeldeten Benutzers geht nichts hinaus (CLAUDE.md, Regel 8).
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import groupby
from typing import Any

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from apps.bank.models import Bankumsatz
from apps.dokumente import (
    anzeige,
    belegmappe,
    belegseite,
    bezuege,
    dokumentarten,
    pdf,
    services,
    textschicht,
    vorlagenarten,
)
from apps.dokumente.forms import (
    AbschlussForm,
    BelegFelder,
    BelegHochladenForm,
    BelegmappeForm,
    BelegteilFormset,
    BelegUmwidmenForm,
    LieferantForm,
    SerieEingrenzen,
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
from apps.kern import htmx, zeit
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
        services.vorlage_freigeben(
            eintrag, _benutzer(request), freigegeben_von=request.POST.get("freigegeben_von", "")
        )
    except FEHLER as fehler:
        messages.error(request, f"Nicht freigegeben: {fehler}")
    else:
        messages.success(
            request,
            f"Vorlage #{eintrag.pk} (Version {eintrag.version}) freigegeben von "
            f"{eintrag.freigabe_durch}.",
        )
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
    alle = Schreiben.objects.select_related("bezug_typ").prefetch_related("bezug")
    lesbar = anzeige.vorbereiten
    return render(
        request,
        "dokumente/schreiben_liste.html",
        {
            "bereit": lesbar(
                list(alle.filter(status=Schreiben.Status.BEREIT).order_by("serie", "pk"))
            ),
            "unterwegs": lesbar(list(alle.filter(status=Schreiben.Status.GESENDET))),
            "erledigt": lesbar(
                list(
                    alle.filter(
                        status__in=[Schreiben.Status.ZUGESTELLT, Schreiben.Status.VERWORFEN]
                    ).order_by("-pk")[:50]
                )
            ),
        },
    )


@login_required
def schreiben(request: HttpRequest, pk: int) -> HttpResponse:
    eintrag = get_object_or_404(
        Schreiben.objects.select_related("vorlage", "bezug_typ", "gesendet_von"), pk=pk
    )
    zu = anzeige.bezug(eintrag)
    return render(
        request,
        "dokumente/schreiben.html",
        {
            "schreiben": eintrag,
            "titel": anzeige.titel(eintrag, zu),
            "bezug": zu,
            "mail_text": services.mail_text(eintrag) if eintrag.inhalt_html else "",
            "versandweg": vorlagenarten.Versandweg.TITEL.get(eintrag.versandweg),
            "abschluss": AbschlussForm(),
            "anwalt": services.braucht_anwalt(eintrag),
            "anwalt_hinweis": vorlagenarten.ANWALT_HINWEIS,
            # Review 3, Punkt 6: Ist der Briefkopf nachgetragen, ein Klick „Neu erzeugen“.
            "neu_erzeugen": (
                eintrag.absender_luecken != ""
                and not services.briefkopf_luecken()
                and services.neu_erzeugbar(eintrag)
            ),
            # Review 1, Punkt 4: Der Knopf bleibt gesperrt, solange der Briefkopf fehlt.
            "gesperrt": (
                services.senden_gesperrt(eintrag)
                if eintrag.status == Schreiben.Status.BEREIT
                else ""
            ),
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
def schreiben_neu_erzeugen(request: HttpRequest, pk: int) -> HttpResponse:
    """„Neu erzeugen“ (Review 3, Punkt 6): dieselben Angaben, neues PDF, das alte Schreiben
    ist verworfen. Danach steht die Seite des neuen Schreibens zur Prüfung da."""
    eintrag = get_object_or_404(Schreiben, pk=pk)
    try:
        neu = services.schreiben_neu_erzeugen(eintrag)
    except FEHLER as fehler:
        messages.error(request, f"Nicht neu erzeugt: {fehler}")
        return redirect("dokumente:schreiben", pk=pk)
    messages.success(
        request,
        format_html(
            'Schreiben #{} neu erzeugt als #{}, das alte ist verworfen · <a href="{}">ansehen</a>',
            eintrag.pk,
            neu.pk,
            reverse("dokumente:schreiben", args=[neu.pk]),
        ),
    )
    return redirect("dokumente:schreiben", pk=neu.pk)


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
    nichts bekommt, steht namentlich mit Grund daneben; die Serie bricht nicht ab.

    Review 2, B13: Auswahl nach Objekt und Typ (was passt, ist vorgewählt) und einzelnen
    Einheiten; ein Freitext „Mitteilung an Nutzer“ (``mitteilung.text``); zur Wahl stehen nur
    seriengeeignete Vorlagen."""
    arten = bezuege.alle()
    schluessel = request.GET.get("bezug") or (arten[0].schluessel if arten else "")
    try:
        bezugsart = bezuege.holen(schluessel)
    except KeyError as fehler:
        raise Http404 from fehler
    grenzen = {k: request.POST.get(k) or request.GET.get(k) or "" for k in ("objekt", "typ")}
    eingrenzen = SerieEingrenzen(grenzen)
    objekt, typ = None, ""
    if eingrenzen.is_valid():
        objekt, typ = eingrenzen.cleaned_data["objekt"], eingrenzen.cleaned_data["typ"] or ""
    if not bezugsart.nach_typ:
        typ = ""
    objekte = {str(o.pk): o for o in bezugsart.eingrenzen(bezugsart.auswahl(), objekt, typ)}
    eingegrenzt = objekt is not None or bool(typ)
    initial: dict[str, Any] = {"auswahl": list(objekte) if eingegrenzt else []}
    form = SerieForm(
        request.POST or None,
        wahl=[(pk, bezugsart.bezeichnung(o)) for pk, o in objekte.items()],
        initial=initial,
    )
    vorlagen = form.fields["vorlage"].queryset  # type: ignore[attr-defined]
    if request.method == "GET" and vorlagen.count() == 1:
        form.initial["vorlage"] = vorlagen.first()
    plan: list[services.Serienempfaenger] | None = None
    marke = ""
    if request.method == "POST" and form.is_valid():
        daten = form.cleaned_data
        vorlage = daten["vorlage"]
        auswahl = sorted(daten["auswahl"], key=int)
        freitext = (daten["freitext"] or "").strip()
        marke = _serie_marke(vorlage.pk, auswahl, daten["versandweg"], daten["ohne_mail"], freitext)
        empfaenger = [
            (objekte[pk], _serie_kontext(bezugsart, objekte[pk], vorlage, freitext))
            for pk in auswahl
        ]
        try:
            plan = services.serie_planen(
                vorlage, empfaenger, daten["versandweg"] or None, ohne_mail=daten["ohne_mail"]
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
            "eingrenzen": eingrenzen,
            "grenzen": grenzen,
            "eingegrenzt": eingegrenzt,
            "arten": arten,
            "bezugsart": bezugsart,
            "plan": plan,
            "marke": marke,
            "wege": [*Versandweg.TITEL.items(), (services.AUSLASSEN, "auslassen")],
            "erzeugt": sum(1 for z in plan or [] if not z.ausgelassen),
        },
    )


def _serie_kontext(
    bezugsart: bezuege.Bezugsart, objekt: Any, vorlage: Vorlage, freitext: str
) -> dict[str, Any]:
    """Kontext eines Empfängers; bei Arten mit Freitext steht er unter ``mitteilung.text``."""
    kontext = dict(bezugsart.kontext(objekt))
    if vorlage.vorlagenart.freitext:
        kontext["mitteilung"] = {"text": freitext}
    return kontext


def _serie_marke(
    vorlage: int, auswahl: list[str], versandweg: str, ohne_mail: str, freitext: str = ""
) -> str:
    """Kennung der gezeigten Vorschau: Erzeugt wird nur, was die Vorschau gezeigt hat."""
    text = hashlib.sha256(freitext.encode()).hexdigest()[:8]
    roh = f"{vorlage}|{','.join(auswahl)}|{versandweg}|{ohne_mail}|{text}"
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
            "schreiben": anzeige.vorbereiten(alle),
            "bereit": bereit,
            "pruefsumme": services.serie_pruefsumme(bereit) if bereit else "",
            # Review 1, Punkt 4: gesperrt, solange der Briefkopf unvollständig ist.
            "gesperrt": next((g for g in (services.senden_gesperrt(s) for s in bereit) if g), ""),
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

ABLAGE_FEHLER = belegseite.ABLAGE_FEHLER
BELEG_FELDER = belegseite.BELEG_FELDER
VORSCHLAG_FELDER = belegseite.VORSCHLAG_FELDER
vorschlag_text = belegseite.vorschlag_text
_vorschlag_per_htmx = belegseite.vorschlag_per_htmx
_lieferant_aus = belegseite._lieferant_aus
ABLAGE_ANZAHL = 100


@login_required
def ablage(request: HttpRequest) -> HttpResponse:
    """Offene Belege und die zuletzt abgelegten Dokumente mit Absender, Betreff und Betrag
    (Review 2, B11). Hauptaktion: „Beleg hochladen“ (Foto oder PDF wählen genügt, B9)."""
    art = request.GET.get("art", "")
    dokumente = Dokument.objects.order_by("-eingegangen_am", "-pk")
    if art in dokumentarten.DOKUMENTARTEN:
        dokumente = dokumente.filter(art=art)
    offen = list(
        Beleg.objects.filter(status=Beleg.Status.OFFEN)
        .select_related("dokument", "lieferant")
        .order_by("dokument__datum", "pk")
    )
    # Review 3, Punkt 7: ohne Lieferanten den Namen, der im PDF steht (grau, „gelesen“).
    gelesen = textschicht.angaben_gemerkt(b.dokument for b in offen if b.lieferant is None)
    for b in offen:
        b.lieferant_gelesen = _gelesener_name(gelesen.get(b.dokument_id))  # type: ignore[attr-defined]
    return render(
        request,
        "dokumente/ablage.html",
        {
            "offen": offen,
            "dokumente": ablage_zeilen(dokumente[:ABLAGE_ANZAHL]),
            "arten": dokumentarten.auswahl(),
            "art": art,
            "hochladen": BelegNeuForm(),
        },
    )


class BelegNeuForm(forms.Form):
    """„Beleg hochladen“ auf der Ablage: nur die Datei (Review 2, B9)."""

    datei = forms.FileField(
        label="Foto oder PDF",
        help_text="Foto oder PDF. Datum, Betrag, Rechnungsnummer und Lieferant liest das "
        "System aus dem PDF; danach nur prüfen und bestätigen.",
        widget=forms.ClearableFileInput(attrs={"accept": "image/*,application/pdf"}),
    )


@dataclass(frozen=True)
class AblageZeile:
    dokument: Dokument
    absender: str
    betreff: str
    betrag: Any
    #: Seite, auf der man weiterarbeitet (Beleg, sonst Dokument)
    url: str
    #: Review 3, Punkt 7: Bei offenen Belegen ohne Angaben das, was im PDF steht (grau,
    #: „gelesen“); leer, sobald der Beleg selbst Lieferant bzw. Betrag hat.
    absender_gelesen: str = ""
    betrag_gelesen: Any = None


def _bezug_titel(bezug: Any) -> str:
    """Absender bzw. Bezug eines Dokuments, so wie Max ihn kennt (Name, V-Nummer, NW-ID)."""
    if bezug is None:
        return ""
    for feld in ("name", "nummer", "nw_id", "kuerzel"):
        wert = getattr(bezug, feld, "")
        if wert:
            return str(wert)
    anzeigename = getattr(bezug, "anzeigename", "")
    return str(anzeigename) if anzeigename else ""


def _gelesener_name(angaben: textschicht.Angaben | None) -> str:
    if angaben is None:
        return ""
    return angaben.lieferant.name if angaben.lieferant is not None else angaben.lieferant_name


def ablage_zeilen(dokumente: Iterable[Dokument]) -> list[AblageZeile]:
    """Je Dokument: Lieferant oder Absender, Betreff und Betrag statt nur „#26“."""
    liste = list(
        Dokument.objects.filter(pk__in=[d.pk for d in dokumente])
        .prefetch_related("belege__lieferant", "belege__kategorie", "bezuege__bezug")
        .order_by("-eingegangen_am", "-pk")
    )
    gelesen = textschicht.angaben_gemerkt(
        d for d in liste if any(b.status == Beleg.Status.OFFEN for b in d.belege.all())
    )
    zeilen = []
    for dok in liste:
        belege = [b for b in dok.belege.all() if b.status != Beleg.Status.VERWORFEN]
        lieferant = next((b.lieferant for b in belege if b.lieferant is not None), None)
        bezuege = [b.bezug for b in dok.bezuege.all() if b.bezug is not None]
        absender = (
            lieferant.name
            if lieferant is not None
            else next((t for t in map(_bezug_titel, bezuege) if t), "")
        )
        nummer = next((b.rechnungsnummer for b in belege if b.rechnungsnummer), "")
        kostenart = next((b.kategorie.bezeichnung for b in belege if b.kategorie), "")
        teile = [f"Rechnung {nummer}" if nummer else dok.dokumentart.titel]
        if kostenart:
            teile.append(kostenart)
        if dok.bemerkung:
            teile.append(dok.bemerkung.splitlines()[0][:80])
        betraege = [b.betrag for b in belege if b.betrag is not None]
        url = (
            reverse("dokumente:beleg", args=[belege[0].pk])
            if len(belege) == 1
            else reverse("dokumente:dokument", args=[dok.pk])
        )
        angaben = gelesen.get(dok.pk)
        zeilen.append(
            AblageZeile(
                dokument=dok,
                absender=absender,
                betreff=" · ".join(teile),
                betrag=sum(betraege) if betraege else None,
                url=url,
                absender_gelesen=_gelesener_name(angaben) if not absender else "",
                betrag_gelesen=angaben.betrag if angaben and not betraege else None,
            )
        )
    return zeilen


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
    beleg_id = request.GET.get("beleg", "")
    dieser = Beleg.objects.filter(pk=int(beleg_id)).first() if beleg_id.isdigit() else None
    treffer = services.vorschlag(lieferant, werte.get("datum") or zeit.heute(), ohne=dieser)
    gefuellt = []
    if treffer is not None:
        for feld in VORSCHLAG_FELDER:
            if feld not in werte:
                werte[feld] = getattr(treffer, feld)
                gefuellt.append(feld)
    form = BelegFelder(initial=werte)
    _vorschlag_per_htmx(form, dieser)
    if dieser is not None and dieser.status == Beleg.Status.OFFEN:
        angaben = textschicht.angaben_gemerkt([dieser.dokument]).get(dieser.dokument_id)
        belegseite.betrag_vergleich(form, angaben.betrag if angaben else None)
    return render(
        request,
        "dokumente/_beleg_felder.html",
        {
            "form": form,
            "felder": BELEG_FELDER,
            "vorschlag_text": vorschlag_text(
                treffer.aus_beleg if treffer else None, werte, gefuellt
            ),
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
        messages.success(
            request,
            format_html(
                'Beleg #{} hängt an Buchung #{} · <a href="{}">Umsatz ansehen</a>',
                eintrag.pk,
                buchung.pk,
                reverse("kassenbuch:umsatz", args=[buchung.umsatz_id]),
            ),
        )
    zurueck = request.POST.get("zurueck", "")
    if zurueck.startswith("/") and not zurueck.startswith("//"):
        return redirect(zurueck)
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
        if htmx.ist_htmx(request):
            return belegseite._umwidmen_dialog(request, eintrag, form)
        messages.error(request, "Bitte Art und Lieferant wählen.")
        return redirect("dokumente:beleg", pk=pk)
    try:
        ergebnis = services.beleg_umwidmen(
            eintrag, art=form.cleaned_data["art"], lieferant=form.cleaned_data["lieferant"]
        )
    except ABLAGE_FEHLER as fehler:
        if htmx.ist_htmx(request):
            form.add_error(None, str(fehler))
            return belegseite._umwidmen_dialog(request, eintrag, form)
        messages.error(request, str(fehler))
        return redirect("dokumente:beleg", pk=pk)
    naechster = belegseite.naechster_offener()
    messages.success(
        request,
        format_html(
            'Dokument #{} liegt jetzt am Lieferanten · <a href="{}">ansehen</a>',
            ergebnis.pk,
            reverse("dokumente:dokument", args=[ergebnis.pk]),
        ),
    )
    ziel = (
        reverse("dokumente:beleg", args=[naechster.pk])
        if naechster is not None
        else reverse("dokumente:dokument", args=[ergebnis.pk])
    )
    return belegseite.weiter(request, ziel)


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
    """Belegmappe je Objekt und Jahr (H6.9): Summen prüfen, dann das PDF auf Knopfdruck.

    Review 2, B11: Objekt und aktuelles Jahr sind vorbelegt (die Seite zeigt sofort die
    Mappe), Buchungs- und Beleg-IDs sind Links, „Buchungen ohne Beleg“ ist eine Tabelle mit
    „Beleg zuordnen“ je Zeile (Dialog mit passenden Belegen ohne Buchung)."""
    daten = request.GET.copy()
    daten.pop("dialog", None)
    daten.pop("buchung", None)
    if not daten.get("jahr"):
        daten["jahr"] = str(zeit.heute().year)
    if not daten.get("objekt"):
        jahr = daten["jahr"]
        vorgabe = belegmappe.vorgabe(int(jahr) if jahr.isdigit() else zeit.heute().year)
        if vorgabe is not None:
            daten["objekt"] = str(vorgabe.pk)
    form = BelegmappeForm(daten)
    mappe = None
    if form.is_valid():
        mappe = belegmappe.daten(form.cleaned_data["objekt"], form.cleaned_data["jahr"])
    if request.GET.get("dialog") == "zuordnen" and mappe is not None:
        buchung = get_object_or_404(
            Buchung.objects.select_related("umsatz", "kategorie"),
            pk=request.GET.get("buchung") or 0,
        )
        return render(
            request,
            "dokumente/_belegmappe_zuordnen.html",
            {
                "buchung": buchung,
                "belege": belegmappe.belege_fuer(buchung, mappe),
                "zurueck": f"{reverse('dokumente:belegmappe')}?{daten.urlencode()}",
            },
        )
    return render(
        request,
        "dokumente/belegmappe.html",
        {
            "form": form,
            "mappe": mappe,
            "hinweise": mappe.hinweise() if mappe else [],
            "ohne_beleg": belegmappe.buchungen(mappe.buchungen_ohne_beleg) if mappe else [],
            "ohne_buchung": [z.beleg for z in mappe.zeilen if z.beleg.buchung_id is None]
            if mappe
            else [],
            "parameter": daten.urlencode(),
        },
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
            "heute": zeit.heute(),
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
            "heute": zeit.heute(),
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
