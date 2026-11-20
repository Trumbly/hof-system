"""Seiten des Protokolls (Review 2, A4 und B14): Vorgänge statt Ereignisse, ein Vorgang mit
allen Einzelheiten und seine Rücknahme.

Filter über der Liste: Suchfeld für Vertrag, Einheit oder Person (Partei; V-Nummer, NW-ID oder
Name, mit Vorschlagsliste), „wer“ (Benutzer oder Läufe), Zeitraum und „betrifft“ (Modell).
Links von anderen Seiten nutzen ``?bezug=app.modell:ID`` (etwa der Reiter „Verlauf“ des
Vertrags); das Suchfeld zeigt dann den Text des Bezugs.

Feldnamen stehen deutsch („Status: aktiv → gekündigt“), technische Felder (``inhalt_html``,
``pdf_sha256``, Ereignis-IDs) nur in den aufgeklappten Einzelheiten (``apps.kern.vorgaenge``).
"""

from __future__ import annotations

import uuid
from typing import Any, cast

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import models
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.utils.http import urlencode
from django.views.decorators.http import require_POST

from apps.kern import services, vorgaenge
from apps.kern.models import Benutzer
from apps.kern.protokoll import protokollierte_modelle

PROTOKOLL_ANZAHL = 50


def _modellwahl() -> list[tuple[str, str]]:
    return sorted(
        ((m._meta.label, str(m._meta.verbose_name_plural)) for m in protokollierte_modelle()),
        key=lambda eintrag: eintrag[1],
    )


def _werwahl() -> list[tuple[str, str]]:
    benutzer = [
        (f"{vorgaenge.WER_BENUTZER}{b.pk}", b.email or f"Benutzer #{b.pk}")
        for b in Benutzer.objects.order_by("email", "pk")
    ]
    return [("", "alle"), *benutzer, (vorgaenge.WER_LAUF, "Läufe")]


