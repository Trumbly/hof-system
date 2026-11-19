"""Seiten der Stammdaten: Einheiten, Parteien, Verträge, Übergabe am Handy, Leerstand,
Datenstand. Gespeichert wird über ``services``; die Seiten prüfen nur die Eingaben."""

from __future__ import annotations

import datetime
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Prefetch
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.views.decorators.http import require_POST

from apps.anbindungen import services as anbindungen
from apps.anbindungen.models import Buchungsanfrage
from apps.dokumente.models import Schreiben
from apps.stammdaten import datenstand, leerstand, schreiben, services
from apps.stammdaten import forms as f
from apps.stammdaten.models import (
    Checkpunkt,
    Einheit,
    Kontaktweg,
    Kuendigung,
    Partei,
    Schluessel,
    Uebergabe,
    UebergabeFoto,
    Vertrag,
    Vertragspartei,
)
from apps.zaehler import services as zaehler_services

#: Ein Handler gibt den Anker zurück, oder (Anker, Meldung), wenn er selbst sagt, was
#: passiert ist (Review 2, B: Rückmeldungen mit Link).
Handler = Callable[[HttpRequest, Any, forms.Form], "str | tuple[str, str]"]


def _heute() -> datetime.date:
    return datetime.date.today()


def _zurueck(url: str, anker: str = "") -> HttpResponse:
    return redirect(f"{url}#{anker}" if anker else url)


def _formular_seite(
    request: HttpRequest,
    objekt: Any,
    formulare: dict[str, forms.Form],
    handler: dict[str, Handler],
    vorlage: str,
    kontext: Callable[[], dict[str, Any]],
) -> HttpResponse:
    """Seite mit mehreren Formularen; ``aktion`` im POST wählt eines aus.

    Ein Handler bekommt das gültige Formular, ruft den Service und gibt den Anker zurück.
    Fehler aus dem Service erscheinen als Meldung, das Formular bleibt ausgefüllt.
    """
    if request.method == "POST":
        aktion = request.POST.get("aktion", "")
        if aktion not in formulare:
            raise Http404("Unbekannte Aktion")
        form = formulare[aktion]
        if form.is_valid():
            try:
                ergebnis = handler[aktion](request, objekt, form)
            except services.StammdatenFehler as fehler:
                messages.error(request, str(fehler))
            else:
                anker, meldung = ergebnis if isinstance(ergebnis, tuple) else (ergebnis, "")
                messages.success(request, meldung or "Gespeichert.")
                return _zurueck(request.path, anker)
        else:
            messages.error(request, "Bitte die markierten Felder prüfen.")
    return render(request, vorlage, {**kontext(), **formulare})


def _gebunden(request: HttpRequest, prefix: str, form_klasse: Any, **kwargs: Any) -> Any:
    """Bindet nur das Formular, dessen ``aktion`` gesendet wurde."""
    if request.method == "POST" and request.POST.get("aktion") == prefix:
        return form_klasse(request.POST, request.FILES, prefix=prefix, **kwargs)
    return form_klasse(prefix=prefix, **kwargs)


# --- Übersicht -----------------------------------------------------------------------


@login_required
def uebersicht(request: HttpRequest) -> HttpResponse:
    stand = datenstand.ermitteln(_heute())
    return render(
        request,
        "stammdaten/uebersicht.html",
        {
            "einheiten": Einheit.objects.filter(aufgeloest_am__isnull=True).count(),
            "parteien": Partei.objects.count(),
            "vertraege": Vertrag.objects.filter(status__in=services.BINDENDE_STATUS).count(),
            "luecken": sum(b.anzahl_luecken for b in stand),
        },
    )


# --- Einheiten -----------------------------------------------------------------------


@login_required
def einheiten(request: HttpRequest) -> HttpResponse:
    heute = _heute()
    liste = list(Einheit.objects.select_related("objekt").order_by("objekt__kuerzel", "nw_id"))
    for einheit in liste:
        einheit.status_heute = einheit.status_am(heute)  # type: ignore[attr-defined]
    return render(request, "stammdaten/einheiten.html", {"einheiten": liste})


