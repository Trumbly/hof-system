"""Seite „Läufe“ (Review 2, B16): letzte Läufe mit Status-Badge, Meldungen mit Links zu den
betroffenen Posten und je Lauf ein Knopf „jetzt ausführen“.

- **Welche Läufe:** nur die aus dem Register des Tageslaufs (``apps.cockpit.tageslauf``), der
  Tageslauf selbst und die Läufe mit eigenem Timer auf dem Server (``TIMER_LAEUFE``, wie
  ``deploy/systemd/laeufe/``). Andere Befehle (``testhof``, Importe) startet die Oberfläche nie.
- **dev und test:** Der Knopf startet den Lauf sofort.
- **prod** (``DJANGO_ENV=prod``): Der Knopf öffnet erst einen Dialog mit der Beschreibung des
  Laufs; gestartet wird nur mit dessen Bestätigung.
- **Danach:** Die Liste der Läufe wird ersetzt (die Seite springt nicht), die Meldung nennt
  Status und Körbe und verlinkt auf den neuen Lauf.

Gestartet wird über ``kern.services.lauf_ausfuehren``; der Lauf schreibt selbst über seine
Services. Er läuft wie auf dem Server ohne die Transaktion der Anfrage.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, cast

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.management import get_commands, load_command_class
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import format_html
from django.views.decorators.http import require_POST

from apps.kern import htmx, services
from apps.kern.models import Arbeitsposten, Lauf

LAEUFE_ANZAHL = 50
#: So viele Arbeitsposten nennt eine Zeile; der Rest steht in den Arbeitslisten.
POSTEN_JE_LAUF = 20
#: Läufe mit eigenem Timer auf dem Server (deploy/systemd/laeufe/*.conf).
TIMER_LAEUFE = (
    "tageslauf",
    "belege_postfach",
    "versandstand_abrufen",
    "aufgaben_abgleich",
    "benachrichtigen",
    "kassenbuch_monatsabschluss",
    "kern_selbsttest",
)


def ist_produktion() -> bool:
    return os.environ.get("DJANGO_ENV", "").strip().lower() == "prod"


def ausfuehrbare_laeufe() -> dict[str, str]:
    """Freigabeliste: Name des Laufs → wann er sonst läuft. Tageslauf zuerst, dann seine
    Schritte in ihrer Reihenfolge, dann die übrigen Läufe mit Timer."""
    from apps.cockpit import tageslauf

    laeufe = {"tageslauf": "täglich, alle Schritte nacheinander"}
    for schritt in tageslauf.schritte():
        laeufe.setdefault(schritt.befehl, f"im Tageslauf, {schritt.rhythmus}")
    for name in TIMER_LAEUFE:
        laeufe.setdefault(name, "eigener Zeitplan")
    return {name: rhythmus for name, rhythmus in laeufe.items() if name in get_commands()}


def _beschreibung(name: str) -> str:
    try:
        return str(load_command_class(get_commands()[name], name).help or "")
    except (KeyError, ImportError):
        return ""


@dataclass
class Startbar:
    name: str
    rhythmus: str
    letzter: Lauf | None


@dataclass
class Zeile:
    lauf: Lauf
    posten: list[Arbeitsposten]


class StartForm(forms.Form):
    lauf = forms.ChoiceField(widget=forms.HiddenInput)
    bestaetigt = forms.BooleanField(required=False, widget=forms.HiddenInput)
    #: Knopf auf einer anderen Seite (etwa „Alle Konten abrufen“ unter Bank): nur die Meldung.
    ausserhalb = forms.BooleanField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        feld = cast(forms.ChoiceField, self.fields["lauf"])
        feld.choices = [(n, n) for n in ausfuehrbare_laeufe()]
        feld.error_messages["invalid_choice"] = "Diesen Lauf startet die Oberfläche nicht."
        feld.error_messages["required"] = "Bitte einen Lauf wählen."


def _teile(request: HttpRequest, *, oob: bool = False) -> dict[str, object]:
    name = request.GET.get("name", "") if request.method == "GET" else ""
    laeufe = Lauf.objects.all()
    if name:
        laeufe = laeufe.filter(name=name)
    liste = list(laeufe[:LAEUFE_ANZAHL])
    posten: dict[int, list[Arbeitsposten]] = {lauf.pk: [] for lauf in liste}
    for p in Arbeitsposten.objects.filter(lauf__in=[lauf.pk for lauf in liste]).order_by("pk"):
        if p.lauf_id is not None and len(posten[p.lauf_id]) < POSTEN_JE_LAUF:
            posten[p.lauf_id].append(p)
    letzte: dict[str, Lauf] = {}
    freigabe = ausfuehrbare_laeufe()
    for lauf in Lauf.objects.filter(name__in=freigabe).order_by("name", "-gestartet_am", "-pk"):
        letzte.setdefault(lauf.name, lauf)
    return {
        "zeilen": [Zeile(lauf, posten[lauf.pk]) for lauf in liste],
        "startbar": [Startbar(n, r, letzte.get(n)) for n, r in freigabe.items()],
        "name_filter": name,
        "oob": oob,
    }


@login_required
def laeufe(request: HttpRequest) -> HttpResponse:
    """Letzte Läufe mit Status, Zählern und Meldung; darüber die Läufe zum Starten.

    ``?name=<lauf>`` zeigt nur die Läufe eines Namens. ``?dialog=ausfuehren&lauf=<name>``
    liefert den Bestätigungsdialog (prod).
    """
    if request.GET.get("dialog") == "ausfuehren":
        name = request.GET.get("lauf", "")
        if name not in ausfuehrbare_laeufe():
            raise Http404("Diesen Lauf startet die Oberfläche nicht.")
        ausserhalb = bool(request.GET.get("ausserhalb"))
        return _dialog(
            request,
            StartForm(initial={"lauf": name, "bestaetigt": True, "ausserhalb": ausserhalb}),
            name,
        )
    return render(request, "kern/laeufe.html", _teile(request))


def _dialog(request: HttpRequest, form: StartForm, name: str) -> HttpResponse:
    return render(
        request,
        "bausteine/dialog_formular.html",
        {
            "titel": f"Lauf {name} jetzt ausführen?",
            "form": form,
            "url": reverse("kern:lauf_ausfuehren"),
            "hinweis": _beschreibung(name),
            "warnungen": [
                "Der Lauf arbeitet mit den echten Daten, wie nachts auf dem Server. "
                "Was er bucht oder anlegt, steht danach im Protokoll und lässt sich dort als "
                "Vorgang zurücknehmen."
            ]
            if ist_produktion()
            else [],
            "knopf": "Jetzt ausführen",
        },
    )


def _meldung(lauf: Lauf) -> str:
    teile = [f"Lauf #{lauf.pk} {lauf.name}: {lauf.get_status_display()}"]
    if lauf.gebucht:
        teile.append(f"{lauf.gebucht} gebucht")
    if lauf.arbeitsposten:
        teile.append(f"{lauf.arbeitsposten} Arbeitsposten")
    if lauf.fehler:
        teile.append(f"{lauf.fehler} Fehler")
    return " · ".join(teile)


@login_required
@require_POST
@transaction.non_atomic_requests
def ausfuehren(request: HttpRequest) -> HttpResponse:
    """Startet einen Lauf aus der Freigabeliste (in prod nur aus dem Bestätigungsdialog)."""
    form = StartForm(request.POST)
    if not form.is_valid():
        text = " ".join(form.errors.get("lauf", ["Diesen Lauf startet die Oberfläche nicht."]))
        if htmx.ist_htmx(request):
            return HttpResponse(htmx.toast(request, text, art="fehler"), status=200)
        messages.error(request, text)
        return redirect("kern:laeufe")
    name = form.cleaned_data["lauf"]
    if ist_produktion() and not form.cleaned_data["bestaetigt"]:
        # Auf dem Server nur nach Bestätigung: erst der Dialog.
        if htmx.ist_htmx(request):
            antwort = _dialog(request, StartForm(initial={"lauf": name, "bestaetigt": True}), name)
            antwort["HX-Retarget"] = "#dialog-inhalt"
            antwort["HX-Reswap"] = "innerHTML"
            return antwort
        return redirect(f"{reverse('kern:laeufe')}?dialog=ausfuehren&lauf={name}")
    try:
        lauf = services.lauf_ausfuehren(name)
    except services.LaufNichtGestartet as fehler:
        if htmx.ist_htmx(request):
            return HttpResponse(htmx.toast(request, str(fehler), art="fehler"))
        messages.error(request, str(fehler))
        return redirect("kern:laeufe")
    text = _meldung(lauf)
    link = f"{reverse('kern:laeufe')}#lauf-{lauf.pk}"
    if htmx.ist_htmx(request):
        teile = []
        if not form.cleaned_data["ausserhalb"]:
            kontext = _teile(request, oob=True)
            teile = [
                render_to_string("kern/_laeufe_start.html", kontext, request=request),
                render_to_string("kern/_laeufe_liste.html", kontext, request=request),
            ]
        return htmx.erfolg(request, teile, text, link=link, link_text="ansehen")
    stufe = messages.success if lauf.status == Lauf.Status.ERFOLGREICH else messages.warning
    stufe(request, format_html('{} · <a href="{}">ansehen</a>', text, link))
    return redirect(link)
