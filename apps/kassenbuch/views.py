"""Seiten des Kassenbuchs: Buchungen mit Filter, Umsatz einordnen (zuordnen, aufteilen, Regel
anlegen), Regeln mit rückwirkender Anwendung, Monatsabschlüsse und Monatsbericht je Objekt."""

from __future__ import annotations

import datetime
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, QuerySet, Sum
from django.http import FileResponse, HttpRequest, HttpResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.bank.models import Bankkonto, Bankumsatz
from apps.kassenbuch import auswertung, pdf, services
from apps.kassenbuch.forms import (
    BerichtForm,
    BuchungFilter,
    RegelForm,
    TeilFormSet,
    ZuordnenForm,
    monat_aus_text,
)
from apps.kassenbuch.models import Buchung, Monatsabschluss, Monatsbericht, Zuordnungsregel
from apps.kern import htmx
from apps.kern.models import Arbeitsposten, Mandant, Objekt

BUCHUNGEN_JE_SEITE = 50
VORSCHAU_ZEILEN = 50
PREFIX_TEILE = "teile"


# --- Kassenbuch ----------------------------------------------------------------------


def _betrag_suche(text: str) -> Decimal | None:
    """„627,34“ oder „1.234,56“ als Betrag für die Suche; sonst None."""
    roh = text.strip().replace("€", "").strip()
    if not re.fullmatch(r"\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?", roh):
        return None
    if "," in roh:
        roh = roh.replace(".", "").replace(",", ".")
    try:
        return Decimal(roh).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def buchungen_filtern(
    filter_form: BuchungFilter, heute: datetime.date
) -> tuple[QuerySet[Buchung], datetime.date, datetime.date]:
    """Die Buchungen zum Filter, neueste zuerst, mit Zeitraum (Standard: Jahr bis heute)."""
    abfrage = Buchung.objects.select_related(
        "kategorie", "objekt", "mandant", "umsatz__konto", "storno_von"
    ).order_by("-datum", "-pk")
    von, bis = datetime.date(heute.year, 1, 1), auswertung.monatsende(heute)
    if filter_form.is_valid():
        werte: dict[str, Any] = filter_form.cleaned_data
        if werte["monat"]:
            von, bis = werte["monat"], auswertung.monatsende(werte["monat"])
        von = werte["von"] or von
        bis = werte["bis"] or bis
        for feld in ("mandant", "objekt", "kategorie"):
            if werte[feld]:
                abfrage = abfrage.filter(**{feld: werte[feld]})
        if werte["art"]:
            abfrage = abfrage.filter(kategorie__art=werte["art"])
        if werte["konto"]:
            abfrage = abfrage.filter(umsatz__konto=werte["konto"])
        if werte["ohne_storno"]:
            abfrage = abfrage.filter(storno_von__isnull=True, storniert_durch__isnull=True)
        if suche := (werte["q"] or "").strip():
            treffer = (
                Q(umsatz__gegenpartei__icontains=suche)
                | Q(umsatz__verwendungszweck__icontains=suche)
                | Q(kategorie__bezeichnung__icontains=suche)
                | Q(objekt__kuerzel__iexact=suche)
            )
            if (betrag := _betrag_suche(suche)) is not None:
                treffer |= Q(betrag=betrag)
            abfrage = abfrage.filter(treffer)
    return abfrage.filter(datum__gte=von, datum__lte=bis), von, bis