@login_required
def einheit_neu(request: HttpRequest) -> HttpResponse:
    form = f.EinheitNeuForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            einheit = services.einheit_anlegen(
                d["objekt"], d["typ"], bezeichnung=d["bezeichnung"], flaeche_qm=d["flaeche_qm"]
            )
        except services.StammdatenFehler as fehler:
            messages.error(request, str(fehler))
        else:
            messages.success(request, f"Einheit {einheit.nw_id} angelegt.")
            return redirect("stammdaten:einheit", einheit.pk)
    return render(request, "stammdaten/neu.html", {"form": form, "titel": "Neue Einheit"})


@login_required
def einheit(request: HttpRequest, pk: int) -> HttpResponse:
    einheit = get_object_or_404(Einheit.objects.select_related("objekt"), pk=pk)
    stamm_klasse = f.WohnraumForm if einheit.ist_wohnraum else f.EinheitForm
    formulare: dict[str, forms.Form] = {
        "stammfelder": _gebunden(request, "stammfelder", stamm_klasse, instance=einheit),
    }
    if not einheit.ist_wohnraum:
        formulare |= {
            "status": _gebunden(request, "status", f.StatusForm),
            "zielmiete": _gebunden(request, "zielmiete", f.ZielmieteForm),
            "schluessel": _gebunden(request, "schluessel", f.SchluesselForm),
        }
        for s in einheit.schluessel.all():
            schluessel_prefix = f"schluessel-{s.pk}"
            formulare[schluessel_prefix] = _gebunden(
                request, schluessel_prefix, f.SchluesselForm, instance=s
            )

    def stammfelder(_: HttpRequest, e: Einheit, form: forms.Form) -> str:
        services.einheit_aendern(e, **form.cleaned_data)
        return "stammfelder"

    def status(_: HttpRequest, e: Einheit, form: forms.Form) -> str:
        d = form.cleaned_data
        services.einheit_status_setzen(e, d["status"], d["gueltig_ab"], d["grund"])
        return "status"

    def zielmiete(_: HttpRequest, e: Einheit, form: forms.Form) -> str:
        d = form.cleaned_data
        services.zielmiete_setzen(e, d["betrag"], d["gueltig_ab"], d["begruendung"])
        return "zielmiete"

    def schluessel_neu(_: HttpRequest, e: Einheit, form: forms.Form) -> str:
        services.schluessel_anlegen(e, **form.cleaned_data)
        return "schluessel"

    handler: dict[str, Handler] = {
        "stammfelder": stammfelder,
        "status": status,
        "zielmiete": zielmiete,
        "schluessel": schluessel_neu,
    }
    for prefix, form in formulare.items():
        if prefix.startswith("schluessel-"):
            instanz = form.instance  # type: ignore[attr-defined]

            def aendern(
                _: HttpRequest, e: Einheit, form: forms.Form, s: Schluessel = instanz
            ) -> str:
                services.schluessel_aendern(s, **form.cleaned_data)
                return "schluessel"

            handler[prefix] = aendern

    def kontext() -> dict[str, Any]:
        heute = _heute()
        return {
            "einheit": einheit,
            "heute": heute,
            "status_heute": einheit.status_am(heute),
            "zielmiete_heute": einheit.zielmiete_am(heute),
            "statusstaende": einheit.statusstaende.order_by("-gueltig_ab"),
            "zielmieten": einheit.zielmieten.order_by("-gueltig_ab"),
            "schluessel_formulare": [
                (form.instance, prefix, form)  # type: ignore[attr-defined]
                for prefix, form in formulare.items()
                if prefix.startswith("schluessel-")
            ],
            "vertraege": einheit.vertraege.order_by("-beginn"),
            "checklisten": einheit.checklisten.prefetch_related("punkte").order_by("-pk"),
        }

    return _formular_seite(request, einheit, formulare, handler, "stammdaten/einheit.html", kontext)


# --- Parteien ------------------------------------------------------------------------


@login_required
def parteien(request: HttpRequest) -> HttpResponse:
    liste = Partei.objects.prefetch_related("kontaktwege").order_by("nachname", "firma", "pk")
    return render(request, "stammdaten/parteien.html", {"parteien": liste})


@login_required
def partei_neu(request: HttpRequest) -> HttpResponse:
    form = f.ParteiForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            partei = services.partei_anlegen(**form.cleaned_data)
        except services.StammdatenFehler as fehler:
            messages.error(request, str(fehler))
        else:
            messages.success(request, f"Partei #{partei.pk} angelegt.")
            return redirect("stammdaten:partei", partei.pk)
    return render(request, "stammdaten/neu.html", {"form": form, "titel": "Neue Partei"})


