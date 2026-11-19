"""Seiten der Zähler in der neuen Gestaltung (Review 2, C; R2-9): Übersicht, Zähler mit Ständen
und Wechsel, Ablesung am Handy (O2), Liste für den nächsten Besuch (druckbar).

- **Aktionen als Dialoge** (``apps.kern.htmx``): Neuer Zähler, Stand erfassen, Stammfelder,
  Zählerwechsel öffnen ``?dialog=<aktion>``. Das Speichern ersetzt Kopf und Stände (die Seite
  springt nicht) und meldet mit Link, was passiert ist. Ohne JavaScript bleibt der alte Weg:
  POST mit ``aktion``, dann Umleitung.
- **Ablesung am Handy:** je Zähler eine Karte mit Stand (Zahlentastatur), Kameraknopf,
  „geschätzt“ und dem vorigen Stand als Hilfe. Nach dem Speichern geht es zum nächsten Objekt.

Gespeichert wird nur über ``services``.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import format_html

from apps.kern import htmx, zahlen
from apps.kern.models import Objekt
from apps.stammdaten import datenstand
from apps.stammdaten.forms import Datum
from apps.zaehler import forms as f
from apps.zaehler import services
from apps.zaehler.models import Zaehler, Zaehlerstand


def _heute() -> datetime.date:
    return datetime.date.today()


# --- Übersicht -----------------------------------------------------------------------


def _gruppen(stichtag: datetime.date) -> list[dict[str, Any]]:
    gruppen = []
    for objekt in Objekt.objects.filter(aktiv=True).order_by("kuerzel"):
        zaehler = services.zaehler_des_objekts(objekt, nur_eingebaut=False)
        eingebaut = [z for z in zaehler if z.ist_eingebaut]
        for z in zaehler:
            z.letzter = z.letzter_stand()  # type: ignore[attr-defined]
        ohne_stand = [
            z
            for z in eingebaut
            if services.braucht_stand(z, stichtag)
            and services.stand_zum_stichtag(z, stichtag) is None
        ]
        gruppen.append(
            {
                "objekt": objekt,
                "eingebaut": eingebaut,
                "ausgebaut": [z for z in zaehler if not z.ist_eingebaut],
                "ohne_stand": len(ohne_stand),
            }
        )
    return gruppen


@login_required
def uebersicht(request: HttpRequest) -> HttpResponse:
    """Alle Zähler nach Objekt; je Objekt eine Kachel zur Ablesung."""
    stichtag = services.letzter_stichtag(_heute())
    return render(
        request,
        "zaehler/uebersicht.html",
        {"objektgruppen": _gruppen(stichtag), "stichtag": stichtag},
    )


def _neu_dialog(request: HttpRequest, form: forms.Form) -> HttpResponse:
    return render(
        request,
        "bausteine/dialog_formular.html",
        {
            "titel": "Neuer Zähler",
            "form": form,
            "url": reverse("zaehler:neu"),
            "hinweis": "Ein Zähler hängt an einer Einheit oder, als Hauptzähler, an einem "
            "Objekt. Wohnraum führt Verwaltio.",
            "knopf": "Anlegen",
        },
    )


@login_required
def neu(request: HttpRequest) -> HttpResponse:
    """Neuer Zähler: im Dialog der Übersicht (HTMX) oder als eigene Seite."""
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
            form.add_error(None, str(fehler))
        else:
            text = f"{zaehler.get_art_display()} {zaehler.nummer} angelegt · {zaehler.ort}"
            link = reverse("zaehler:zaehler", args=[zaehler.pk])
            if htmx.ist_htmx(request):
                stichtag = services.letzter_stichtag(_heute())
                teil = render_to_string(
                    "zaehler/_liste.html",
                    {"objektgruppen": _gruppen(stichtag), "stichtag": stichtag, "oob": True},
                    request=request,
                )
                return htmx.erfolg(request, [teil], text, link=link, link_text="Zähler ansehen")
            messages.success(request, text)
            return redirect(link)
    if htmx.ist_htmx(request):
        return _neu_dialog(request, form)
    return render(request, "zaehler/neu.html", {"form": form})


# --- Ein Zähler ----------------------------------------------------------------------


@dataclass(frozen=True)
class Dialog:
    titel: str
    knopf: str
    hinweis: str = ""
    kamera: bool = False


DIALOGE: dict[str, Dialog] = {
    "stand": Dialog(
        "Stand erfassen",
        "Stand speichern",
        "Ein Stand, der kleiner ist als der letzte, wird abgelehnt (außer nach einem Wechsel).",
        kamera=True,
    ),
    "stammfelder": Dialog("Stammfelder ändern", "Speichern"),
    "wechsel": Dialog(
        "Zähler wechseln",
        "Zähler wechseln",
        "Ausbaustand des alten und Einbaustand des neuen Zählers in einem Vorgang. Danach "
        "gehören neue Stände an den neuen Zähler.",
        kamera=True,
    ),
}


#: Feld, an dem ein unplausibler Stand (``services.StandUnplausibel``) gemeldet wird.
FELD_DES_STANDS = {"stand": "stand", "wechsel": "ausbau_stand"}


def _formular(request: HttpRequest, aktion: str, klasse: Any, **kwargs: Any) -> Any:
    if request.method == "POST" and request.POST.get("aktion") == aktion:
        return klasse(request.POST, request.FILES, prefix=aktion, **kwargs)
    return klasse(prefix=aktion, **kwargs)


def _formulare(request: HttpRequest, zaehler: Zaehler) -> dict[str, forms.Form]:
    alle = {
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
    }
    if zaehler.ist_eingebaut:
        letzter = zaehler.letzter_stand()
        stand = _formular(request, "stand", f.StandForm, initial={"datum": _heute()})
        if letzter is not None:
            stand.fields["stand"].help_text = _voriger_text(letzter)
        alle["stand"] = stand
        alle["wechsel"] = _formular(request, "wechsel", f.WechselForm, initial={"datum": _heute()})
    return alle


def _voriger_text(stand: Zaehlerstand) -> str:
    text = f"Voriger Stand: {zahlen.zahl(stand.stand_text)} am {stand.datum:%d.%m.%Y}"
    return text + (" (geschätzt)" if stand.ist_geschaetzt else "")


def _teile(zaehler: Zaehler, *, oob: bool = False) -> dict[str, Any]:
    stichtag = services.letzter_stichtag(_heute())
    return {
        "zaehler": zaehler,
        "staende": list(zaehler.staende.select_related("uebergabe").order_by("-datum", "-pk")),
        "vorgaenger": Zaehler.objects.filter(nachfolger=zaehler).first(),
        "stichtag": stichtag,
        "stand_stichtag": services.stand_zum_stichtag(zaehler, stichtag),
        "basis": reverse("zaehler:zaehler", args=[zaehler.pk]),
        "oob": oob,
    }


def _dialog(request: HttpRequest, zaehler: Zaehler, aktion: str, form: forms.Form) -> HttpResponse:
    dialog = DIALOGE[aktion]
    return render(
        request,
        "bausteine/dialog_formular.html",
        {
            "titel": f"{dialog.titel} · {zaehler.nummer}",
            "form": form,
            "url": reverse("zaehler:zaehler", args=[zaehler.pk]),
            "aktion": aktion,
            "hinweis": dialog.hinweis,
            "knopf": dialog.knopf,
            "kamera": dialog.kamera,
        },
    )


def _speichern(zaehler: Zaehler, aktion: str, d: dict[str, Any]) -> tuple[Zaehler, str, str]:
    """Führt die Aktion aus. Rückgabe: Zähler, dessen Seite danach gilt, Meldung, Link-Text."""
    if aktion == "stammfelder":
        services.zaehler_aendern(zaehler, **d)
        return zaehler, f"Stammfelder von Zähler {d['nummer']} gespeichert", "ansehen"
    if aktion == "stand":
        stand = services.stand_erfassen(
            zaehler,
            d["datum"],
            d["stand"],
            art="geschaetzt" if d["geschaetzt"] else "abgelesen",
            foto=d["foto"],
            vermerk=d["vermerk"],
        )
        text = f"Stand {zahlen.zahl(stand.stand_text)} vom {stand.datum:%d.%m.%Y} gespeichert"
        if stand.ist_geschaetzt:
            text += " (geschätzt)"
        if stand.foto:
            text += " · mit Foto"
        return zaehler, text, "Stände ansehen"
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
    text = (
        f"Zähler {zaehler.nummer} am {d['datum']:%d.%m.%Y} ausgebaut, "
        f"{wechsel.neuer.nummer} eingebaut"
    )
    return wechsel.neuer, text, "neuen Zähler ansehen"


@login_required
def zaehler(request: HttpRequest, pk: int) -> HttpResponse:
    """Kopf mit Stammdaten, Stände (mit Kennzeichen „geschätzt“); Stand erfassen,
    Stammfelder und Zählerwechsel als Dialoge."""
    zaehler = get_object_or_404(
        Zaehler.objects.select_related("einheit", "objekt", "nachfolger"), pk=pk
    )
    alle = _formulare(request, zaehler)
    if request.method == "GET":
        if aktion := request.GET.get("dialog"):
            if aktion not in alle:
                raise Http404("Diese Aktion gibt es für diesen Zähler nicht.")
            return _dialog(request, zaehler, aktion, alle[aktion])
        return render(request, "zaehler/zaehler.html", _teile(zaehler))

    aktion = request.POST.get("aktion", "")
    if aktion not in alle:
        raise Http404("Unbekannte Aktion")
    form = alle[aktion]
    if form.is_valid():
        try:
            ziel, text, link_text = _speichern(zaehler, aktion, form.cleaned_data)
        except services.StandUnplausibel as fehler:
            # Der Grund gehört an das Feld des Stands (Review 2, B: Fehler am Feld).
            form.add_error(FELD_DES_STANDS.get(aktion), str(fehler))
        except services.ZaehlerFehler as fehler:
            form.add_error(None, str(fehler))
        else:
            link = reverse("zaehler:zaehler", args=[ziel.pk])
            if ziel == zaehler:
                link += "#staende"
            if htmx.ist_htmx(request):
                zaehler.refresh_from_db()
                kontext = _teile(zaehler, oob=True)
                teile = [
                    render_to_string("zaehler/_kopf.html", kontext, request=request),
                    render_to_string("zaehler/_staende.html", kontext, request=request),
                ]
                return htmx.erfolg(request, teile, text, link=link, link_text=link_text)
            messages.success(
                request, format_html('{} · <a href="{}">{}</a>', text, link, link_text)
            )
            return redirect("zaehler:zaehler", ziel.pk)
    if htmx.ist_htmx(request):
        return _dialog(request, zaehler, aktion, form)
    # Ohne JavaScript: Seite mit dem Formular und seinen Fehlern
    for fehler in form.non_field_errors():
        messages.error(request, fehler)
    if not form.non_field_errors():
        messages.error(request, "Bitte die markierten Angaben prüfen.")
    return render(
        request,
        "zaehler/zaehler.html",
        _teile(zaehler)
        | {"offenes_formular": form, "offene_aktion": aktion, "offener_dialog": DIALOGE[aktion]},
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
    stand: forms.BoundField | None = None
    geschaetzt: forms.BoundField | None = None
    foto: forms.BoundField | None = None
    gespeichert: Zaehlerstand | None = None


class AblesungForm(forms.Form):
    """Datum und je Zähler Stand, „geschätzt“ und Foto (Felder ``stand_<ID>`` usw.)."""

    datum = forms.DateField(label="Datum der Ablesung", widget=Datum())

    def __init__(self, *args: Any, zaehler: list[Zaehler], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["datum"].error_messages["invalid"] = "Bitte ein gültiges Datum angeben."
        self.fields["datum"].error_messages["required"] = "Bitte das Datum der Ablesung angeben."
        for z in zaehler:
            letzter = z.letzter_stand()
            self.fields[f"stand_{z.pk}"] = forms.CharField(
                label="Stand",
                required=False,
                max_length=30,
                help_text=_voriger_text(letzter) if letzter else "Noch kein Stand erfasst.",
                widget=forms.TextInput(attrs={"inputmode": "decimal", "autocomplete": "off"}),
            )
            self.fields[f"geschaetzt_{z.pk}"] = forms.BooleanField(
                label="geschätzt (nicht abgelesen)", required=False
            )
            self.fields[f"foto_{z.pk}"] = forms.FileField(
                label="Foto des Zählers",
                required=False,
                widget=forms.ClearableFileInput(attrs=f.FOTO_ATTRS),
            )

    def clean_datum(self) -> datetime.date:
        datum: datetime.date = self.cleaned_data["datum"]
        if datum > _heute():
            raise forms.ValidationError("Bitte ein gültiges Datum angeben, nicht in der Zukunft.")
        return datum


def _naechstes_objekt(objekt: Objekt) -> Objekt | None:
    """Das nächste Objekt mit eingebauten Zählern (für „weiter zu …“ nach dem Speichern)."""
    objekte = [o for o in Objekt.objects.filter(aktiv=True).order_by("kuerzel")]
    if objekt not in objekte:
        return None
    for o in objekte[objekte.index(objekt) + 1 :] + objekte[: objekte.index(objekt)]:
        if services.zaehler_des_objekts(o):
            return o
    return None


@login_required
def ablesung(request: HttpRequest, pk: int) -> HttpResponse:
    """Je Zähler des Objekts eine Karte: Stand, „geschätzt“, Foto aus der Kamera.

    Was passt, wird gespeichert; ein abgelehnter Stand (z. B. kleiner als der letzte) bleibt
    mit dem Grund am Feld stehen. Nach dem Speichern geht es zum nächsten Objekt.
    """
    objekt = get_object_or_404(Objekt, pk=pk)
    liste = services.zaehler_des_objekts(objekt)
    form = AblesungForm(
        request.POST if request.method == "POST" else None,
        request.FILES if request.method == "POST" else None,
        zaehler=liste,
        initial={"datum": _heute()},
    )
    zeilen = [Zeile(z, z.letzter_stand()) for z in liste]
    if request.method == "POST" and form.is_valid():
        datum = form.cleaned_data["datum"]
        gespeichert = 0
        for zeile in zeilen:
            pk_z = zeile.zaehler.pk
            wert = (form.cleaned_data.get(f"stand_{pk_z}") or "").strip()
            datei = form.cleaned_data.get(f"foto_{pk_z}")
            if not wert:
                if datei:
                    form.add_error(f"stand_{pk_z}", "Zum Foto gehört ein Stand.")
                continue
            if datei and (fehler := f.foto_pruefen(datei)):
                form.add_error(f"foto_{pk_z}", fehler)
                continue
            try:
                zeile.gespeichert = services.stand_erfassen(
                    zeile.zaehler,
                    datum,
                    wert,
                    art="geschaetzt"
                    if form.cleaned_data.get(f"geschaetzt_{pk_z}")
                    else "abgelesen",
                    foto=datei or None,
                )
            except services.ZaehlerFehler as fehler:
                form.add_error(f"stand_{pk_z}", str(fehler))
            else:
                gespeichert += 1
        abgelehnt = sum(
            1
            for z in zeilen
            if form[f"stand_{z.zaehler.pk}"].errors or form[f"foto_{z.zaehler.pk}"].errors
        )
        naechstes = _naechstes_objekt(objekt)
        if not abgelehnt:
            text = (
                f"{gespeichert} Stand gespeichert ({objekt.kuerzel})"
                if gespeichert == 1
                else f"{gespeichert} Stände gespeichert ({objekt.kuerzel})"
            )
            if naechstes is not None:
                url = reverse("zaehler:ablesung", args=[naechstes.pk])
                messages.success(
                    request,
                    format_html('{} · <a href="{}">weiter zu {}</a>', text, url, naechstes.kuerzel),
                )
            else:
                messages.success(request, text)
            return redirect(reverse("zaehler:ablesung", args=[objekt.pk]))
        if gespeichert:
            messages.success(request, f"{gespeichert} Stand/Stände gespeichert.")
        messages.error(request, f"{abgelehnt} Stand/Stände abgelehnt; der Grund steht am Feld.")
        # Gespeicherte Felder leeren, abgelehnte stehen lassen.
        daten = request.POST.copy()
        for zeile in zeilen:
            if zeile.gespeichert is not None:
                daten[f"stand_{zeile.zaehler.pk}"] = ""
                daten.pop(f"geschaetzt_{zeile.zaehler.pk}", None)
        fehler_alt = form.errors
        form = AblesungForm(daten, zaehler=liste)
        form.is_valid()
        for name, liste_fehler in fehler_alt.items():
            if name.startswith(("stand_", "foto_")) and not form.errors.get(name):
                for fehler_text in liste_fehler:
                    form.add_error(name, fehler_text)
        zeilen = [
            Zeile(z, z.letzter_stand(), gespeichert=alt.gespeichert)
            for z, alt in zip(liste, zeilen, strict=True)
        ]
    elif request.method == "POST":
        messages.error(
            request, " ".join(form.errors.get("datum", ["Bitte die markierten Angaben prüfen."]))
        )
    for zeile in zeilen:
        pk_z = zeile.zaehler.pk
        zeile.stand = form[f"stand_{pk_z}"]
        zeile.geschaetzt = form[f"geschaetzt_{pk_z}"]
        zeile.foto = form[f"foto_{pk_z}"]
    return render(
        request,
        "zaehler/ablesung.html",
        {"objekt": objekt, "zeilen": zeilen, "form": form, "naechstes": _naechstes_objekt(objekt)},
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
        {"objektgruppen": gruppen, "anzahl": len(zeilen), "stichtag": stichtag, "heute": heute},
    )