@login_required
@require_GET
def buchungen(request: HttpRequest) -> HttpResponse:
    """Kassenbuch (Review 2, B11): Suche und Monat oben, weitere Filter zugeklappt; Spalten
    Gegenpartei, Zweck und Konto mit Namen; 50 Zeilen, dann „mehr laden“ per HTMX."""
    filter_form = BuchungFilter(request.GET or None)
    abfrage, von, bis = buchungen_filtern(filter_form, timezone.localdate())
    ab = request.GET.get("ab", "0")
    ab_zahl = int(ab) if ab.isdigit() else 0
    zeilen = list(abfrage[ab_zahl : ab_zahl + BUCHUNGEN_JE_SEITE + 1])
    weiter = len(zeilen) > BUCHUNGEN_JE_SEITE
    zeilen = zeilen[:BUCHUNGEN_JE_SEITE]
    parameter = request.GET.copy()
    parameter.pop("ab", None)
    parameter.pop("seite", None)
    if weiter:
        parameter["ab"] = str(ab_zahl + BUCHUNGEN_JE_SEITE)
    teil = {
        "zeilen": zeilen,
        "weiter": weiter,
        "mehr_url": f"?{parameter.urlencode()}" if weiter else "",
    }
    if htmx.ist_htmx(request) and ab_zahl:
        return render(request, "kassenbuch/_buchungen_zeilen.html", teil)
    summen = abfrage.aggregate(
        anzahl=Count("pk"),
        eingang=Sum("betrag", filter=Q(richtung="eingang")),
        ausgang=Sum("betrag", filter=Q(richtung="ausgang")),
    )
    return render(
        request,
        "kassenbuch/buchungen.html",
        {
            **teil,
            "filter": filter_form,
            "von": von,
            "bis": bis,
            "zeitraum_text": f"{von:%d.%m.%Y} bis {bis:%d.%m.%Y}",
            "anzahl": summen["anzahl"],
            "summe_eingang": summen["eingang"] or Decimal("0"),
            "summe_ausgang": summen["ausgang"] or Decimal("0"),
            "entnahmen": auswertung.entnahmen_je_mandant(von, bis),
            "offen": services.nicht_gebuchte_umsaetze().count(),
        },
    )


# --- Umsatz einordnen ----------------------------------------------------------------


def _offener_posten(umsatz: Bankumsatz) -> Arbeitsposten | None:
    return services.einordnen_posten_offen(umsatz)


def _vorschlag(umsatz: Bankumsatz) -> dict[str, Any]:
    """Startwerte für Zuordnen: die beste passende Regel oder die Buchung des Eingangs, den
    ein Ausgang zurückbucht; sonst nur das Leistungsjahr."""
    initial: dict[str, Any] = {"leistungsjahr": umsatz.buchungsdatum.year}
    einordnung = services.einordnen(umsatz, services.aktive_regeln())
    regel = einordnung.regel or (einordnung.gleichauf[0] if einordnung.gleichauf else None)
    if regel is not None:
        initial.update(kategorie=regel.kategorie, objekt=regel.objekt, mandant=regel.mandant)
        return initial
    zurueck = services.rueckbuchung_zu(umsatz)
    if zurueck is not None:
        vorlage = services.gueltige_buchungen().filter(umsatz=zurueck).first()
        if vorlage is not None:
            initial.update(
                kategorie=vorlage.kategorie, objekt=vorlage.objekt, mandant=vorlage.mandant
            )
    return initial


def _umsatz_kontext(umsatz: Bankumsatz) -> dict[str, Any]:
    buchungen = list(
        umsatz.buchungen.select_related("kategorie", "objekt", "mandant", "regel").order_by("pk")
    )
    gueltig = [b for b in buchungen if not b.ist_storno and not hasattr(b, "storniert_durch")]
    einordnung = services.einordnen(umsatz, services.aktive_regeln())
    return {
        "umsatz": umsatz,
        "buchungen": buchungen,
        "gebucht": sum((b.betrag for b in gueltig), Decimal("0")) == umsatz.betrag,
        "hat_gueltige": bool(gueltig),
        "posten": _offener_posten(umsatz),
        "einordnung": einordnung,
        "rueckbuchung_zu": services.rueckbuchung_zu(umsatz),
        "gesperrt": services.ist_gesperrt(umsatz.konto.mandant_id, umsatz.buchungsdatum),
    }


def _nach_dem_buchen(request: HttpRequest, umsatz: Bankumsatz, hatte_posten: bool) -> HttpResponse:
    messages.success(request, f"Umsatz #{umsatz.pk} ist gebucht.")
    if hatte_posten:
        return redirect("kern:arbeitslisten")
    return redirect("kassenbuch:umsatz", pk=umsatz.pk)