@login_required
def partei(request: HttpRequest, pk: int) -> HttpResponse:
    partei = get_object_or_404(Partei, pk=pk)
    formulare: dict[str, forms.Form] = {
        "stammfelder": _gebunden(request, "stammfelder", f.ParteiForm, instance=partei),
        "kontaktwege": _gebunden(request, "kontaktwege", f.KontaktwegForm),
    }

    def stammfelder(_: HttpRequest, p: Partei, form: forms.Form) -> str:
        services.partei_aendern(p, **form.cleaned_data)
        return "stammfelder"

    def kontakt(_: HttpRequest, p: Partei, form: forms.Form) -> str:
        d = form.cleaned_data
        services.kontaktweg_hinzufuegen(p, d["art"], d["wert"], bevorzugt=d["bevorzugt"])
        return "kontaktwege"

    def kontext() -> dict[str, Any]:
        return {
            "partei": partei,
            "kontaktwege_liste": partei.kontaktwege.all(),
            "vertragsparteien": partei.vertragsparteien.select_related("vertrag"),
        }

    return _formular_seite(
        request,
        partei,
        formulare,
        {"stammfelder": stammfelder, "kontaktwege": kontakt},
        "stammdaten/partei.html",
        kontext,
    )


@login_required
@require_POST
def kontaktweg_entfernen(request: HttpRequest, pk: int) -> HttpResponse:
    kontaktweg = get_object_or_404(Kontaktweg, pk=pk)
    partei_pk = kontaktweg.partei_id
    services.kontaktweg_entfernen(kontaktweg)
    messages.success(request, "Kontaktweg entfernt.")
    return _zurueck(reverse("stammdaten:partei", args=[partei_pk]), "kontaktwege")


# --- Verträge ------------------------------------------------------------------------


@login_required
def vertraege(request: HttpRequest) -> HttpResponse:
    liste = Vertrag.objects.prefetch_related(
        "einheiten",
        Prefetch(
            "vertragsparteien",
            queryset=Vertragspartei.objects.filter(
                rolle=Vertragspartei.Rolle.MIETER
            ).select_related("partei"),
            to_attr="mieter",
        ),
    ).order_by("nummer")
    return render(request, "stammdaten/vertraege.html", {"vertraege": liste})


def _dokument_melden(request: HttpRequest, ergebnis: schreiben.Ergebnis) -> None:
    if isinstance(ergebnis, Schreiben):
        messages.success(
            request,
            f"Vertragsdokument als PDF erzeugt (Schreiben #{ergebnis.pk}, Seite „Schreiben“).",
        )
    else:
        messages.warning(
            request, f"Kein PDF: {ergebnis.hinweis} (Arbeitsliste „Schreiben erstellen“)."
        )


@login_required
def vertrag_neu(request: HttpRequest) -> HttpResponse:
    initial: dict[str, Any] = {}
    if request.GET.get("einheit", "").isdigit():
        initial["einheit"] = int(request.GET["einheit"])
    form = f.VertragNeuForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            with transaction.atomic():
                vertrag = services.vertrag_anlegen(
                    einheiten=[d["einheit"]],
                    mieter=[d["mieter"]],
                    vertragsart=d["vertragsart"] or None,
                    mandant=d["mandant"],
                    beginn=d["beginn"],
                    ende=d["ende"],
                    miete=d["miete"],
                    pauschale=d["pauschale"] or Decimal("0.00"),
                    steuersatz=d["steuersatz"] or Decimal("0.00"),
                    zahlweise=d["zahlweise"],
                    kaution=d["kaution"],
                    kuendigungsregel=d["kuendigungsregel"] or None,
                )
                services.checkliste_anlegen("vertrag", vertrag=vertrag)
                ergebnis = services.vertragsdokument_erzeugen(vertrag)
                # Review 2, A11: Eine angenommene Buchungsanfrage der Einheit gehört zu diesem
                # Vertrag, egal ob er von der Anfrage oder der Einheitenseite aus entsteht.
                anfrage = anbindungen.vertrag_zur_anfrage(vertrag)
        except (services.StammdatenFehler, anbindungen.AnbindungFehler) as fehler:
            messages.error(request, str(fehler))
        else:
            meldung = format_html("Vertrag {} als Entwurf angelegt.", vertrag.nummer)
            if anfrage is not None:
                meldung = format_html(
                    '{} Mit der angenommenen Buchungsanfrage <a href="{}">#{}</a> verknüpft.',
                    meldung,
                    reverse("anbindungen:anfrage", args=[anfrage.pk]),
                    anfrage.pk,
                )
            messages.success(request, meldung)
            _dokument_melden(request, ergebnis)
            return redirect("stammdaten:vertrag", vertrag.pk)
    einheit = Einheit.objects.filter(pk=initial.get("einheit") or 0).first() if initial else None
    return render(
        request,
        "stammdaten/neu.html",
        {
            "form": form,
            "titel": "Neuer Vertrag",
            "angenommene_anfrage": anbindungen.angenommene_anfrage(einheit) if einheit else None,
        },
    )


