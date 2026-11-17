"""Seiten der Zähler: Übersicht, Zähler mit Ständen und Wechsel, Ablesung am Handy (O2),
Liste für den nächsten Besuch. Gespeichert wird über ``services``."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.kern.models import Objekt
from apps.stammdaten import datenstand
from apps.stammdaten.forms import foto_pruefen
from apps.zaehler import forms as f
from apps.zaehler import services
from apps.zaehler.models import Zaehler, Zaehlerstand


def _heute() -> datetime.date:
    return datetime.date.today()


# --- Übersicht -----------------------------------------------------------------------


@login_required
def uebersicht(request: HttpRequest) -> HttpResponse:
    """Alle Zähler nach Objekt, mit Link zur Ablesung je Objekt."""
    gruppen = []
    for objekt in Objekt.objects.filter(aktiv=True).order_by("kuerzel"):
        zaehler = services.zaehler_des_objekts(objekt, nur_eingebaut=False)
        for z in zaehler:
            z.letzter = z.letzter_stand()  # type: ignore[attr-defined]
        gruppen.append(
            {
                "objekt": objekt,
                "eingebaut": [z for z in zaehler if z.ist_eingebaut],
                "ausgebaut": [z for z in zaehler if not z.ist_eingebaut],
            }
        )
    return render(
        request,
        "zaehler/uebersicht.html",
        {"gruppen": gruppen, "stichtag": services.letzter_stichtag(_heute())},
    )


@login_required
def neu(request: HttpRequest) -> HttpResponse:
    initial = {k: request.GET[k] for k in ("einheit", "objekt") if request.GET.get(k)}
    form = f.ZaehlerNeuForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            zaehler = services.zaehler_anlegen(
                d["nummer"],
                d["art"],
                einheit=d["einheit"],
                objekt=d["objekt"],
                bezeichnung=d["bezeichnung"],
                einbaudatum=d["einbaudatum"],
                eichfrist_bis=d["eichfrist_bis"],
                einbau_stand=d["einbau_stand"] or None,
            )
        except services.ZaehlerFehler as fehler:
            messages.error(request, str(fehler))
        else:
            messages.success(request, f"{zaehler} angelegt.")
            return redirect("zaehler:zaehler", zaehler.pk)
    return render(request, "zaehler/neu.html", {"form": form})


# --- Ein Zähler ----------------------------------------------------------------------


def _formular(request: HttpRequest, aktion: str, klasse: Any, **kwargs: Any) -> Any:
    if request.method == "POST" and request.POST.get("aktion") == aktion:
        return klasse(request.POST, request.FILES, prefix=aktion, **kwargs)
    return klasse(prefix=aktion, **kwargs)


@login_required
def zaehler(request: HttpRequest, pk: int) -> HttpResponse:
    """Stammfelder, Stände (mit Kennzeichen „geschätzt“), Stand erfassen, Zählerwechsel."""
    zaehler = get_object_or_404(
        Zaehler.objects.select_related("einheit", "objekt", "nachfolger"), pk=pk
    )
    formulare = {
        "stammfelder": _formular(
            request,
            "stammfelder",
            f.ZaehlerForm,
            initial={
                "nummer": zaehler.nummer,
                "bezeichnung": zaehler.bezeichnung,
                "einbaudatum": zaehler.einbaudatum,
                "eichfrist_bis": zaehler.eichfrist_bis,
            },
        ),
        "stand": _formular(request, "stand", f.StandForm, initial={"datum": _heute()}),
        "wechsel": _formular(request, "wechsel", f.WechselForm, initial={"datum": _heute()}),
    }
    if request.method == "POST":
        aktion = request.POST.get("aktion", "")
        if aktion not in formulare:
            raise Http404("Unbekannte Aktion")
        form = formulare[aktion]
        if form.is_valid():
            d = form.cleaned_data
            try:
                if aktion == "stammfelder":
                    services.zaehler_aendern(zaehler, **d)
                    ziel = zaehler
                elif aktion == "stand":
                    services.stand_erfassen(
                        zaehler,
                        d["datum"],
                        d["stand"],
                        art="geschaetzt" if d["geschaetzt"] else "abgelesen",
                        foto=d["foto"],
                        vermerk=d["vermerk"],
                    )
                    ziel = zaehler
                else:
                    wechsel = services.zaehler_wechseln(
                        zaehler,
                        d["datum"],
                        d["ausbau_stand"],
                        neue_nummer=d["neue_nummer"],
                        einbau_stand=d["einbau_stand"],
                        eichfrist_bis=d["eichfrist_bis"],
                        ausbau_foto=d["ausbau_foto"],
                        einbau_foto=d["einbau_foto"],
                    )
                    ziel = wechsel.neuer
            except services.ZaehlerFehler as fehler:
                messages.error(request, str(fehler))
            else:
                messages.success(request, "Gespeichert.")
                return redirect("zaehler:zaehler", ziel.pk)
        else:
            messages.error(request, "Bitte die markierten Felder prüfen.")
    staende = list(zaehler.staende.select_related("uebergabe").order_by("-datum", "-pk"))
    stichtag = services.letzter_stichtag(_heute())
    return render(
        request,
        "zaehler/zaehler.html",
        {
            "zaehler": zaehler,
            "staende": staende,
            "vorgaenger": Zaehler.objects.filter(nachfolger=zaehler).first(),
            "stichtag": stichtag,
            "stand_stichtag": services.stand_zum_stichtag(zaehler, stichtag),
            **formulare,
        },
    )


@login_required
def foto(request: HttpRequest, pk: int) -> FileResponse:
    """Liefert das Foto eines Stands nur mit Anmeldung aus (nie direkt über Caddy)."""
    stand = get_object_or_404(Zaehlerstand, pk=pk)
    if not stand.foto:
        raise Http404("Kein Foto")
    return FileResponse(stand.foto.open("rb"))


# --- Ablesung am Handy (O2) ----------------------------------------------------------


@dataclass
class Zeile:
    zaehler: Zaehler
    letzter: Zaehlerstand | None
    wert: str = ""
    geschaetzt: bool = False
    fehler: str = ""
    gespeichert: Zaehlerstand | None = None


@login_required
def ablesung(request: HttpRequest, pk: int) -> HttpResponse:
    """Liste der Zähler eines Objekts; je Zähler Stand und Foto aus der Kamera.

    Was passt, wird gespeichert; ein abgelehnter Stand (z. B. kleiner als der letzte)
    bleibt mit Meldung im Formular stehen.
    """
    objekt = get_object_or_404(Objekt, pk=pk)
    zeilen = [Zeile(z, z.letzter_stand()) for z in services.zaehler_des_objekts(objekt)]
    datum = _heute()
    if request.method == "POST":
        try:
            datum = datetime.date.fromisoformat(request.POST.get("datum", ""))
            if datum > _heute():
                raise ValueError("Zukunft")
        except ValueError:
            datum = _heute()
            messages.error(request, "Bitte ein gültiges Datum angeben, nicht in der Zukunft.")
            return render(
                request,
                "zaehler/ablesung.html",
                {"objekt": objekt, "zeilen": zeilen, "datum": datum},
            )
        gespeichert = 0
        for zeile in zeilen:
            pk_z = zeile.zaehler.pk
            zeile.wert = request.POST.get(f"stand_{pk_z}", "").strip()
            zeile.geschaetzt = bool(request.POST.get(f"geschaetzt_{pk_z}"))
            datei = request.FILES.get(f"foto_{pk_z}")
            if not zeile.wert:
                if datei is not None:
                    zeile.fehler = "Zum Foto gehört ein Stand."
                continue
            if datei is not None and (fehler := foto_pruefen(datei)):
                zeile.fehler = fehler
                continue
            try:
                zeile.gespeichert = services.stand_erfassen(
                    zeile.zaehler,
                    datum,
                    zeile.wert,
                    art="geschaetzt" if zeile.geschaetzt else "abgelesen",
                    foto=datei,
                )
            except services.ZaehlerFehler as fehler:
                zeile.fehler = str(fehler)
            else:
                gespeichert += 1
        abgelehnt = [z for z in zeilen if z.fehler]
        if gespeichert:
            messages.success(request, f"{gespeichert} Stand/Stände gespeichert.")
        if not abgelehnt:
            return redirect(reverse("zaehler:ablesung", args=[objekt.pk]))
        messages.error(request, f"{len(abgelehnt)} Stand/Stände abgelehnt, bitte prüfen.")
        for zeile in zeilen:
            zeile.letzter = zeile.zaehler.letzter_stand()
            if zeile.gespeichert is not None:
                zeile.wert = ""
        return render(
            request,
            "zaehler/ablesung.html",
            {"objekt": objekt, "zeilen": zeilen, "datum": datum},
            status=200,
        )
    return render(
        request, "zaehler/ablesung.html", {"objekt": objekt, "zeilen": zeilen, "datum": datum}
    )


# --- Liste für den nächsten Besuch ---------------------------------------------------


@login_required
def besuch(request: HttpRequest) -> HttpResponse:
    """Zähler ohne Stand zum letzten Stichtag, ohne Foto oder ohne Eichfrist (und die übrigen
    Lücken des Datenstands), sortiert nach Objekt. Druckbar."""
    heute = _heute()
    luecken = {
        luecke.objekt_id: luecke
        for bereich in datenstand.ermitteln(heute)
        if bereich.schluessel == "zaehler"
        for luecke in bereich.luecken
    }
    stichtag = services.letzter_stichtag(heute)
    liste = Zaehler.objects.filter(pk__in=luecken).select_related(
        "einheit", "objekt", "einheit__objekt"
    )
    zeilen = sorted(
        (
            {
                "zaehler": z,
                "luecke": luecken[z.pk],
                "letzter": z.letzter_stand(),
                "objekt": z.standort_objekt,
            }
            for z in liste
        ),
        key=lambda zeile: (zeile["objekt"].kuerzel, zeile["zaehler"].ort, zeile["zaehler"].nummer),
    )
    gruppen: list[dict[str, Any]] = []
    for zeile in zeilen:
        if not gruppen or gruppen[-1]["objekt"] != zeile["objekt"]:
            gruppen.append({"objekt": zeile["objekt"], "zeilen": []})
        gruppen[-1]["zeilen"].append(zeile)
    return render(
        request,
        "zaehler/besuch.html",
        {"gruppen": gruppen, "anzahl": len(zeilen), "stichtag": stichtag, "heute": heute},
    )