@login_required
@require_GET
def umsatz(request: HttpRequest, pk: int) -> HttpResponse:
    eintrag = get_object_or_404(Bankumsatz.objects.select_related("konto"), pk=pk)
    return render(request, "kassenbuch/umsatz.html", _umsatz_kontext(eintrag))


@login_required
@require_http_methods(["GET", "POST"])
def zuordnen(request: HttpRequest, pk: int) -> HttpResponse:
    """Aktion „Zuordnen“: ganzer Umsatz auf eine Kategorie, ein Objekt, einen Mandanten."""
    eintrag = get_object_or_404(Bankumsatz.objects.select_related("konto"), pk=pk)
    form = ZuordnenForm(request.POST or None, initial=_vorschlag(eintrag))
    if request.method == "POST" and form.is_valid():
        werte = form.cleaned_data
        hatte_posten = _offener_posten(eintrag) is not None
        teil = services.Teil(
            betrag=eintrag.betrag,
            kategorie=werte["kategorie"],
            objekt=werte["objekt"],
            mandant=werte["mandant"],
            leistungsjahr=werte["leistungsjahr"],
            umlagefaehiger_anteil=werte["umlagefaehiger_anteil"],
        )
        try:
            services.umsatz_umbuchen(eintrag, [teil])
        except services.KassenbuchFehler as fehler:
            form.add_error(None, str(fehler))
        else:
            return _nach_dem_buchen(request, eintrag, hatte_posten)
    return render(request, "kassenbuch/zuordnen.html", {**_umsatz_kontext(eintrag), "form": form})


def _teile_daten(daten: QueryDict, mehr: bool) -> QueryDict:
    """Formulardaten mit einer Zeile mehr (Knopf „weitere Zeile“ ohne JavaScript)."""
    kopie = daten.copy()
    if mehr:
        schluessel = f"{PREFIX_TEILE}-TOTAL_FORMS"
        kopie[schluessel] = str(int(kopie.get(schluessel, "2")) + 1)
    return kopie


@login_required
@require_http_methods(["GET", "POST"])
def aufteilen(request: HttpRequest, pk: int) -> HttpResponse:
    """Aktion „Aufteilen“: ein Umsatz auf mehrere Kategorien oder Objekte (Summe = Umsatz)."""
    eintrag = get_object_or_404(Bankumsatz.objects.select_related("konto"), pk=pk)
    vorschlag = _vorschlag(eintrag)
    if request.method == "POST":
        mehr = "mehr" in request.POST
        formset = TeilFormSet(_teile_daten(request.POST, mehr), prefix=PREFIX_TEILE)
        if not mehr and formset.is_valid():
            hatte_posten = _offener_posten(eintrag) is not None
            teile = [
                services.Teil(
                    betrag=f["betrag"],
                    kategorie=f["kategorie"],
                    objekt=f["objekt"],
                    mandant=f["mandant"],
                    leistungsjahr=f["leistungsjahr"],
                    umlagefaehiger_anteil=f["umlagefaehiger_anteil"],
                )
                for f in formset.cleaned_data
                if f
            ]
            try:
                services.umsatz_umbuchen(eintrag, teile)
            except services.KassenbuchFehler as fehler:
                formset._non_form_errors = formset.error_class([str(fehler)])
            else:
                return _nach_dem_buchen(request, eintrag, hatte_posten)
    else:
        formset = TeilFormSet(prefix=PREFIX_TEILE, initial=[vorschlag, vorschlag])
    return render(
        request,
        "kassenbuch/aufteilen.html",
        {**_umsatz_kontext(eintrag), "formset": formset},
    )


@login_required
@require_GET
def aufteilen_zeile(request: HttpRequest, pk: int) -> HttpResponse:
    """HTMX: eine weitere Zeile im Formular „Aufteilen“ (und der Zähler der Zeilen)."""
    eintrag = get_object_or_404(Bankumsatz, pk=pk)
    try:
        nummer = max(2, min(int(request.GET.get("nummer", "2")), 50))
    except ValueError:
        nummer = 2
    formset = TeilFormSet(prefix=PREFIX_TEILE)
    form = formset.form(prefix=f"{PREFIX_TEILE}-{nummer}", initial=_vorschlag(eintrag))
    return render(
        request,
        "kassenbuch/_teil_zeile.html",
        {"form": form, "nummer": nummer, "umsatz": eintrag, "naechste": nummer + 1},
    )