def _kuendigung_anfrage(request: HttpRequest, vertrag: Vertrag) -> Buchungsanfrage | None:
    """Die offene Website-Kündigung, aus der der Dialog vorausgefüllt wird (``?anfrage=`` oder
    das versteckte Feld beim Speichern). Nur, wenn sie zu einer Einheit des Vertrags gehört."""
    roh = request.POST.get("kuendigung-anfrage") or request.GET.get("anfrage") or ""
    if not roh.isdigit():
        return None
    return (
        Buchungsanfrage.objects.filter(
            pk=int(roh),
            art=Buchungsanfrage.Art.KUENDIGUNG,
            status__in=anbindungen.NOCH_OFFEN,
            einheit__in=vertrag.einheiten.all(),
        )
        .select_related("einheit")
        .first()
    )


def _kuendigung_meldung(kuendigung: Kuendigung, anfrage: Buchungsanfrage | None) -> str:
    """„Kündigung erfasst · Ende 28.02.2027 · Bestätigung #3 liegt bereit → ansehen“."""
    brief = (
        Schreiben.objects.filter(
            bezug_typ=ContentType.objects.get_for_model(Kuendigung), bezug_id=kuendigung.pk
        )
        .order_by("-pk")
        .first()
    )
    teile = [
        format_html("Aufhebung zum {} erfasst", f"{kuendigung.fristende:%d.%m.%Y}")
        if kuendigung.aufhebung
        else format_html("Kündigung erfasst · Ende {}", f"{kuendigung.fristende:%d.%m.%Y}")
    ]
    art = "Aufhebungsvertrag" if kuendigung.aufhebung else "Bestätigung"
    if brief is not None:
        teile.append(
            format_html(
                '{} #{} liegt bereit → <a href="{}">ansehen</a>',
                art,
                brief.pk,
                reverse("dokumente:schreiben", args=[brief.pk]),
            )
        )
    else:
        teile.append(format_html("{} nicht erzeugt (Arbeitsliste „Schreiben erstellen“)", art))
    if anfrage is not None:
        teile.append(
            format_html(
                'Anfrage <a href="{}">#{}</a> erledigt',
                reverse("anbindungen:anfrage", args=[anfrage.pk]),
                anfrage.pk,
            )
        )
    return format_html_join(" · ", "{}", ((t,) for t in teile))


