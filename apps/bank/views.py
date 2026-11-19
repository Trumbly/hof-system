"""Seiten der Bank: Konten, Umsätze, Import und die Erneuerung der Bankfreigabe.

Neue Gestaltung (Review 2, C; R2-9): Kontonamen statt IDs, Unternavigation als Reiter,
„Erneut abrufen“ je Konto mit Enable Banking (``services.abruf_starten``), Meldungen mit Link.
"""

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
from django.db import transaction
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.bank import enablebanking, importieren, services
from apps.bank.importformat import FORMATE, VIVID_CSV
from apps.bank.models import Bankabruf, Bankkonto, Bankumsatz
from apps.kern import htmx, zeit
from apps.kern import services as kern_services

logger = logging.getLogger(__name__)

UMSAETZE_JE_SEITE = 100
MAX_IMPORT_BYTES = 5 * 1024 * 1024
SITZUNG_FREIGABE = "bank_freigabe"
SITZUNG_IMPORT = "bank_import"


# --- Konten --------------------------------------------------------------------------


def _konten_zeilen() -> list[dict[str, Any]]:
    liste = Bankkonto.objects.select_related("mandant").order_by("-aktiv", "bezeichnung", "pk")
    zeilen = []
    heute = zeit.heute()
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
                "abrufbar": konto.aktiv and konto.zugang == Bankkonto.Zugang.ENABLE_BANKING,
            }
        )
    return zeilen


@login_required
@require_GET
def konten(request: HttpRequest) -> HttpResponse:
    """Konten mit Saldo (Stand des letzten erfolgreichen Abrufs), letztem Abruf und Freigabe."""
    return render(
        request,
        "bank/konten.html",
        {"zeilen": _konten_zeilen()},
    )


@login_required
@require_POST
@transaction.non_atomic_requests
def abrufen(request: HttpRequest, pk: int) -> HttpResponse:
    """„Erneut abrufen“: ruft ein Konto mit Enable Banking sofort ab (eigener Lauf)."""
    konto = get_object_or_404(Bankkonto, pk=pk)
    try:
        ergebnis = services.abruf_starten(konto)
    except services.BankFehler as fehler:
        if htmx.ist_htmx(request):
            return HttpResponse(htmx.toast(request, str(fehler), art="fehler"))
        messages.error(request, str(fehler))
        return redirect("bank:konten")
    abruf = ergebnis.abruf
    gelungen = abruf is not None and abruf.status == Bankabruf.Status.ERFOLGREICH
    if gelungen and abruf is not None:
        text = f"{konto.bezeichnung} abgerufen: {abruf.neu} neue Umsätze"
        link, link_text = f"{reverse('bank:umsaetze')}?konto={konto.pk}", "Umsätze ansehen"
    else:
        grund = abruf.meldung if abruf is not None else ergebnis.lauf.get_status_display()
        text = f"Abruf für {konto.bezeichnung} gescheitert: {grund}"
        link, link_text = f"{reverse('kern:laeufe')}#lauf-{ergebnis.lauf.pk}", "Lauf ansehen"
    if htmx.ist_htmx(request):
        teil = render_to_string(
            "bank/_konten.html", {"zeilen": _konten_zeilen(), "oob": True}, request=request
        )
        return htmx.erfolg(request, [teil], text, link=link, link_text=link_text)
    stufe = messages.success if gelungen else messages.warning
    stufe(request, format_html('{} · <a href="{}">{}</a>', text, link, link_text))
    return redirect(f"{reverse('bank:konten')}#konto-{konto.pk}")


# --- Umsätze -------------------------------------------------------------------------


class KontoWahl(forms.ModelChoiceField):  # type: ignore[type-arg]
    """Konten mit Namen statt „Bankkonto #3“."""

    def label_from_instance(self, obj: Any) -> str:
        return f"{obj.bezeichnung} ({obj.bank})"