@login_required
@require_POST
def stornieren(request: HttpRequest, pk: int) -> HttpResponse:
    """Storniert die Buchung eines Umsatzes; er steht danach wieder in der Arbeitsliste."""
    eintrag = get_object_or_404(Bankumsatz, pk=pk)
    try:
        stornos = services.umsatz_stornieren(eintrag)
    except services.KassenbuchFehler as fehler:
        messages.error(request, f"Nicht storniert: {fehler}")
    else:
        messages.success(
            request,
            f"Umsatz #{eintrag.pk}: {len(stornos)} Buchungen storniert "
            f"(Datum {stornos[0].datum:%d.%m.%Y}).",
        )
    return redirect("kassenbuch:umsatz", pk=eintrag.pk)


# --- Regeln --------------------------------------------------------------------------


@login_required
@require_GET
def regeln(request: HttpRequest) -> HttpResponse:
    liste = (
        Zuordnungsregel.objects.select_related("kategorie", "objekt", "mandant", "konto")
        .annotate(anzahl=Count("buchungen", filter=Q(buchungen__storno_von__isnull=True)))
        .order_by("-aktiv", "-prioritaet", "pk")
    )
    return render(request, "kassenbuch/regeln.html", {"regeln": liste})


def _regel_speichern(form: RegelForm, regel: Zuordnungsregel | None) -> Zuordnungsregel:
    werte = {k: v for k, v in form.cleaned_data.items() if k in services.REGEL_FELDER}
    if regel is None:
        werte.pop("aktiv", None)
        bezeichnung = werte.pop("bezeichnung")
        return services.regel_anlegen(bezeichnung, **werte)
    return services.regel_aendern(regel, **werte)


@login_required
@require_http_methods(["GET", "POST"])
def regel_bearbeiten(request: HttpRequest, pk: int | None = None) -> HttpResponse:
    regel = get_object_or_404(Zuordnungsregel, pk=pk) if pk is not None else None
    form = RegelForm(request.POST or None, instance=regel)
    if request.method == "POST" and form.is_valid():
        try:
            gespeichert = _regel_speichern(form, regel)
        except services.KassenbuchFehler as fehler:
            form.add_error(None, str(fehler))
        else:
            messages.success(request, f"Regel #{gespeichert.pk} gespeichert.")
            return redirect("kassenbuch:regel_anwenden", pk=gespeichert.pk)
    return render(request, "kassenbuch/regel_form.html", {"form": form, "regel": regel})


@login_required
@require_http_methods(["GET", "POST"])
def regel_aus_umsatz(request: HttpRequest, pk: int) -> HttpResponse:
    """Aktion „Regel anlegen“: Regel mit IBAN, Gegenpartei und Richtung des Umsatzes."""
    eintrag = get_object_or_404(Bankumsatz.objects.select_related("konto"), pk=pk)
    vorschlag = _vorschlag(eintrag)
    initial = {
        "bezeichnung": (eintrag.gegenpartei or f"Regel zu Umsatz #{eintrag.pk}")[:200],
        "gegen_iban": eintrag.gegen_iban,
        "gegenpartei": "" if eintrag.gegen_iban else eintrag.gegenpartei[:200],
        "richtung": eintrag.richtung,
        "prioritaet": 10,
        "aktiv": True,
        **{k: v for k, v in vorschlag.items() if k in ("kategorie", "objekt", "mandant")},
    }
    form = RegelForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            regel = _regel_speichern(form, None)
        except services.KassenbuchFehler as fehler:
            form.add_error(None, str(fehler))
        else:
            messages.success(request, f"Regel #{regel.pk} angelegt. Jetzt rückwirkend anwenden?")
            return redirect("kassenbuch:regel_anwenden", pk=regel.pk)
    return render(
        request,
        "kassenbuch/regel_form.html",
        {"form": form, "regel": None, "umsatz": eintrag},
    )