class ProtokollFilter(forms.Form):
    suche = forms.CharField(
        label="Vertrag, Einheit oder Person",
        required=False,
        max_length=200,
        widget=forms.TextInput(
            attrs={
                "type": "search",
                "list": "protokoll-bezuege",
                "placeholder": "V-0005, NW-G-0018 oder Name",
                "autocomplete": "off",
            }
        ),
    )
    wer = forms.ChoiceField(label="Wer", required=False)
    von = forms.DateField(
        label="von", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    bis = forms.DateField(
        label="bis", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    modell = forms.ChoiceField(label="Betrifft", required=False)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        wer = cast(forms.ChoiceField, self.fields["wer"])
        modell = cast(forms.ChoiceField, self.fields["modell"])
        wer.choices = _werwahl()
        modell.choices = [("", "alles"), *_modellwahl()]
        for feld in (wer, modell):
            feld.error_messages["invalid_choice"] = "Diese Auswahl gibt es nicht (mehr)."
        for name in ("von", "bis"):
            self.fields[name].error_messages["invalid"] = "Bitte ein Datum angeben."

    def clean(self) -> dict[str, Any]:
        daten = super().clean() or {}
        von, bis = daten.get("von"), daten.get("bis")
        if von and bis and bis < von:
            self.add_error("bis", "„bis“ liegt vor „von“.")
        return daten


def _bezug(request: HttpRequest, form: ProtokollFilter) -> tuple[models.Model | None, list[Any]]:
    """Der Bezug aus dem Suchfeld (oder aus ``?bezug=``) und, wenn der Text mehrdeutig ist,
    die Kandidaten zum Auswählen."""
    suche = form.cleaned_data.get("suche", "") if form.is_valid() else ""
    if suche:
        treffer = vorgaenge.bezug_suchen(suche)
        if len(treffer) == 1:
            return treffer[0], []
        if not treffer:
            form.add_error(
                "suche",
                f"Zu „{suche}“ passt kein Vertrag, keine Einheit und keine Person. Bitte die "
                "V-Nummer, die NW-ID oder einen Teil des Namens eingeben.",
            )
            return None, []
        return None, [
            (vorgaenge.bezug_beschriften(t), vorgaenge.bezug_als_text(t)) for t in treffer
        ]
    text = request.GET.get("bezug", "")
    return (vorgaenge.bezug_aus_text(text) if text else None), []


@login_required
def protokoll(request: HttpRequest) -> HttpResponse:
    """Die letzten Vorgänge (Review 2, A4 und B14): je Nutzeraktion oder Lauf eine Zeile mit
    Titel, Zeit, wer und Zahl der Änderungen; die Einzelheiten zum Aufklappen.

    Filter: ``suche`` (Vertrag, Einheit, Person), ``bezug`` (``app.modell:ID``), ``wer``,
    ``von``/``bis`` (Datum), ``modell`` (Label), ``seite``.
    """
    form = ProtokollFilter(request.GET or None)
    form.is_valid()
    daten: dict[str, Any] = getattr(form, "cleaned_data", {})
    bezug, kandidaten = _bezug(request, form)
    try:
        seite = max(1, int(request.GET.get("seite", "1")))
    except ValueError:
        seite = 1
    zeilen = (
        []
        if kandidaten or form.errors.get("suche")
        else vorgaenge.vorgaenge_suchen(
            modell=daten.get("modell") or "",
            bezug=bezug,
            von=daten.get("von"),
            bis=daten.get("bis"),
            wer=daten.get("wer") or "",
            anzahl=PROTOKOLL_ANZAHL + 1,
            versatz=(seite - 1) * PROTOKOLL_ANZAHL,
        )
    )
    weiter = len(zeilen) > PROTOKOLL_ANZAHL
    filter_ohne_seite = {
        k: v
        for k, v in {
            "suche": daten.get("suche") or "",
            "bezug": vorgaenge.bezug_als_text(bezug) if bezug is not None else "",
            "wer": daten.get("wer") or "",
            "von": daten["von"].isoformat() if daten.get("von") else "",
            "bis": daten["bis"].isoformat() if daten.get("bis") else "",
            "modell": daten.get("modell") or "",
        }.items()
        if v
    }
    if bezug is not None and not form["suche"].value():
        # Aus einem Link (``?bezug=``): Das Suchfeld zeigt, wonach gefiltert ist.
        form = ProtokollFilter({**request.GET.dict(), "suche": vorgaenge.bezug_beschriften(bezug)})
        form.is_valid()
    return render(
        request,
        "kern/protokoll.html",
        {
            "form": form,
            "zeilen": zeilen[:PROTOKOLL_ANZAHL],
            "bezug": bezug,
            "bezug_titel": vorgaenge.objekt_titel(
                type(bezug),
                {f.attname: getattr(bezug, f.attname) for f in type(bezug)._meta.concrete_fields},
            )
            if bezug is not None
            else "",
            "kandidaten": [
                (titel, "?" + urlencode({**filter_ohne_seite, "suche": "", "bezug": wert}))
                for titel, wert in kandidaten
            ],
            "vorschlaege": vorgaenge.bezug_vorschlaege(),
            "gefiltert": bool(filter_ohne_seite),
            "seite": seite,
            "weiter_url": "?" + urlencode({**filter_ohne_seite, "seite": seite + 1})
            if weiter
            else "",
            "zurueck_url": "?" + urlencode({**filter_ohne_seite, "seite": seite - 1})
            if seite > 1
            else "",
        },
    )


@login_required
def vorgang(request: HttpRequest, pk: uuid.UUID) -> HttpResponse:
    """Ein Vorgang mit allen Einzelheiten und der Vorschau seiner Rücknahme.

    Lässt er sich nicht zurücknehmen, sagt die Seite warum (mit Link zur Ursache, etwa zum
    versandten Schreiben).
    """
    zeile = vorgaenge.vorgang_lesen(pk)
    if zeile is None:
        raise Http404("Diesen Vorgang gibt es nicht.")
    vorschau: services.Ruecknahmebericht | None = None
    sperren: list[services.Sperre] = []
    if zeile.zuruecknehmbar or zeile.zurueckgenommen_durch:
        try:
            vorschau = services.ruecknahme_vorschau(pk)
        except services.RuecknahmeGesperrt as fehler:
            sperren = fehler.sperren
        except services.RuecknahmeNichtMoeglich as fehler:
            sperren = [services.Sperre(str(fehler))]
    elif zeile.ruecknahme_von is not None or zeile.art == vorgaenge.Art.RUECKNAHME:
        sperren = [
            services.Sperre(
                "Eine Rücknahme lässt sich nicht zurücknehmen. Bitte die ursprüngliche Aktion "
                "neu ausführen, etwa die Kündigung neu erfassen."
            )
        ]
    elif zeile.testhof:
        sperren = [
            services.Sperre(
                "Den Aufbau des Testhofs nimmt man nicht zurück; „hof testhof“ baut ihn neu auf."
            )
        ]
    return render(
        request,
        "kern/vorgang.html",
        {
            "v": zeile,
            "vorschau": vorschau,
            "sperren": sperren,
            "ruecknahme_von_titel": vorgaenge.titel_von(zeile.ruecknahme_von)
            if zeile.ruecknahme_von
            else "",
        },
    )


@login_required
@require_POST
def vorgang_zuruecknehmen(request: HttpRequest, pk: uuid.UUID) -> HttpResponse:
    """Nimmt einen ganzen Vorgang zurück. Die Meldung sagt, was passiert ist, mit Link."""
    try:
        bericht = services.vorgang_zuruecknehmen(pk)
    except services.RuecknahmeNichtMoeglich as fehler:
        messages.error(request, f"Nicht zurückgenommen: {fehler}")
        if vorgaenge.vorgang_lesen(pk) is None:
            return redirect("kern:protokoll")
        return redirect("kern:vorgang", pk=pk)
    # Review 3, Punkt 10: kurz, „Kündigung V-0002 zurückgenommen · 12 Änderungen → ansehen“;
    # die einzelnen Schritte stehen auf der Seite der Rücknahme.
    anzahl = bericht.ereignisse
    messages.success(
        request,
        format_html(
            '{} · {} → <a href="{}">ansehen</a>',
            vorgaenge.ruecknahme_titel(bericht.titel),
            f"{anzahl} Änderung" if anzahl == 1 else f"{anzahl} Änderungen",
            reverse("kern:vorgang", args=[bericht.ruecknahme_id]),
        ),
    )
    return redirect("kern:protokoll")