class UmsatzFilter(forms.Form):
    suche = forms.CharField(
        required=False,
        max_length=100,
        label="Suche",
        widget=forms.TextInput(
            attrs={"type": "search", "placeholder": "Gegenpartei, Zweck oder IBAN"}
        ),
    )
    konto = KontoWahl(
        Bankkonto.objects.order_by("bezeichnung"),
        required=False,
        empty_label="alle Konten",
        label="Konto",
    )
    von = forms.DateField(
        required=False, label="von", widget=forms.DateInput(attrs={"type": "date"})
    )
    bis = forms.DateField(
        required=False, label="bis", widget=forms.DateInput(attrs={"type": "date"})
    )
    richtung = forms.ChoiceField(
        choices=[("", "beide Richtungen"), *Bankumsatz.Richtung.choices],
        required=False,
        label="Richtung",
    )


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
        {
            "filter": filter_form,
            "seite": seite,
            "parameter": parameter.urlencode(),
            "gefiltert": any(v for k, v in request.GET.items() if k != "seite"),
        },
    )


# --- Import (H3.3) -------------------------------------------------------------------


class ImportForm(forms.Form):
    konto = KontoWahl(Bankkonto.objects.order_by("bezeichnung"), label="Konto")
    format = forms.ChoiceField(
        label="Format der Datei",
        choices=[(n, n) for n in sorted(FORMATE)],
        initial=VIVID_CSV.name,
    )
    datei = forms.FileField(
        label="CSV-Datei",
        widget=forms.ClearableFileInput(attrs={"accept": ".csv,text/csv"}),
        help_text="Höchstens 5 MB. Der Probelauf zählt nur und ändert nichts.",
    )


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
        messages.success(
            request,
            format_html(
                'Import ausgeführt: {} neue Umsätze in {} · Lauf #{} · <a href="{}">Umsätze '
                "ansehen</a>",
                bericht.neu,
                konto.bezeichnung,
                lauf.pk,
                f"{reverse('bank:umsaetze')}?konto={konto.pk}",
            ),
        )
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
    vorgemerkt_konto = (
        Bankkonto.objects.filter(pk=vorgemerkt["konto"]).first() if vorgemerkt else None
    )
    return render(
        request,
        "bank/import.html",
        {
            "form": form,
            "bericht": bericht,
            "vorgemerkt": vorgemerkt,
            "vorgemerkt_konto": vorgemerkt_konto,
        },
    )


# --- Bankfreigabe (H3.2) -------------------------------------------------------------


class FreigabeVonHandForm(forms.Form):
    gueltig_bis = forms.DateField(
        label="gültig bis",
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text="Bis wann die Freigabe am hof-mac gilt (steht nach der TAN in der Banking-App).",
        error_messages={"required": "Bitte angeben, bis wann die Freigabe gilt."},
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
            # zeit: echt (Ablauf für die Bank, sie prüft gegen ihre Uhr)
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
            messages.success(
                request,
                format_html(
                    'Freigabe für {} eingetragen, gilt bis {} · <a href="{}">Konten ansehen</a>',
                    konto.bezeichnung,
                    f"{neu.gueltig_bis:%d.%m.%Y}",
                    f"{reverse('bank:konten')}#konto-{konto.pk}",
                ),
            )
            return redirect(f"{reverse('bank:konten')}#konto-{konto.pk}")
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
            request,
            f"Die Freigabe enthält das Konto {konto.bezeichnung} nicht. Bitte erneut freigeben.",
        )
        return redirect("bank:freigabe", pk=konto.pk)
    neu = services.bankfreigabe_anlegen(
        konto,
        gueltig_bis=sitzung.gueltig_bis,
        sitzung_id=sitzung.sitzung_id,
        eb_konto_uid=passend[0].uid if passend else None,
    )
    messages.success(
        request, f"Freigabe für {konto.bezeichnung} gilt bis {neu.gueltig_bis:%d.%m.%Y}."
    )
    return redirect(f"{reverse('bank:konten')}#konto-{konto.pk}")