@login_required
@require_http_methods(["GET", "POST"])
def regel_anwenden(request: HttpRequest, pk: int) -> HttpResponse:
    """Rückwirkende Anwendung: erst Zähler und Liste (Vorschau), dann mit Klick buchen."""
    regel = get_object_or_404(
        Zuordnungsregel.objects.select_related("kategorie", "objekt", "mandant"), pk=pk
    )
    if request.method == "POST":
        try:
            erwartet = int(request.POST.get("erwartet", ""))
        except ValueError:
            erwartet = -1
        try:
            gebucht = services.regel_rueckwirkend_anwenden(regel, erwartet=erwartet)
        except services.VorschauVeraltet as fehler:
            messages.warning(request, str(fehler))
        except services.KassenbuchFehler as fehler:
            messages.error(request, f"Nicht angewandt: {fehler}")
        else:
            messages.success(request, f"Regel #{regel.pk}: {len(gebucht)} Umsätze gebucht.")
            return redirect("kassenbuch:regeln")
    vorschau = services.regel_vorschau(regel)
    return render(
        request,
        "kassenbuch/regel_anwenden.html",
        {
            "regel": regel,
            "vorschau": vorschau,
            "zeilen": vorschau.zu_buchen[:VORSCHAU_ZEILEN],
            "mehr": max(0, len(vorschau.zu_buchen) - VORSCHAU_ZEILEN),
        },
    )


# --- Monatsabschluss -----------------------------------------------------------------


@login_required
@require_GET
def abschluesse(request: HttpRequest) -> HttpResponse:
    """Abschlüsse je Mandant; für den ersten offenen Monat die Prüfung von jetzt."""
    gruppen = []
    for mandant in services.mandanten_mit_konten():
        liste = list(Monatsabschluss.objects.filter(mandant=mandant).order_by("-monat")[:24])
        naechster = services.naechster_abschlussmonat(mandant, None)
        pruefung = (
            services.abschluss_pruefen(mandant, naechster)
            if naechster is not None and naechster <= auswertung.vormonat(timezone.localdate())
            else None
        )
        gruppen.append({"mandant": mandant, "abschluesse": liste, "pruefung": pruefung})
    # „abschlussgruppen“: ``gruppen`` belegt die Navigation der neuen Gestaltung (base_neu).
    return render(
        request,
        "kassenbuch/abschluesse.html",
        {
            "abschlussgruppen": gruppen,
            # Kontonamen statt „Konto #3“ (Review 2, B11)
            "kontonamen": dict(Bankkonto.objects.values_list("pk", "bezeichnung")),
        },
    )


def _nach_abschluss(request: HttpRequest, ziel: str) -> HttpResponse:
    """Aus dem Dialog (HTMX) per HX-Redirect, sonst normal umleiten; die Meldung bleibt."""
    if htmx.ist_htmx(request):
        antwort = HttpResponse("")
        antwort["HX-Redirect"] = ziel
        return antwort
    return redirect(ziel)