@login_required
def vertrag(request: HttpRequest, pk: int) -> HttpResponse:
    vertrag = get_object_or_404(Vertrag.objects.select_related("mandant"), pk=pk)
    kaution = getattr(vertrag, "kaution", None)
    formulare: dict[str, forms.Form] = {
        "stammfelder": _gebunden(request, "stammfelder", f.VertragForm, instance=vertrag),
        "kondition": _gebunden(request, "kondition", f.KonditionForm),
        "unterschrift": _gebunden(request, "unterschrift", f.UnterschriftForm),
        "partei": _gebunden(request, "partei", f.VertragsparteiForm),
    }
    if kaution is not None:
        formulare["kaution"] = _gebunden(request, "kaution", f.KautionForm, instance=kaution)
        formulare["kaution_abrechnen"] = _gebunden(
            request, "kaution_abrechnen", f.KautionAbrechnenForm
        )
    kuendigung_anfrage = _kuendigung_anfrage(request, vertrag)
    if vertrag.status == Vertrag.Status.AKTIV:
        formulare["kuendigung"] = _gebunden(
            request,
            "kuendigung",
            f.KuendigungForm,
            vertrag=vertrag,
            initial=(
                anbindungen.kuendigung_vorschlag(kuendigung_anfrage) if kuendigung_anfrage else {}
            ),
        )

    def stammfelder(_: HttpRequest, v: Vertrag, form: forms.Form) -> str:
        services.vertrag_aendern(v, **form.cleaned_data)
        return "stammfelder"

    def kondition(_: HttpRequest, v: Vertrag, form: forms.Form) -> str:
        d = form.cleaned_data
        services.kondition_ab(
            v,
            d["gueltig_ab"],
            miete=d["miete"],
            pauschale=d["pauschale"],
            steuersatz=d["steuersatz"],
            zahlweise=d["zahlweise"] or None,
            grund=d["grund"],
        )
        return "kondition"

    def unterschrift(_: HttpRequest, v: Vertrag, form: forms.Form) -> str:
        d = form.cleaned_data
        services.vertrag_unterschrieben(v, d["unterschrieben_am"], d["scan"])
        return "unterschrift"

    def partei_hinzu(_: HttpRequest, v: Vertrag, form: forms.Form) -> str:
        services.vertragspartei_hinzufuegen(
            v, form.cleaned_data["partei"], form.cleaned_data["rolle"]
        )
        return "parteien"

    def kaution_speichern(_: HttpRequest, v: Vertrag, form: forms.Form) -> str:
        services.kaution_aendern(v.kaution, **form.cleaned_data)
        return "kaution"

    def kaution_abrechnen(_: HttpRequest, v: Vertrag, form: forms.Form) -> str:
        d = form.cleaned_data
        services.kaution_abrechnen(v.kaution, d["datum"], d["einbehalt"], d["notiz"])
        return "kaution"

    def kuendigung(_: HttpRequest, v: Vertrag, form: forms.Form) -> tuple[str, str]:
        d = form.cleaned_data
        felder: dict[str, Any] = {
            "von": d["von"],
            "zum_wunsch": d["zum_wunsch"],
            "rueckgabetermin": d["rueckgabetermin"],
            "zugangsweg": d["zugangsweg"] or "",
            "aufhebung": d["wunsch_wahl"] == f.KuendigungForm.AUFHEBUNG,
        }
        if kuendigung_anfrage is not None:
            try:
                erfasst = anbindungen.kuendigung_aus_anfrage(
                    kuendigung_anfrage, v, d["zugang_am"], **felder
                )
            except anbindungen.AnbindungFehler as fehler:
                raise services.StammdatenFehler(str(fehler)) from fehler
        else:
            erfasst = services.kuendigung_erfassen(v, d["zugang_am"], **felder)
        return "kuendigung", _kuendigung_meldung(erfasst, kuendigung_anfrage)

    def kontext() -> dict[str, Any]:
        heute = _heute()
        return {
            "vertrag": vertrag,
            "kaution_obj": kaution,
            "heute": heute,
            "kondition_heute": vertrag.kondition_am(max(heute, vertrag.beginn)),
            "konditionen": vertrag.konditionen.order_by("-gueltig_ab"),
            "einheiten": vertrag.einheiten.select_related("objekt"),
            "vertragsparteien": vertrag.vertragsparteien.select_related("partei"),
            "kuendigungen": vertrag.kuendigungen.all(),
            "kuendigung_anfrage": kuendigung_anfrage,
            "absender_abweichungen": (
                anbindungen.absender_abweichungen(kuendigung_anfrage, vertrag)
                if kuendigung_anfrage
                else []
            ),
            "buchungsanfragen": vertrag.buchungsanfragen.order_by("pk"),
            "kuendigungsregel": services.kuendigungsregel_fuer(vertrag),
            "uebergaben": vertrag.uebergaben.all(),
            "checklisten": vertrag.checklisten.prefetch_related("punkte").order_by("-pk"),
        }

    return _formular_seite(
        request,
        vertrag,
        formulare,
        {
            "stammfelder": stammfelder,
            "kondition": kondition,
            "unterschrift": unterschrift,
            "partei": partei_hinzu,
            "kaution": kaution_speichern,
            "kaution_abrechnen": kaution_abrechnen,
            "kuendigung": kuendigung,
        },
        "stammdaten/vertrag.html",
        kontext,
    )


@login_required
@require_POST
def vertragsdokument(request: HttpRequest, pk: int) -> HttpResponse:
    vertrag = get_object_or_404(Vertrag, pk=pk)
    try:
        ergebnis = services.vertragsdokument_erzeugen(vertrag)
    except services.StammdatenFehler as fehler:
        messages.error(request, str(fehler))
    else:
        _dokument_melden(request, ergebnis)
    return redirect("stammdaten:vertrag", pk)


