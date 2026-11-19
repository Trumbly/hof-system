"""Seiten der Anbindungen: Übersicht, Buchungsanfragen, Kennzahlen Wohnraum, ID-Abweichungen,
Vermarktung mit Bildern. Gespeichert wird über ``services``."""

from __future__ import annotations

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.anbindungen import services
from apps.anbindungen.models import Abweichung, Buchungsanfrage, Einheitbild, Kennzahl
from apps.stammdaten.models import Einheit, Kuendigung, Vertrag

ANZAHL_BEHOBEN = 50


@login_required
def uebersicht(request: HttpRequest) -> HttpResponse:
    heute = timezone.localdate()
    return render(
        request,
        "anbindungen/uebersicht.html",
        {
            "anfragen_offen": Buchungsanfrage.objects.filter(
                status__in=services.NOCH_OFFEN
            ).count(),
            "abweichungen_offen": Abweichung.objects.filter(behoben_am__isnull=True).count(),
            "im_katalog": len(services.katalog(heute)),
            "vermarktet": services.vermarktbare_einheiten(heute).count(),
        },
    )


# --- Buchungsanfragen ----------------------------------------------------------------

FILTER = (("offen", "offen und angenommen"), ("alle", "alle"))


@login_required
def anfragen(request: HttpRequest) -> HttpResponse:
    auswahl = request.GET.get("status", "offen")
    liste = Buchungsanfrage.objects.select_related("einheit")
    if auswahl != "alle":
        auswahl = "offen"
        liste = liste.filter(status__in=services.NOCH_OFFEN)
    return render(
        request,
        "anbindungen/anfragen.html",
        {"anfragen": list(liste), "filter": FILTER, "auswahl": auswahl},
    )


