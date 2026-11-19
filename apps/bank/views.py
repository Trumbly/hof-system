"""Seiten der Bank: Konten, Umsätze, Import und die Erneuerung der Bankfreigabe."""

from __future__ import annotations

import datetime
import hashlib
import logging
import secrets
from typing import Any

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from apps.bank import enablebanking, importieren, services
from apps.bank.importformat import FORMATE, VIVID_CSV
from apps.bank.models import Bankabruf, Bankkonto, Bankumsatz
from apps.kern import services as kern_services

logger = logging.getLogger(__name__)

UMSAETZE_JE_SEITE = 100
MAX_IMPORT_BYTES = 5 * 1024 * 1024
SITZUNG_FREIGABE = "bank_freigabe"
SITZUNG_IMPORT = "bank_import"


# --- Konten --------------------------------------------------------------------------


@login_required
@require_GET
def konten(request: HttpRequest) -> HttpResponse:
    """Konten mit Saldo (Stand des letzten erfolgreichen Abrufs), letztem Abruf und Freigabe."""
    liste = Bankkonto.objects.select_related("mandant").order_by("-aktiv", "bezeichnung", "pk")
    zeilen = []
    heute = timezone.localdate()
    for konto in liste:
        freigabe = services.aktuelle_freigabe(konto)
        zeilen.append(
            {
                "konto": konto,
                "saldo": services.saldo_stand(konto),
                "letzter_abruf": konto.abrufe.exclude(quelle=Bankabruf.Quelle.IMPORT).first(),
                "freigabe": freigabe,
                "freigabe_bald": freigabe is not None
                and freigabe.gueltig_bis
                <= heute + datetime.timedelta(days=services.FREIGABE_VORLAUF_TAGE),
                "umsaetze": konto.umsaetze.count(),
            }
        )
    return render(request, "bank/konten.html", {"zeilen": zeilen, "heute": heute})


# --- Umsätze -------------------------------------------------------------------------


class UmsatzFilter(forms.Form):
    konto = forms.ModelChoiceField(
        Bankkonto.objects.order_by("bezeichnung"), required=False, empty_label="alle Konten"
    )
    von = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    bis = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    richtung = forms.ChoiceField(
        choices=[("", "beide Richtungen"), *Bankumsatz.Richtung.choices], required=False
    )
    suche = forms.CharField(required=False, max_length=100, label="Suche")


@login_required
@require_GET
def umsaetze(request: HttpRequest) -> HttpResponse:
    """Umsätze mit Filter nach Konto, Zeitraum, Richtung und Suche in Gegenpartei/Zweck."""
    filter_form = UmsatzFilter(request.GET or None)
    abfrage = Bankumsatz.objects.select_related("konto").order_by("-buchungsdatum", "-pk")
    if filter_form.is_valid():
        werte: dict[str, Any] = filter_form.cleaned_data
        if werte["konto"]:
            abfrage = abfrage.filter(konto=werte["konto"])
        if werte["von"]:
            abfrage = abfrage.filter(buchungsdatum__gte=werte["von"])
        if werte["bis"]:
            abfrage = abfrage.filter(buchungsdatum__lte=werte["bis"])
        if werte["richtung"]:
            abfrage = abfrage.filter(richtung=werte["richtung"])
        if werte["suche"]:
            abfrage = abfrage.filter(
                Q(gegenpartei__icontains=werte["suche"])
                | Q(verwendungszweck__icontains=werte["suche"])
                | Q(gegen_iban__icontains=werte["suche"].replace(" ", ""))
            )
    seite = Paginator(abfrage, UMSAETZE_JE_SEITE).get_page(request.GET.get("seite"))
    parameter = request.GET.copy()
    parameter.pop("seite", None)
    return render(
        request,
        "bank/umsaetze.html",
        {"filter": filter_form, "seite": seite, "parameter": parameter.urlencode()},
    )


# --- Import (H3.3) -------------------------------------------------------------------


class ImportForm(forms.Form):
    konto = forms.ModelChoiceField(Bankkonto.objects.order_by("bezeichnung"))
    format = forms.ChoiceField(choices=[(n, n) for n in sorted(FORMATE)], initial=VIVID_CSV.name)
    datei = forms.FileField(label="CSV-Datei")


@login_required
@require_http_methods(["GET", "POST"])
def import_seite(request: HttpRequest) -> HttpResponse:
    """Datei hochladen, Probelauf mit Zählern ansehen, dann ausführen."""
    bericht = None
    vorgemerkt = request.session.get(SITZUNG_IMPORT)
    form = ImportForm()
    if request.method == "POST" and request.POST.get("schritt") == "ausfuehren":
        if not vorgemerkt or vorgemerkt.get("pruefsumme") != request.POST.get("pruefsumme"):
            messages.error(request, "Bitte die Datei zuerst im Probelauf prüfen.")
            return redirect("bank:import")
        konto = get_object_or_404(Bankkonto, pk=vorgemerkt["konto"])
        lauf = kern_services.lauf_starten("bank_import")
        bericht = importieren.importieren(
            konto,
            vorgemerkt["inhalt"],
            FORMATE[vorgemerkt["format"]],
            ausfuehren=True,
            lauf=lauf,
        )
        kern_services.lauf_beenden(
            lauf,
            gebucht=bericht.neu,
            arbeitsposten=0,
            fehler=sum(bericht.fehlerhaft.values()),
            meldung="; ".join(bericht.zeilen_text()),
        )
        del request.session[SITZUNG_IMPORT]
        messages.success(request, f"Import ausgeführt: {bericht.neu} neue Umsätze.")
        vorgemerkt = None
    elif request.method == "POST":
        form = ImportForm(request.POST, request.FILES)
        if form.is_valid():
            datei = form.cleaned_data["datei"]
            format = FORMATE[form.cleaned_data["format"]]
            if datei.size > MAX_IMPORT_BYTES:
                form.add_error("datei", "Die Datei ist größer als 5 MB.")
            else:
                try:
                    inhalt = datei.read().decode(format.kodierung)
                    bericht = importieren.importieren(
                        form.cleaned_data["konto"], inhalt, format, ausfuehren=False
                    )
                except (UnicodeDecodeError, importieren.ImportFehler) as fehler:
                    meldung = (
                        str(fehler)
                        if isinstance(fehler, importieren.ImportFehler)
                        else "Die Datei hat nicht die erwartete Kodierung."
                    )
                    form.add_error("datei", meldung)
                else:
                    vorgemerkt = {
                        "konto": form.cleaned_data["konto"].pk,
                        "format": format.name,
                        "inhalt": inhalt,
                        "pruefsumme": hashlib.sha256(inhalt.encode()).hexdigest(),
                    }
                    request.session[SITZUNG_IMPORT] = vorgemerkt
    return render(
        request,
        "bank/import.html",
        {"form": form, "bericht": bericht, "vorgemerkt": vorgemerkt},
    )


