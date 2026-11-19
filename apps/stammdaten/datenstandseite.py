"""Seite „Datenstand“ in der neuen Gestaltung (Review 2, B12).

- Oben eine dichte Übersicht je Bereich (geprüft, mit Lücken, fehlende Angaben).
- Je Bereich die Lücken gruppiert nach fehlendem Feld („14 Verträge ohne Scan“), jede
  Gruppe aufklappbar mit einer dichten Tabelle; jede Zeile führt mit einem Klick zur Eingabe.
- Sammelaktion „Scans hochladen“: mehrere PDFs wählen, die Seite ordnet sie über die
  V-Nummer im Dateinamen zu und zeigt die Zuordnung; erst „übernehmen“ hängt sie an
  (``apps.stammdaten.scans``).

Die Seite zeigt Namen von Parteien (Max' Oberfläche); Meldungen nennen nur Nummern.
"""

from __future__ import annotations

import datetime
from typing import Any

from django import forms
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.kern import htmx
from apps.stammdaten import datenstand, scans, services


def _heute() -> datetime.date:
    return datetime.date.today()


class MehrereDateien(forms.ClearableFileInput):
    allow_multiple_selected = True


class ScanForm(forms.Form):
    scans = forms.FileField(
        label="Scans (PDF)",
        help_text="Die V-Nummer steht im Dateinamen, z. B. „V-0005 unterschrieben.pdf“. "
        "Vor dem Übernehmen zeigt die Vorschau, welche Datei zu welchem Vertrag gehört.",
        widget=MehrereDateien(attrs={"accept": "application/pdf"}),
    )


def _kontext() -> dict[str, Any]:
    bereiche = datenstand.ermitteln(_heute())
    return {
        "bereiche": bereiche,
        "bereiche_gruppen": [(b, datenstand.nach_feld(b)) for b in bereiche],
        "luecken_gesamt": sum(b.anzahl_luecken for b in bereiche),
    }


@login_required
def seite(request: HttpRequest) -> HttpResponse:
    """Fehlende Pflichtfelder je Bereich, gruppiert nach Feld; jede Lücke führt mit einem
    Klick zur Eingabe."""
    if request.GET.get("dialog") == "scans":
        return _scan_dialog(request, ScanForm())
    return render(request, "stammdaten/datenstand.html", _kontext())


def _scan_dialog(
    request: HttpRequest, form: ScanForm, warnungen: list[str] | None = None
) -> HttpResponse:
    return render(
        request,
        "bausteine/dialog_formular.html",
        {
            "titel": "Scans hochladen",
            "form": form,
            "url": reverse("stammdaten:datenstand_scans"),
            "aktion": "vorschau",
            "knopf": "Zuordnung zeigen",
            "warnungen": warnungen or [],
            "hinweis": "Mehrere PDFs auf einmal. Zugeordnet wird über die V-Nummer im "
            "Dateinamen; nichts wird angehängt, bevor du die Zuordnung bestätigst.",
        },
    )


@login_required
@require_POST
def scans_hochladen(request: HttpRequest) -> HttpResponse:
    """Sammelaktion „Scans hochladen“: erst Vorschau der Zuordnung, dann übernehmen."""
    if request.POST.get("aktion") == "uebernehmen":
        kennung = request.POST.get("kennung", "")
        try:
            angehaengt = scans.uebernehmen(kennung)
        except (scans.ScanFehler, services.StammdatenFehler) as fehler:
            return _scan_dialog(request, ScanForm(), [str(fehler)])
        nummern = ", ".join(v.nummer for v in angehaengt[:6])
        if len(angehaengt) > 6:
            nummern += " …"
        meldung = f"{len(angehaengt)} Scan{'s' if len(angehaengt) != 1 else ''} übernommen" + (
            f" · {nummern}" if nummern else ""
        )
        ziel = f"{reverse('stammdaten:datenstand')}#bereich-vertrag"
        if htmx.ist_htmx(request):
            teil = render_to_string(
                "stammdaten/_datenstand_inhalt.html", {**_kontext(), "oob": True}, request
            )
            return htmx.erfolg(request, [teil], meldung, link=ziel, link_text="Verträge ansehen")
        return redirect(ziel)
    form = ScanForm(request.POST, request.FILES)
    dateien = request.FILES.getlist("scans")
    if not dateien:
        form.add_error("scans", "Bitte mindestens eine Datei wählen.")
        return _scan_dialog(request, form)
    try:
        kennung = scans.ablegen(dateien)
    except scans.ScanFehler as fehler:
        form.add_error("scans", str(fehler))
        return _scan_dialog(request, form)
    zuordnung = scans.vorschau(kennung)
    return render(
        request,
        "stammdaten/_scans_vorschau.html",
        {
            "kennung": kennung,
            "zuordnung": zuordnung,
            "passend": sum(1 for z in zuordnung if z.ok),
        },
    )
