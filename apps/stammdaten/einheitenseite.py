"""Einheitenseite in der neuen Gestaltung (Review 2, B7), nach dem Muster der Vertragsseite.

- **Kopf:** NW-ID, Status, Typ, Fläche, Objekt; aktueller Vertrag und Mieter, Zielmiete,
  Zähler. Rechts die Aktionen: „Neuer Vertrag“ (wenn frei, als Dialog), Zielmiete ändern,
  Status setzen, mehr ▾.
- **Reiter:** Übersicht · Verträge · Zähler · Bilder und Katalog · Verlauf (``?reiter=…``).
- **Aktionen als Dialoge** (``apps.kern.htmx``): ``?dialog=<aktion>`` liefert das Formular,
  das Speichern ersetzt Kopf und Reiter und meldet mit Link, was passiert ist. Ohne
  JavaScript: POST mit ``aktion``, dann Umleitung.

Wohnraum führt Verwaltio: Die Seite zeigt nur NW-ID, Objekt und Bezeichnung.
Gespeichert wird nur über ``apps.stammdaten.services``.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlsplit

from django import forms
from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.http import urlencode

from apps.anbindungen import services as anbindungen
from apps.kern import htmx, zahlen, zeit
from apps.stammdaten import einheitenliste, services
from apps.stammdaten import forms as f
from apps.stammdaten.models import (
    Einheit,
    EinheitStatus,
    Kontaktweg,
    Schluessel,
    Vertrag,
    Vertragspartei,
    Zielmiete,
)
from apps.stammdaten.neuer_vertrag import naechster_monatserster
from apps.stammdaten.vertragsseite import Dialog, Rueckmeldung, protokoll_url, verlauf_aus

REITER: tuple[tuple[str, str], ...] = (
    ("uebersicht", "Übersicht"),
    ("vertraege", "Verträge"),
    ("zaehler", "Zähler"),
    ("bilder", "Bilder und Katalog"),
    ("verlauf", "Verlauf"),
)

DIALOGE: dict[str, Dialog] = {
    "stammfelder": Dialog("Einheit bearbeiten", "Speichern", "stammfelder"),
    "zielmiete": Dialog(
        "Zielmiete ändern",
        "Ab Datum übernehmen",
        "zielmiete",
        "Die neue Zielmiete gilt ab dem Datum; der alte Stand bleibt lesbar.",
    ),
    "status": Dialog(
        "Status setzen",
        "Status setzen",
        "status",
        "Der neue Status gilt ab dem Datum. Vermietet und frei setzen Übergabe und Rückgabe "
        "selbst; hier geht es um gesperrt, in Vorbereitung oder reserviert.",
    ),
    "schluessel": Dialog("Schlüssel hinzufügen", "Hinzufügen", "schluessel"),
}
SCHLUESSEL_DIALOG = Dialog("Schlüssel bearbeiten", "Speichern", "schluessel")


def _dialog_fuer(aktion: str) -> Dialog:
    return SCHLUESSEL_DIALOG if aktion.startswith("schluessel-") else DIALOGE[aktion]


def _gebunden(request: HttpRequest, prefix: str, klasse: Any, **kwargs: Any) -> Any:
    if request.method == "POST" and request.POST.get("aktion") == prefix:
        return klasse(request.POST, request.FILES, prefix=prefix, **kwargs)
    return klasse(prefix=prefix, **kwargs)


def formulare(request: HttpRequest, einheit: Einheit) -> dict[str, forms.Form]:
    heute = zeit.heute()
    if einheit.ist_wohnraum:
        return {"stammfelder": _gebunden(request, "stammfelder", f.WohnraumForm, instance=einheit)}
    zielmiete = einheit.zielmiete_am(heute)
    ergebnis: dict[str, forms.Form] = {
        "stammfelder": _gebunden(request, "stammfelder", f.EinheitForm, instance=einheit),
        # Vorausgefüllt: nächster Monatserster, Zielmiete von heute.
        "zielmiete": _gebunden(
            request,
            "zielmiete",
            f.ZielmieteForm,
            initial={
                "gueltig_ab": naechster_monatserster(heute),
                "betrag": zielmiete.betrag if zielmiete else None,
            },
        ),
        "status": _gebunden(request, "status", f.StatusForm, initial={"gueltig_ab": heute}),
        "schluessel": _gebunden(
            request, "schluessel", f.SchluesselForm, initial={"inhaber": Schluessel.Inhaber.HOF}
        ),
    }
    for s in einheit.schluessel.all():
        prefix = f"schluessel-{s.pk}"
        ergebnis[prefix] = _gebunden(request, prefix, f.SchluesselForm, instance=s)
    return ergebnis


def _url(einheit: Einheit, reiter: str = "", anker: str = "") -> str:
    basis = reverse("stammdaten:einheit", args=[einheit.pk])
    if reiter and reiter != "uebersicht":
        basis = f"{basis}?{urlencode({'reiter': reiter})}"
    return f"{basis}#{anker}" if anker else basis


# --- Speichern -----------------------------------------------------------------------


def _speichern_stammfelder(e: Einheit, form: forms.Form) -> Rueckmeldung:
    services.einheit_aendern(e, **form.cleaned_data)
    return Rueckmeldung(f"Einheit {e.nw_id} gespeichert", _url(e, anker="stammfelder"))


def _speichern_zielmiete(e: Einheit, form: forms.Form) -> Rueckmeldung:
    d = form.cleaned_data
    z = services.zielmiete_setzen(e, d["betrag"], d["gueltig_ab"], d["begruendung"])
    return Rueckmeldung(
        f"Zielmiete {zahlen.euro(z.betrag)} ab {z.gueltig_ab:%d.%m.%Y} · alter Stand bleibt lesbar",
        _url(e, anker="zielmiete"),
    )


def _speichern_status(e: Einheit, form: forms.Form) -> Rueckmeldung:
    d = form.cleaned_data
    s = services.einheit_status_setzen(e, d["status"], d["gueltig_ab"], d["grund"])
    return Rueckmeldung(
        f"Status „{s.get_status_display()}“ ab {s.gueltig_ab:%d.%m.%Y}", _url(e, anker="status")
    )


def _speichern_schluessel(e: Einheit, form: forms.Form) -> Rueckmeldung:
    s = services.schluessel_anlegen(e, **form.cleaned_data)
    return Rueckmeldung(f"{s.anzahl} × {s.get_art_display()} erfasst", _url(e, anker="schluessel"))


HANDLER: dict[str, Callable[[Einheit, forms.Form], Rueckmeldung]] = {
    "stammfelder": _speichern_stammfelder,
    "zielmiete": _speichern_zielmiete,
    "status": _speichern_status,
    "schluessel": _speichern_schluessel,
}


def _speichern(e: Einheit, aktion: str, form: forms.Form) -> Rueckmeldung:
    if aktion.startswith("schluessel-"):
        schluessel: Schluessel = form.instance  # type: ignore[attr-defined]
        services.schluessel_aendern(schluessel, **form.cleaned_data)
        return Rueckmeldung(f"Schlüssel #{schluessel.pk} gespeichert", _url(e, anker="schluessel"))
    return HANDLER[aktion](e, form)


# --- Kopf und Reiter -----------------------------------------------------------------


def _mieter(vertrag: Vertrag | None) -> list[dict[str, Any]]:
    if vertrag is None:
        return []
    ergebnis = []
    for vp in (
        vertrag.vertragsparteien.filter(rolle=Vertragspartei.Rolle.MIETER)
        .select_related("partei")
        .prefetch_related("partei__kontaktwege")
    ):
        wege = list(vp.partei.kontaktwege.all())
        ergebnis.append(
            {
                "partei_id": vp.partei_id,
                "name": vp.partei.anzeigename,
                "telefon": [
                    k.wert for k in wege if k.art in (Kontaktweg.Art.TELEFON, Kontaktweg.Art.MOBIL)
                ],
                "email": [k.wert for k in wege if k.art == Kontaktweg.Art.EMAIL],
            }
        )
    return ergebnis


def kopf_kontext(einheit: Einheit) -> dict[str, Any]:
    heute = zeit.heute()
    lage = einheitenliste.lage(einheit, heute)
    zaehler = list(einheit.zaehler.filter(ausgebaut_am__isnull=True).order_by("art", "nummer"))
    return {
        "einheit": einheit,
        "heute": heute,
        "lage": lage,
        "mieter": _mieter(lage.vertrag),
        "status_heute": einheit.status_am(heute),
        "zielmiete_heute": einheit.zielmiete_am(heute),
        "zaehler_aktiv": zaehler,
        "dialoge": DIALOGE,
        "verlauf_url": protokoll_url(einheit),
    }


def _bilder_kontext(einheit: Einheit, heute: datetime.date) -> dict[str, Any]:
    return {
        "bilder": list(einheit.bilder.filter(aktiv=True).order_by("reihenfolge", "pk")),  # type: ignore[attr-defined]
        "buchbar_ab": anbindungen.buchbar_ab(einheit, heute),
    }


def reiter_kontext(einheit: Einheit, reiter: str, kopf: dict[str, Any]) -> dict[str, Any]:
    heute = kopf["heute"]
    kontext: dict[str, Any] = {
        "reiter_aktiv": reiter,
        "reiter": [{"schluessel": s, "titel": t, "url": _url(einheit, s)} for s, t in REITER],
    }
    if einheit.ist_wohnraum:
        kontext["reiter"] = [kontext["reiter"][0], kontext["reiter"][-1]]
    if reiter == "uebersicht":
        kontext |= {
            "statusstaende": einheit.statusstaende.order_by("-gueltig_ab"),
            "zielmieten": einheit.zielmieten.order_by("-gueltig_ab"),
            "schluessel_liste": einheit.schluessel.select_related("partei").order_by("pk"),
            "checklisten": einheit.checklisten.prefetch_related("punkte").order_by("-pk"),
        }
    elif reiter == "vertraege":
        vertraege = list(
            einheit.vertraege.order_by("-beginn").prefetch_related(
                "vertragsparteien__partei", "konditionen"
            )
        )
        kontext["vertraege"] = [
            {
                "vertrag": v,
                "mieter": ", ".join(
                    vp.partei.anzeigename
                    for vp in v.vertragsparteien.all()
                    if vp.rolle == Vertragspartei.Rolle.MIETER
                ),
                "kondition": v.kondition_am(max(heute, v.beginn)),
            }
            for v in vertraege
        ]
    elif reiter == "zaehler":
        kontext["zaehler_zeilen"] = [
            (z, z.letzter_stand(heute)) for z in einheit.zaehler.order_by("ausgebaut_am", "art")
        ]
    elif reiter == "bilder":
        kontext |= _bilder_kontext(einheit, heute)
    elif reiter == "verlauf":
        kontext["verlauf"] = verlauf_aus(
            [
                (Einheit, [einheit.pk]),
                (EinheitStatus, list(einheit.statusstaende.values_list("pk", flat=True))),
                (Zielmiete, list(einheit.zielmieten.values_list("pk", flat=True))),
                (Schluessel, list(einheit.schluessel.values_list("pk", flat=True))),
            ]
        )
    return kontext


# --- Seite ---------------------------------------------------------------------------


def _reiter_wahl(request: HttpRequest, einheit: Einheit) -> str:
    reiter = request.GET.get("reiter")
    if reiter is None and (aktuell := request.headers.get("HX-Current-URL")):
        reiter = parse_qs(urlsplit(aktuell).query).get("reiter", [""])[0]
    erlaubt = ("uebersicht", "verlauf") if einheit.ist_wohnraum else dict(REITER)
    return reiter if reiter in erlaubt else "uebersicht"


def _teile(einheit: Einheit, reiter: str, oob: bool) -> dict[str, Any]:
    kopf = kopf_kontext(einheit)
    return {**kopf, **reiter_kontext(einheit, reiter, kopf), "oob": oob}


def _dialog(
    request: HttpRequest, einheit: Einheit, aktion: str, form: forms.Form, reiter: str
) -> HttpResponse:
    dialog = _dialog_fuer(aktion)
    return render(
        request,
        "bausteine/dialog_formular.html",
        {
            "titel": f"{dialog.titel} · {einheit.nw_id}",
            "form": form,
            "url": _url(einheit, reiter),
            "aktion": aktion,
            "knopf": dialog.knopf,
            "hinweis": dialog.hinweis,
        },
    )


def seite(request: HttpRequest, pk: int) -> HttpResponse:
    einheit = get_object_or_404(Einheit.objects.select_related("objekt"), pk=pk)
    reiter = _reiter_wahl(request, einheit)
    alle = formulare(request, einheit)

    if request.method == "GET":
        if aktion := request.GET.get("dialog"):
            if aktion not in alle:
                raise Http404("Diese Aktion gibt es für diese Einheit nicht.")
            if htmx.ist_htmx(request):
                return _dialog(request, einheit, aktion, alle[aktion], reiter)
            # Ohne JavaScript (oder aus einem Link, z. B. dem Datenstand): die Seite mit
            # dem Formular offen.
            return render(
                request,
                "stammdaten/einheit.html",
                _teile(einheit, reiter, False) | _offen(aktion, alle[aktion]),
            )
        if htmx.ist_htmx(request) and request.headers.get("HX-Target") == "einheit-reiter":
            return render(
                request, "stammdaten/_einheit_reiter.html", _teile(einheit, reiter, False)
            )
        return render(request, "stammdaten/einheit.html", _teile(einheit, reiter, False))

    aktion = request.POST.get("aktion", "")
    if aktion not in alle:
        raise Http404("Unbekannte Aktion")
    form = alle[aktion]
    if form.is_valid():
        try:
            meldung = _speichern(einheit, aktion, form)
        except services.StammdatenFehler as fehler:
            form.add_error(None, str(fehler))
        else:
            if htmx.ist_htmx(request):
                einheit.refresh_from_db()
                kontext = _teile(einheit, reiter, True)
                teile = [
                    render_to_string("stammdaten/_einheit_kopf.html", kontext, request=request),
                    render_to_string("stammdaten/_einheit_reiter.html", kontext, request=request),
                ]
                return htmx.erfolg(
                    request, teile, meldung.text, link=meldung.link, link_text=meldung.link_text
                )
            messages.success(request, meldung.text)
            return redirect(_url(einheit, reiter, _dialog_fuer(aktion).anker))
    if htmx.ist_htmx(request):
        return _dialog(request, einheit, aktion, form, reiter)
    # Ohne JavaScript: Seite mit dem Formular und seinen Fehlern
    for fehler in form.non_field_errors():
        messages.error(request, fehler)
    if not form.non_field_errors():
        messages.error(request, "Bitte die markierten Angaben prüfen.")
    kontext = _teile(einheit, reiter, False) | _offen(aktion, form)
    return render(request, "stammdaten/einheit.html", kontext)


def _offen(aktion: str, form: forms.Form) -> dict[str, Any]:
    return {
        "offenes_formular": form,
        "offene_aktion": aktion,
        "offener_dialog": _dialog_fuer(aktion),
    }