@login_required
@require_POST
def checkpunkt(request: HttpRequest, pk: int) -> HttpResponse:
    punkt = get_object_or_404(Checkpunkt.objects.select_related("checkliste"), pk=pk)
    if punkt.erledigt_am is None:
        services.checkpunkt_erledigen(punkt, _heute())
    else:
        services.checkpunkt_oeffnen(punkt)
    ziel = request.POST.get("weiter", "")
    if not ziel.startswith("/") or ziel.startswith("//"):
        ziel = reverse("stammdaten:uebersicht")
    return redirect(ziel)


# --- Übergabe und Rückgabe am Handy (O1) ---------------------------------------------

SCHRITTE: tuple[tuple[str, str], ...] = (
    ("vorab", "Vorab"),
    ("zaehler", "Zählerstände"),
    ("fotos", "Fotos"),
    ("schluessel", "Schlüssel"),
    ("unterschrift", "Zustand und Unterschrift"),
    ("abschluss", "Abschluss"),
)


@login_required
@require_POST
def uebergabe_starten(request: HttpRequest, pk: int, art: str) -> HttpResponse:
    vertrag = get_object_or_404(Vertrag, pk=pk)
    try:
        uebergabe = services.uebergabe_beginnen(vertrag, art, _heute())
    except services.StammdatenFehler as fehler:
        messages.error(request, str(fehler))
        return redirect("stammdaten:vertrag", pk)
    return redirect("stammdaten:uebergabe", uebergabe.pk, 1)


def _zaehler_aus_post(request: HttpRequest, uebergabe: Uebergabe) -> list[dict[str, Any]]:
    """Stand, Foto und Kennzeichen „geschätzt“ je Zähler der Einheiten des Vertrags."""
    zeilen: list[dict[str, Any]] = []
    for z in zaehler_services.zaehler_der_uebergabe(uebergabe):
        zeilen.append(
            {
                "zaehler": z.pk,
                "stand": request.POST.get(f"stand_{z.pk}", ""),
                "foto": request.FILES.get(f"foto_{z.pk}"),
                "art": "geschaetzt" if request.POST.get(f"geschaetzt_{z.pk}") else "abgelesen",
            }
        )
    return zeilen


def _uebergabe_post(request: HttpRequest, uebergabe: Uebergabe, schritt: str) -> str | None:
    """Speichert einen Schritt. Rückgabe: Fehlermeldung oder None."""
    if schritt == "vorab":
        form = f.UebergabeVorabForm(request.POST)
        if not form.is_valid():
            return "Bitte ein gültiges Datum angeben."
        services.uebergabe_speichern(uebergabe, datum=form.cleaned_data["datum"])
        if form.cleaned_data["vertrag_unterschrieben"]:
            services.vertrag_unterschrieben(uebergabe.vertrag, form.cleaned_data["datum"])
    elif schritt == "zaehler":
        zeilen = _zaehler_aus_post(request, uebergabe)
        for zeile in zeilen:
            if zeile["foto"] is not None and (fehler := f.foto_pruefen(zeile["foto"])):
                return fehler
        services.uebergabe_speichern(uebergabe, zaehlerstaende=zeilen)
    elif schritt == "fotos":
        dateien = request.FILES.getlist("fotos")
        for datei in dateien:
            if fehler := f.foto_pruefen(datei):
                return fehler
        for datei in dateien:
            services.uebergabe_foto_hinzufuegen(uebergabe, datei)
    elif schritt == "schluessel":
        eintraege = []
        for s in services.schluessel_der_uebergabe(uebergabe):
            wert = request.POST.get(f"schluessel_{s.pk}", "0") or "0"
            if not wert.isdigit() or int(wert) > s.anzahl:
                return f"Schlüssel #{s.pk}: Anzahl zwischen 0 und {s.anzahl}."
            eintraege.append({"schluessel": s.pk, "anzahl": int(wert)})
        services.uebergabe_speichern(uebergabe, schluessel=eintraege)
    elif schritt == "unterschrift":
        datei = request.FILES.get("unterschrift")
        if datei is not None and (fehler := f.foto_pruefen(datei)):
            return fehler
        services.uebergabe_speichern(
            uebergabe,
            zustand=request.POST.get("zustand", ""),
            zurueckgelassen=request.POST.get("zurueckgelassen", ""),
            unterschrift=datei,
        )
    elif schritt == "abschluss":
        services.uebergabe_abschliessen(uebergabe)
    return None


