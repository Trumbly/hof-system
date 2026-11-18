"""Seiten des Abgleichs: Rückstände, Mietkonto je Vertrag mit Handzahlung, Klärfall mit den
Aktionen Zuordnen, Aufteilen und Regel anlegen, Zahlerregeln.

Gespeichert wird über ``services``; die Seiten prüfen nur die Eingaben.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from apps.abgleich import services
from apps.abgleich.forms import (
    AnteilFormSet,
    GuthabenForm,
    HandzahlungForm,
    RegelAusKlaerfallForm,
    StornoForm,
    ZahlerregelForm,
    ZuordnenForm,
)
from apps.abgleich.models import Klaerfall, Zahlerregel, Zuordnung
from apps.kassenbuch import services as kassenbuch
from apps.kassenbuch.models import Zuordnungsregel
from apps.soll.models import Sollposten
from apps.stammdaten.models import Vertrag


@login_required
@require_GET
def rueckstaende(request: HttpRequest) -> HttpResponse:
    """Rückstände je Vertrag mit Alter und Summe (H3.11)."""
    stand = services.rueckstaende()
    return render(request, "abgleich/rueckstaende.html", {"stand": stand})


@login_required
@require_http_methods(["GET", "POST"])
def mietkonto(request: HttpRequest, pk: int) -> HttpResponse:
    """Mietkonto eines Vertrags; POST storniert eine Zuordnung oder Handzahlung."""
    vertrag = get_object_or_404(Vertrag.objects.prefetch_related("einheiten"), pk=pk)
    storno = StornoForm(request.POST or None)
    if request.method == "POST" and storno.is_valid():
        grund = storno.cleaned_data["grund"]
        try:
            if request.POST.get("zuordnung"):
                zuordnung = get_object_or_404(Zuordnung, pk=request.POST["zuordnung"])
                services.zuordnung_stornieren(zuordnung, grund)
                messages.success(request, f"Zuordnung #{zuordnung.pk} ist storniert.")
            elif request.POST.get("handzahlung"):
                handzahlung = get_object_or_404(
                    vertrag.handzahlungen, pk=request.POST["handzahlung"]
                )
                services.handzahlung_stornieren(handzahlung, grund)
                messages.success(request, f"Handzahlung #{handzahlung.pk} ist storniert.")
        except services.AbgleichFehler as fehler:
            messages.error(request, str(fehler))
        return redirect("abgleich:mietkonto", pk=vertrag.pk)
    return render(
        request,
        "abgleich/mietkonto.html",
        {"konto": services.mietkonto(vertrag), "vertrag": vertrag, "storno": StornoForm()},
    )


@login_required
@require_http_methods(["GET", "POST"])
def handzahlung(request: HttpRequest, pk: int) -> HttpResponse:
    """Handbuchung (H3.7): Barzahlung, fremdes Konto, Erstattung. Grund und Beleg Pflicht."""
    vertrag = get_object_or_404(Vertrag, pk=pk)
    form = HandzahlungForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        werte = form.cleaned_data
        try:
            neu = services.handzahlung_erfassen(
                vertrag,
                werte["art"],
                datum=werte["datum"],
                betrag=werte["betrag"],
                grund=werte["grund"],
                beleg=werte["beleg"],
            )
        except services.AbgleichFehler as fehler:
            form.add_error(None, str(fehler))
        else:
            messages.success(request, f"Handzahlung #{neu.pk} ist erfasst.")
            return redirect("abgleich:mietkonto", pk=vertrag.pk)
    return render(request, "abgleich/handzahlung.html", {"vertrag": vertrag, "form": form})


# --- Klärfall ------------------------------------------------------------------------


def _klaerfall_kontext(klaerfall: Klaerfall) -> dict[str, Any]:
    buchung = klaerfall.buchung
    vorschlag = klaerfall.vorschlag or {}
    posten = {
        p.pk: p
        for p in Sollposten.objects.filter(
            pk__in=[i for i, _ in vorschlag.get("posten", [])]
        ).select_related("vertrag")
    }
    return {
        "klaerfall": klaerfall,
        "buchung": buchung,
        "umsatz": buchung.umsatz,
        "offen": services.offener_betrag(buchung),
        "posten_arbeit": services.klaerfall_posten(klaerfall),
        "vorschlag_vertrag": Vertrag.objects.filter(pk=vorschlag.get("vertrag")).first(),
        "vorschlag_posten": [
            (posten[i], Decimal(b)) for i, b in vorschlag.get("posten", []) if i in posten
        ],
        "vorschlag_guthaben": Decimal(vorschlag.get("guthaben") or "0"),
        "alternativen": Vertrag.objects.filter(pk__in=vorschlag.get("alternativen", [])),
        "zuordnungen": list(buchung.zuordnungen.select_related("sollposten__vertrag")),
    }


def _klaerfall(pk: int) -> Klaerfall:
    return get_object_or_404(
        Klaerfall.objects.select_related("buchung__umsatz__konto", "buchung__regel"), pk=pk
    )


def _fertig(request: HttpRequest, text: str) -> HttpResponse:
    messages.success(request, text)
    return redirect("kern:arbeitslisten")


@login_required
@require_GET
def klaerfall(request: HttpRequest, pk: int) -> HttpResponse:
    fall = _klaerfall(pk)
    return render(request, "abgleich/klaerfall.html", _klaerfall_kontext(fall))


@login_required
@require_http_methods(["GET", "POST"])
def zuordnen(request: HttpRequest, pk: int) -> HttpResponse:
    """Aktion „Zuordnen“: offener Betrag auf einen Vertrag, älteste Posten zuerst."""
    fall = _klaerfall(pk)
    initial = {"vertrag": (fall.vorschlag or {}).get("vertrag")}
    form = ZuordnenForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            services.klaerfall_zuordnen(
                fall,
                form.cleaned_data["vertrag"],
                rest_als_guthaben=form.cleaned_data["rest_als_guthaben"],
            )
        except services.AbgleichFehler as fehler:
            form.add_error(None, str(fehler))
        else:
            return _fertig(request, f"Klärfall #{fall.pk}: zugeordnet.")
    return render(request, "abgleich/zuordnen.html", {**_klaerfall_kontext(fall), "form": form})


@login_required
@require_http_methods(["GET", "POST"])
def aufteilen(request: HttpRequest, pk: int) -> HttpResponse:
    """Aktion „Aufteilen“: Beträge auf einzelne Posten, ein Rest als Guthaben."""
    fall = _klaerfall(pk)
    vorschlag = fall.vorschlag or {}
    initial = [{"posten": i, "betrag": Decimal(b)} for i, b in vorschlag.get("posten", [])]
    formset = AnteilFormSet(request.POST or None, initial=initial or None, prefix="anteile")
    guthaben = GuthabenForm(
        request.POST or None,
        initial={
            "guthaben": Decimal(vorschlag.get("guthaben") or "0"),
            "guthaben_vertrag": vorschlag.get("vertrag"),
        },
    )
    if request.method == "POST" and formset.is_valid() and guthaben.is_valid():
        anteile = [
            services.Anteil(f["posten"], f["betrag"])
            for f in formset.cleaned_data
            if f and f.get("posten")
        ]
        try:
            services.klaerfall_aufteilen(
                fall,
                anteile,
                guthaben=guthaben.cleaned_data["guthaben"] or Decimal("0.00"),
                guthaben_vertrag=guthaben.cleaned_data["guthaben_vertrag"],
            )
        except services.AbgleichFehler as fehler:
            guthaben.add_error(None, str(fehler))
        else:
            return _fertig(request, f"Klärfall #{fall.pk}: aufgeteilt.")
    return render(
        request,
        "abgleich/aufteilen.html",
        {**_klaerfall_kontext(fall), "formset": formset, "guthaben_form": guthaben},
    )


@login_required
@require_http_methods(["GET", "POST"])
def regel_aus_klaerfall(request: HttpRequest, pk: int) -> HttpResponse:
    """Aktion „Regel anlegen“: Zahlerregel für diesen Zahler; danach Abgleich der Buchung."""
    fall = _klaerfall(pk)
    initial = {
        "vertrag": (fall.vorschlag or {}).get("vertrag"),
        "kategorie": fall.buchung.kategorie_id,
        "objekt": fall.buchung.objekt_id,
    }
    form = RegelAusKlaerfallForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        werte = form.cleaned_data
        try:
            ergebnis = services.klaerfall_regel_anlegen(
                fall,
                vertrag=werte["vertrag"],
                vertraege=list(werte["vertraege"]),
                betrag=werte["betrag"],
                kategorie=werte["kategorie"],
                objekt=werte["objekt"],
            )
        except (services.AbgleichFehler, kassenbuch.KassenbuchFehler) as fehler:
            form.add_error(None, str(fehler))
        else:
            if ergebnis.gebucht:
                return _fertig(
                    request, f"Zahlerregel #{ergebnis.zahlerregel.pk} angelegt, Zahlung zugeordnet."
                )
            messages.warning(
                request,
                f"Zahlerregel #{ergebnis.zahlerregel.pk} angelegt. Die Zahlung passt nicht genau; "
                "bitte zuordnen oder aufteilen.",
            )
            return redirect("abgleich:klaerfall", pk=fall.pk)
    return render(request, "abgleich/regel.html", {**_klaerfall_kontext(fall), "form": form})


# --- Zahlerregeln (H1.4) -------------------------------------------------------------


@login_required
@require_GET
def zahlerregeln(request: HttpRequest) -> HttpResponse:
    regeln = (
        Zahlerregel.objects.select_related("regel", "vertrag")
        .prefetch_related("sammelvertraege", "vertrag__einheiten")
        .order_by("regel__bezeichnung", "pk")
    )
    ohne = (
        Zuordnungsregel.objects.filter(
            aktiv=True,
            zahlerregel__isnull=True,
            kategorie__art="einnahme",
            kategorie__wohnraum=False,
        )
        .exclude(richtung="ausgang")
        .order_by("bezeichnung")
    )
    return render(request, "abgleich/zahlerregeln.html", {"regeln": regeln, "ohne": ohne})


@login_required
@require_http_methods(["GET", "POST"])
def zahlerregel(request: HttpRequest, regel: int) -> HttpResponse:
    """Zahlerregel einer Zuordnungsregel anlegen oder ändern."""
    zuordnungsregel = get_object_or_404(Zuordnungsregel, pk=regel)
    vorhanden = Zahlerregel.objects.filter(regel=zuordnungsregel).first()
    initial: dict[str, Any] = {}
    if vorhanden is not None:
        initial = {
            "vertrag": vorhanden.vertrag_id,
            "vertraege": list(vorhanden.sammelvertraege.all()),
            "betrag": vorhanden.betrag,
            "nicht_vertragsbezogen": vorhanden.nicht_vertragsbezogen,
        }
    form = ZahlerregelForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        werte = form.cleaned_data
        try:
            services.zahlerregel_setzen(
                zuordnungsregel,
                vertrag=werte["vertrag"],
                vertraege=list(werte["vertraege"]),
                betrag=werte["betrag"],
                nicht_vertragsbezogen=werte["nicht_vertragsbezogen"],
            )
        except services.AbgleichFehler as fehler:
            form.add_error(None, str(fehler))
        else:
            messages.success(request, f"Zahlerregel zu Regel #{zuordnungsregel.pk} gespeichert.")
            return redirect("abgleich:zahlerregeln")
    return render(
        request,
        "abgleich/zahlerregel.html",
        {"regel": zuordnungsregel, "form": form, "vorhanden": vorhanden},
    )