class EntscheidungFormular(forms.Form):
    status = forms.ChoiceField(choices=Buchungsanfrage.Status.choices)
    vertrag = forms.ModelChoiceField(queryset=Vertrag.objects.none(), required=False)
    kuendigung = forms.ModelChoiceField(queryset=Kuendigung.objects.none(), required=False)

    def __init__(self, *args: object, anfrage: Buchungsanfrage, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.fields["vertrag"].queryset = anfrage.einheit.vertraege.order_by("-beginn")  # type: ignore[attr-defined]
        self.fields["kuendigung"].queryset = Kuendigung.objects.filter(  # type: ignore[attr-defined]
            vertrag__einheiten=anfrage.einheit
        )


def _kuendigung_angebote(anfrage: Buchungsanfrage) -> dict[str, object]:
    """Was die Seite einer Website-Kündigung anbietet (Review 2, A3): „Kündigung anlegen“ je
    passendem Vertrag (mit Hinweisen, wenn der Absender abweicht), und bereits erfasste
    Kündigungen dieser Einheit zum Verknüpfen."""
    if anfrage.art != Buchungsanfrage.Art.KUENDIGUNG or anfrage.status not in services.NOCH_OFFEN:
        return {}
    vorschlag = services.kuendigung_vorschlag(anfrage)
    anlegen = [
        {
            "vertrag": v,
            "url": reverse("stammdaten:vertrag", args=[v.pk]) + f"?anfrage={anfrage.pk}#kuendigung",
            "abweichungen": services.absender_abweichungen(anfrage, v),
        }
        for v in services.vertraege_zur_kuendigung(anfrage)
    ]
    erfasst = list(
        Kuendigung.objects.filter(vertrag__einheiten=anfrage.einheit)
        .select_related("vertrag")
        .order_by("-zugang_am", "-pk")
    )
    return {"kuendigung_anlegen": anlegen, "kuendigungen_erfasst": erfasst, "vorschlag": vorschlag}


@login_required
def anfrage(request: HttpRequest, pk: int) -> HttpResponse:
    eintrag = get_object_or_404(Buchungsanfrage.objects.select_related("einheit", "vertrag"), pk=pk)
    if request.method == "POST":
        formular = EntscheidungFormular(request.POST, anfrage=eintrag)
        if formular.is_valid():
            try:
                services.buchungsanfrage_entscheiden(
                    eintrag,
                    formular.cleaned_data["status"],
                    vertrag=formular.cleaned_data["vertrag"],
                    kuendigung=formular.cleaned_data["kuendigung"],
                )
            except services.AnbindungFehler as fehler:
                messages.error(request, str(fehler))
            else:
                messages.success(
                    request, f"Buchungsanfrage #{eintrag.pk}: {eintrag.get_status_display()}."
                )
        else:
            messages.error(request, "Die Entscheidung ließ sich nicht lesen. Bitte erneut wählen.")
        return redirect("anbindungen:anfrage", pk=eintrag.pk)
    titel = dict(Buchungsanfrage.Status.choices)
    entscheidungen = list(services.entscheidungen(eintrag))
    if eintrag.art == Buchungsanfrage.Art.KUENDIGUNG:
        # „Erledigt“ gibt es nur über „Kündigung anlegen“ oder das Verknüpfen einer schon
        # erfassten Kündigung (Review 2, A3).
        entscheidungen = [s for s in entscheidungen if s != Buchungsanfrage.Status.ERLEDIGT]
    vertraege = list(eintrag.einheit.vertraege.order_by("-beginn"))
    return render(
        request,
        "anbindungen/anfrage.html",
        {
            "anfrage": eintrag,
            "entscheidungen": [(s, titel[s]) for s in entscheidungen],
            "vertraege": vertraege,
            "buchbar_ab": services.buchbar_ab(eintrag.einheit, timezone.localdate()),
            **_kuendigung_angebote(eintrag),
        },
    )


# --- Kennzahlen Wohnraum -------------------------------------------------------------


@login_required
def kennzahlen(request: HttpRequest) -> HttpResponse:
    ueberblick = services.wohnraum_ueberblick()
    zeilen = (
        list(Kennzahl.objects.filter(datum=ueberblick.stand).order_by("nw_id"))
        if ueberblick.stand
        else []
    )
    return render(
        request,
        "anbindungen/kennzahlen.html",
        {"wohnraum": ueberblick, "zeilen": zeilen},
    )


# --- ID-Abweichungen -----------------------------------------------------------------


@login_required
def abweichungen(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "anbindungen/abweichungen.html",
        {
            "offen": list(Abweichung.objects.filter(behoben_am__isnull=True)),
            "behoben": list(
                Abweichung.objects.filter(behoben_am__isnull=False).order_by("-behoben_am", "-pk")[
                    :ANZAHL_BEHOBEN
                ]
            ),
        },
    )


# --- Vermarktung und Bilder ----------------------------------------------------------


@login_required
def vermarktung(request: HttpRequest) -> HttpResponse:
    """Vorschau des Katalogs: alle vermarkteten Einheiten, ob und ab wann sie buchbar sind."""
    heute = timezone.localdate()
    im_katalog = {e.nw_id: e for e in services.katalog(heute)}
    einheiten = list(
        services.vermarktbare_einheiten(heute).select_related("objekt").order_by("nw_id")
    )
    for einheit in einheiten:
        einheit.katalog_eintrag = im_katalog.get(einheit.nw_id)  # type: ignore[attr-defined]
        einheit.anzahl_bilder = einheit.bilder.filter(aktiv=True).count()  # type: ignore[attr-defined]
    # Review 2, B11: standardmäßig nur buchbare Einheiten; „alle N“ als Schalter.
    alle = request.GET.get("alle") == "1"
    gezeigt = einheiten if alle else [e for e in einheiten if e.nw_id in im_katalog]
    basis = reverse("anbindungen:vermarktung")
    return render(
        request,
        "anbindungen/vermarktung.html",
        {
            "einheiten": gezeigt,
            "alle": alle,
            "anzahl_alle": len(einheiten),
            "im_katalog": len(im_katalog),
            "schalter": [
                {
                    "schluessel": "buchbar",
                    "titel": "buchbar",
                    "url": basis,
                    "anzahl": len(im_katalog),
                },
                {
                    "schluessel": "alle",
                    "titel": "alle",
                    "url": f"{basis}?alle=1",
                    "anzahl": len(einheiten),
                },
            ],
        },
    )


class BildFormular(forms.Form):
    bild = forms.FileField(label="Bild (JPEG, PNG oder WebP)")
    beschreibung = forms.CharField(
        label="Beschreibung", max_length=200, required=False, help_text="Keine Namen."
    )


@login_required
def bilder(request: HttpRequest, pk: int) -> HttpResponse:
    einheit = get_object_or_404(Einheit, pk=pk)
    formular = BildFormular()
    if request.method == "POST":
        formular = BildFormular(request.POST, request.FILES)
        if formular.is_valid():
            try:
                services.einheitbild_hinzufuegen(
                    einheit, formular.cleaned_data["bild"], formular.cleaned_data["beschreibung"]
                )
            except services.AnbindungFehler as fehler:
                messages.error(request, str(fehler))
            else:
                messages.success(request, f"Bild für {einheit.nw_id} gespeichert.")
                return redirect("anbindungen:bilder", pk=einheit.pk)
    return render(
        request,
        "anbindungen/bilder.html",
        {
            "einheit": einheit,
            "bilder": list(einheit.bilder.filter(aktiv=True)),
            "formular": formular,
        },
    )


@login_required
@require_POST
def bild_entfernen(request: HttpRequest, pk: int) -> HttpResponse:
    eintrag = get_object_or_404(Einheitbild, pk=pk, aktiv=True)
    services.einheitbild_entfernen(eintrag)
    messages.success(request, f"Bild #{eintrag.pk} aus dem Katalog genommen.")
    return redirect(reverse("anbindungen:bilder", args=[eintrag.einheit_id]))


@login_required
def bild(request: HttpRequest, pk: int) -> FileResponse:
    """Liefert ein Bild nur mit Anmeldung aus (nie direkt über Caddy)."""
    eintrag = get_object_or_404(Einheitbild, pk=pk)
    return FileResponse(eintrag.bild.open("rb"))