@login_required
def uebergabe(request: HttpRequest, pk: int, schritt: int) -> HttpResponse:
    """Übergabe oder Rückgabe in Schritten; Fotos direkt aus der Kamera."""
    uebergabe = get_object_or_404(Uebergabe.objects.select_related("vertrag"), pk=pk)
    if not 1 <= schritt <= len(SCHRITTE):
        raise Http404("Unbekannter Schritt")
    schluessel_name, schritt_titel = SCHRITTE[schritt - 1]
    if uebergabe.status == Uebergabe.Status.ABGESCHLOSSEN:
        messages.info(request, f"{uebergabe} ist abgeschlossen.")
        return redirect("stammdaten:vertrag", uebergabe.vertrag_id)

    if request.method == "POST":
        try:
            fehler = _uebergabe_post(request, uebergabe, schluessel_name)
        except services.StammdatenFehler as ausnahme:
            fehler = str(ausnahme)
        if fehler:
            messages.error(request, fehler)
        elif schluessel_name == "abschluss":
            messages.success(request, f"{uebergabe.get_art_display()} abgeschlossen.")
            return redirect("stammdaten:vertrag", uebergabe.vertrag_id)
        else:
            return redirect("stammdaten:uebergabe", uebergabe.pk, schritt + 1)

    uebergabe.refresh_from_db()
    erfasst = {s.zaehler_id: s for s in uebergabe.zaehlerstaende.all()}
    zaehler_zeilen = [
        (z, erfasst.get(z.pk), z.letzter_stand(uebergabe.datum - datetime.timedelta(days=1)))
        for z in zaehler_services.zaehler_der_uebergabe(uebergabe)
    ]
    schluessel = services.schluessel_der_uebergabe(uebergabe)
    gespeichert = {int(e["schluessel"]): int(e["anzahl"]) for e in uebergabe.schluessel}
    return render(
        request,
        "stammdaten/uebergabe.html",
        {
            "uebergabe": uebergabe,
            "vertrag": uebergabe.vertrag,
            "schritt": schritt,
            "schritt_name": schluessel_name,
            "schritt_titel": schritt_titel,
            "schritte": SCHRITTE,
            "anzahl_schritte": len(SCHRITTE),
            "vorab_form": f.UebergabeVorabForm(initial={"datum": uebergabe.datum}),
            "zaehler_zeilen": zaehler_zeilen,
            "zaehlerstaende": list(erfasst.values()),
            "schluessel_zeilen": [
                (s, gespeichert.get(s.pk, s.anzahl if not gespeichert else 0)) for s in schluessel
            ],
            "fotos": uebergabe.fotos.all(),
            "hindernisse": services.uebergabe_hindernisse(uebergabe),
            "einheiten": uebergabe.vertrag.einheiten.all(),
        },
    )


@login_required
def uebergabe_foto(request: HttpRequest, pk: int) -> FileResponse:
    """Liefert ein Foto nur mit Anmeldung aus (nie direkt über Caddy)."""
    foto = get_object_or_404(UebergabeFoto, pk=pk)
    return FileResponse(foto.bild.open("rb"))


# --- Leerstand und Datenstand --------------------------------------------------------


@login_required
def leerstand_seite(request: HttpRequest) -> HttpResponse:
    """Leerstand je Einheit mit Tagen frei und entgangenem Ertrag (H2.11)."""
    heute = _heute()
    try:
        bis = (
            datetime.date.fromisoformat(request.GET.get("bis", ""))
            if request.GET.get("bis")
            else heute
        )
        von = (
            datetime.date.fromisoformat(request.GET["von"])
            if request.GET.get("von")
            else bis - datetime.timedelta(days=364)
        )
    except ValueError:
        messages.error(request, "Ungültiges Datum.")
        von, bis = heute - datetime.timedelta(days=364), heute
    zeilen = leerstand.berechnen(von, bis)
    return render(
        request,
        "stammdaten/leerstand.html",
        {"zeilen": zeilen, "summe": leerstand.summe(zeilen), "von": von, "bis": bis},
    )


@login_required
def datenstand_seite(request: HttpRequest) -> HttpResponse:
    """Fehlende Pflichtfelder je Bereich; jede Lücke führt mit einem Klick zur Eingabe."""
    return render(
        request, "stammdaten/datenstand.html", {"bereiche": datenstand.ermitteln(_heute())}
    )