@login_required
@require_http_methods(["GET", "POST"])
def abschluss_jetzt(request: HttpRequest, mandant: int, jahr: int, monat: int) -> HttpResponse:
    """Knopf „Monat jetzt abschließen“ für einen Monat, der bereit ist (Review 2, A11).

    GET liefert den Bestätigungsdialog (R2-9, statt ``confirm()`` des Browsers), POST schließt
    ab. Geht nur für den nächsten offenen Monat und nur, wenn die Prüfung von jetzt stimmt;
    sonst sagt die Seite, was fehlt. Abgeschlossen ist abgeschlossen (Sperre).
    """
    eintrag = get_object_or_404(Mandant, pk=mandant)
    try:
        erster = datetime.date(jahr, monat, 1)
    except ValueError:
        return redirect("kassenbuch:abschluesse")
    name = eintrag.bezeichnung or eintrag.kuerzel
    if request.method == "GET":
        return render(
            request,
            "bausteine/dialog_formular.html",
            {
                "titel": f"Monat {erster:%m/%Y} für {name} abschließen?",
                "form": forms.Form(),
                "url": request.path,
                "hinweis": "Danach ist der Monat gesperrt; geändert wird nur noch per "
                "Stornobuchung im offenen Monat.",
                "knopf": "Jetzt abschließen",
                "gefahr": True,
            },
        )
    if services.naechster_abschlussmonat(eintrag, None) != erster:
        messages.error(
            request,
            f"{erster:%m/%Y} ist für {name} nicht der nächste offene Monat; abgeschlossen wird "
            "der Reihe nach.",
        )
        return _nach_abschluss(request, reverse("kassenbuch:abschluesse"))
    if erster > auswertung.vormonat(timezone.localdate()):
        messages.error(request, f"{erster:%m/%Y} ist noch nicht vorbei.")
        return _nach_abschluss(request, reverse("kassenbuch:abschluesse"))
    ergebnis = services.monat_abschliessen(eintrag, erster)
    if ergebnis.abschluss.abgeschlossen:
        messages.success(
            request,
            f"Monat {erster:%m/%Y} für {name} abgeschlossen. Geändert wird ab jetzt nur noch "
            "per Stornobuchung im offenen Monat.",
        )
    else:
        messages.warning(
            request,
            f"Monat {erster:%m/%Y} für {name} ist nicht bereit; was fehlt, steht unten.",
        )
    return _nach_abschluss(request, f"{reverse('kassenbuch:abschluesse')}#mandant-{eintrag.pk}")


# --- Monatsbericht (H10.2) -----------------------------------------------------------


@login_required
@require_GET
def bericht(request: HttpRequest) -> HttpResponse:
    """Monatsbericht je Objekt: Einnahmen, Kosten, Ergebnis, Entnahmen, Vorjahresmonat."""
    vormonat = auswertung.vormonat(timezone.localdate())
    daten = request.GET.copy()
    daten.setdefault("monat", f"{vormonat:%Y-%m}")
    if "objekt" not in daten:
        erstes = Objekt.objects.filter(aktiv=True).order_by("kuerzel").first()
        if erstes is not None:
            daten["objekt"] = str(erstes.pk)
    form = BerichtForm(daten)
    inhalt = None
    gespeichert = None
    if form.is_valid():
        inhalt = auswertung.bericht(form.cleaned_data["objekt"], form.cleaned_data["monat"])
        gespeichert = Monatsbericht.objects.filter(objekt=inhalt.objekt, monat=inhalt.monat).first()
    return render(
        request,
        "kassenbuch/bericht.html",
        {
            "form": form,
            "bericht": inhalt,
            "gespeichert": gespeichert,
            "pdf_verfuegbar": pdf.verfuegbar(),
            "letzte": Monatsbericht.objects.select_related("objekt").order_by("-monat", "objekt")[
                :12
            ],
        },
    )


@login_required
@require_GET
def bericht_pdf(request: HttpRequest, objekt: int, jahr: int, monat: int) -> HttpResponse:
    """Das gespeicherte PDF; ohne gespeichertes PDF wird es jetzt gesetzt."""
    eintrag = get_object_or_404(Objekt, pk=objekt)
    erster = monat_aus_text(f"{jahr:04d}-{monat:02d}")
    if erster is None:
        return HttpResponse("Unbekannter Monat.", status=404)
    gespeichert = Monatsbericht.objects.filter(objekt=eintrag, monat=erster).first()
    daten = auswertung.bericht(eintrag, erster)
    if gespeichert is not None and gespeichert.datei:
        return FileResponse(
            gespeichert.datei.open("rb"),
            content_type="application/pdf",
            filename=daten.dateiname,
        )
    try:
        inhalt = pdf.html_zu_pdf(services.bericht_html(daten))
    except pdf.PdfNichtVerfuegbar as fehler:
        return HttpResponse(
            f"Das PDF lässt sich hier nicht setzen: {fehler}",
            status=503,
            content_type="text/plain; charset=utf-8",
        )
    antwort = HttpResponse(inhalt, content_type="application/pdf")
    antwort["Content-Disposition"] = f'inline; filename="{daten.dateiname}"'
    return antwort