# --- Bankfreigabe (H3.2) -------------------------------------------------------------


class FreigabeVonHandForm(forms.Form):
    gueltig_bis = forms.DateField(
        label="gültig bis", widget=forms.DateInput(attrs={"type": "date"})
    )


def _redirect_url(request: HttpRequest) -> str:
    return settings.ENABLE_BANKING_REDIRECT_URL or request.build_absolute_uri(
        reverse("bank:freigabe_rueckkehr")
    )


@login_required
@require_http_methods(["GET", "POST"])
def freigabe(request: HttpRequest, pk: int) -> HttpResponse:
    """Freigabe erneuern: bei Enable Banking über die Bank, sonst Ablauf von Hand eintragen."""
    konto = get_object_or_404(Bankkonto, pk=pk)
    von_hand = FreigabeVonHandForm()
    if request.method == "POST":
        if konto.zugang == Bankkonto.Zugang.ENABLE_BANKING:
            state = secrets.token_urlsafe(24)
            request.session[SITZUNG_FREIGABE] = {"state": state, "konto": konto.pk}
            gueltig_bis = timezone.now() + datetime.timedelta(
                days=settings.ENABLE_BANKING_FREIGABE_TAGE
            )
            try:
                url = enablebanking.client().freigabe_starten(
                    bank_name=konto.eb_bank_name or konto.bank,
                    land=settings.ENABLE_BANKING_LAND,
                    redirect_url=_redirect_url(request),
                    state=state,
                    gueltig_bis=gueltig_bis,
                )
            except enablebanking.EnableBankingFehler as fehler:
                messages.error(request, f"Freigabe nicht gestartet: {fehler}")
                return redirect("bank:freigabe", pk=konto.pk)
            return redirect(url)
        von_hand = FreigabeVonHandForm(request.POST)
        if von_hand.is_valid():
            neu = services.bankfreigabe_anlegen(
                konto, gueltig_bis=von_hand.cleaned_data["gueltig_bis"]
            )
            messages.success(request, f"Freigabe #{neu.pk} eingetragen.")
            return redirect("bank:konten")
    return render(
        request,
        "bank/freigabe.html",
        {
            "konto": konto,
            "freigabe": services.aktuelle_freigabe(konto),
            "von_hand": von_hand,
            "enable_banking": konto.zugang == Bankkonto.Zugang.ENABLE_BANKING,
        },
    )


@login_required
@require_GET
def freigabe_rueckkehr(request: HttpRequest) -> HttpResponse:
    """Rückkehr von der Bank: Code gegen Sitzung tauschen, Freigabe festhalten."""
    vorgemerkt = request.session.pop(SITZUNG_FREIGABE, None)
    state = request.GET.get("state", "")
    if not vorgemerkt or not secrets.compare_digest(str(vorgemerkt.get("state")), state):
        messages.error(request, "Die Rückkehr von der Bank passt zu keiner gestarteten Freigabe.")
        return redirect("bank:konten")
    konto = get_object_or_404(Bankkonto, pk=vorgemerkt["konto"])
    code = request.GET.get("code")
    if not code:
        messages.error(request, "Die Bank hat die Freigabe nicht erteilt.")
        return redirect("bank:freigabe", pk=konto.pk)
    try:
        sitzung = enablebanking.client().sitzung_anlegen(code)
    except enablebanking.EnableBankingFehler as fehler:
        messages.error(request, f"Freigabe nicht abgeschlossen: {fehler}")
        return redirect("bank:freigabe", pk=konto.pk)
    passend = [k for k in sitzung.konten if k.iban.replace(" ", "").upper() == konto.iban]
    if sitzung.konten and not passend:
        messages.error(
            request, f"Die Freigabe enthält Konto #{konto.pk} nicht. Bitte erneut freigeben."
        )
        return redirect("bank:freigabe", pk=konto.pk)
    neu = services.bankfreigabe_anlegen(
        konto,
        gueltig_bis=sitzung.gueltig_bis,
        sitzung_id=sitzung.sitzung_id,
        eb_konto_uid=passend[0].uid if passend else None,
    )
    messages.success(request, f"Freigabe #{neu.pk} gilt bis {neu.gueltig_bis:%d.%m.%Y}.")
    return redirect("bank:konten")
