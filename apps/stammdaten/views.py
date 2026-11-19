"""Seiten der Stammdaten: Einheiten, Parteien, Verträge, Übergabe am Handy, Leerstand,
Datenstand. Gespeichert wird über ``services``; die Seiten prüfen nur die Eingaben."""

from __future__ import annotations

import datetime
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch, Q
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.dokumente.models import Schreiben
from apps.kern import zeit
from apps.kern.models import Objekt
from apps.stammdaten import (
    datenstand,
    einheitenliste,
    einheitenseite,
    leerstand,
    neuer_vertrag,
    schreiben,
    services,
    vertragsseite,
)
from apps.stammdaten import forms as f
from apps.stammdaten.models import (
    LAUFEND,
    Checkpunkt,
    Einheit,
    Kaution,
    Kontaktweg,
    Partei,
    Schluessel,
    Uebergabe,
    UebergabeFoto,
    Vertrag,
    Vertragspartei,
)
from apps.zaehler import services as zaehler_services

#: Ein Handler gibt den Anker zurück, oder (Anker, Meldung), wenn er selbst sagt, was
#: passiert ist (Review 2, B: Rückmeldungen mit Link).
Handler = Callable[[HttpRequest, Any, forms.Form], "str | tuple[str, str]"]


def _heute() -> datetime.date:
    return zeit.heute()


def _zurueck(url: str, anker: str = "") -> HttpResponse:
    return redirect(f"{url}#{anker}" if anker else url)


def _formular_seite(
    request: HttpRequest,
    objekt: Any,
    formulare: dict[str, forms.Form],
    handler: dict[str, Handler],
    vorlage: str,
    kontext: Callable[[], dict[str, Any]],
) -> HttpResponse:
    """Seite mit mehreren Formularen; ``aktion`` im POST wählt eines aus.

    Ein Handler bekommt das gültige Formular, ruft den Service und gibt den Anker zurück.
    Fehler aus dem Service erscheinen als Meldung, das Formular bleibt ausgefüllt.
    """
    if request.method == "POST":
        aktion = request.POST.get("aktion", "")
        if aktion not in formulare:
            raise Http404("Unbekannte Aktion")
        form = formulare[aktion]
        if form.is_valid():
            try:
                ergebnis = handler[aktion](request, objekt, form)
            except services.StammdatenFehler as fehler:
                messages.error(request, str(fehler))
            else:
                anker, meldung = ergebnis if isinstance(ergebnis, tuple) else (ergebnis, "")
                messages.success(request, meldung or "Gespeichert.")
                return _zurueck(request.path, anker)
        else:
            messages.error(request, "Bitte die markierten Felder prüfen.")
    return render(request, vorlage, {**kontext(), **formulare})


def _gebunden(request: HttpRequest, prefix: str, form_klasse: Any, **kwargs: Any) -> Any:
    """Bindet nur das Formular, dessen ``aktion`` gesendet wurde."""
    if request.method == "POST" and request.POST.get("aktion") == prefix:
        return form_klasse(request.POST, request.FILES, prefix=prefix, **kwargs)
    return form_klasse(prefix=prefix, **kwargs)


# --- Übersicht -----------------------------------------------------------------------


@login_required
def uebersicht(request: HttpRequest) -> HttpResponse:
    stand = datenstand.ermitteln(_heute())
    return render(
        request,
        "stammdaten/uebersicht.html",
        {
            "einheiten": Einheit.objects.filter(aufgeloest_am__isnull=True).count(),
            "parteien": Partei.objects.count(),
            "zaehlung": Vertrag.objects.zaehlung(),
            "luecken": sum(b.anzahl_luecken for b in stand),
        },
    )


# --- Einheiten -----------------------------------------------------------------------


@login_required
def einheiten(request: HttpRequest) -> HttpResponse:
    """Liste der Einheiten (Review 2, B11): Suche; Filter nach Typ, Objekt und Status;
    Spalten Mieter, Miete, offen (aus ``soll.offen``) und frei ab."""
    heute = _heute()
    alle = einheitenliste.lagen(heute, Einheit.objects.filter(aufgeloest_am__isnull=True))
    filter_ = {
        "suche": request.GET.get("suche", "").strip(),
        "typ": request.GET.get("typ", ""),
        "objekt": request.GET.get("objekt", ""),
        "status": request.GET.get("status", ""),
    }
    if filter_["typ"] not in Einheit.Typ.values:
        filter_["typ"] = ""
    if filter_["status"] not in dict(einheitenliste.STATUS_FILTER):
        filter_["status"] = ""
    liste = einheitenliste.filtern(alle, **filter_)
    zaehlung = {
        s: sum(1 for lage in alle if lage.status == s) for s, _ in einheitenliste.STATUS_FILTER
    }
    return render(
        request,
        "stammdaten/einheiten.html",
        {
            "lagen": liste,
            "anzahl_alle": len(alle),
            "filter": filter_,
            "gefiltert": any(filter_.values()),
            "typen": [(w, t) for w, t in Einheit.Typ.choices],
            "objekte": Objekt.objects.filter(einheiten__isnull=False)
            .distinct()
            .order_by("kuerzel"),
            "status_filter": [
                (s, t, zaehlung.get(s, 0) if s else len(alle))
                for s, t in einheitenliste.STATUS_FILTER
            ],
            "summe_offen": sum(lage.offen for lage in liste),
        },
    )


@login_required
def einheit_neu(request: HttpRequest) -> HttpResponse:
    form = f.EinheitNeuForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            einheit = services.einheit_anlegen(
                d["objekt"], d["typ"], bezeichnung=d["bezeichnung"], flaeche_qm=d["flaeche_qm"]
            )
        except services.StammdatenFehler as fehler:
            messages.error(request, str(fehler))
        else:
            messages.success(request, f"Einheit {einheit.nw_id} angelegt.")
            return redirect("stammdaten:einheit", einheit.pk)
    return render(request, "stammdaten/neu.html", {"form": form, "titel": "Neue Einheit"})


@login_required
def einheit(request: HttpRequest, pk: int) -> HttpResponse:
    """Einheitenseite in der neuen Gestaltung: Kopf, Reiter, Aktionen als Dialoge
    (``apps.stammdaten.einheitenseite``)."""
    return einheitenseite.seite(request, pk)


# --- Parteien ------------------------------------------------------------------------


@login_required
def parteien(request: HttpRequest) -> HttpResponse:
    """Liste der Parteien (Review 2, B11): Suche; Spalten Rolle und Verträge."""
    suche = request.GET.get("suche", "").strip()
    qs = neuer_vertrag.parteien_suchen(suche) if suche else Partei.objects.all()
    liste = list(
        qs.prefetch_related(
            "kontaktwege",
            Prefetch(
                "vertragsparteien",
                queryset=Vertragspartei.objects.select_related("vertrag").order_by(
                    "vertrag__nummer"
                ),
            ),
        ).order_by("nachname", "vorname", "firma", "pk")
    )
    zeilen = []
    for p in liste:
        vps = list(p.vertragsparteien.all())
        rollen = sorted({vp.get_rolle_display() for vp in vps})
        zeilen.append(
            {
                "partei": p,
                "rollen": rollen,
                "vertraege": [vp.vertrag for vp in vps],
                "laufend": sum(1 for vp in vps if vp.vertrag.status in LAUFEND),
                "kontakt": next(iter(p.kontaktwege.all()), None),
            }
        )
    return render(
        request,
        "stammdaten/parteien.html",
        {"zeilen": zeilen, "suche": suche, "anzahl_alle": Partei.objects.count()},
    )


@login_required
def partei_neu(request: HttpRequest) -> HttpResponse:
    form = f.ParteiForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            partei = services.partei_anlegen(**form.cleaned_data)
        except services.StammdatenFehler as fehler:
            messages.error(request, str(fehler))
        else:
            messages.success(request, f"Partei #{partei.pk} angelegt.")
            return redirect("stammdaten:partei", partei.pk)
    return render(request, "stammdaten/neu.html", {"form": form, "titel": "Neue Partei"})


@login_required
def partei(request: HttpRequest, pk: int) -> HttpResponse:
    partei = get_object_or_404(Partei, pk=pk)
    formulare: dict[str, forms.Form] = {
        "stammfelder": _gebunden(request, "stammfelder", f.ParteiForm, instance=partei),
        "kontaktwege": _gebunden(request, "kontaktwege", f.KontaktwegForm),
    }

    def stammfelder(_: HttpRequest, p: Partei, form: forms.Form) -> str:
        services.partei_aendern(p, **form.cleaned_data)
        return "stammfelder"

    def kontakt(_: HttpRequest, p: Partei, form: forms.Form) -> str:
        d = form.cleaned_data
        services.kontaktweg_hinzufuegen(p, d["art"], d["wert"], bevorzugt=d["bevorzugt"])
        return "kontaktwege"

    def kontext() -> dict[str, Any]:
        return {
            "partei": partei,
            "kontaktwege_liste": partei.kontaktwege.all(),
            "vertragsparteien": partei.vertragsparteien.select_related("vertrag"),
        }

    return _formular_seite(
        request,
        partei,
        formulare,
        {"stammfelder": stammfelder, "kontaktwege": kontakt},
        "stammdaten/partei.html",
        kontext,
    )


@login_required
@require_POST
def kontaktweg_entfernen(request: HttpRequest, pk: int) -> HttpResponse:
    kontaktweg = get_object_or_404(Kontaktweg, pk=pk)
    partei_pk = kontaktweg.partei_id
    services.kontaktweg_entfernen(kontaktweg)
    messages.success(request, "Kontaktweg entfernt.")
    return _zurueck(reverse("stammdaten:partei", args=[partei_pk]), "kontaktwege")


# --- Verträge ------------------------------------------------------------------------


#: Filter der Vertragsliste; Standard sind die laufenden (dieselbe Zahl wie überall).
VERTRAG_FILTER = (
    ("laufend", "laufend"),
    ("entwurf", "Entwürfe"),
    ("beendet", "beendet"),
    ("alle", "alle"),
)


@login_required
def vertraege(request: HttpRequest) -> HttpResponse:
    """Liste der Verträge im neuen Stil (Review 2, B11): Status als Reiter (dieselbe Zählung
    wie überall), Suche nach Nummer, Einheit und Mieter; Spalten Miete heute und offen."""
    heute = _heute()
    gruppe = request.GET.get("status", "laufend")
    if gruppe not in dict(VERTRAG_FILTER):
        gruppe = "laufend"
    suche = request.GET.get("suche", "").strip()
    auswahl = {
        "laufend": Vertrag.objects.laufend(),
        "entwurf": Vertrag.objects.filter(status=Vertrag.Status.ENTWURF),
        "beendet": Vertrag.objects.filter(status=Vertrag.Status.BEENDET),
        "alle": Vertrag.objects.all(),
    }[gruppe]
    for wort in suche.split():
        auswahl = auswahl.filter(
            Q(nummer__icontains=wort)
            | Q(einheiten__nw_id__icontains=wort)
            | Q(einheiten__bezeichnung__icontains=wort)
            | Q(vertragsparteien__partei__vorname__icontains=wort)
            | Q(vertragsparteien__partei__nachname__icontains=wort)
            | Q(vertragsparteien__partei__firma__icontains=wort)
        )
    liste = list(
        auswahl.distinct()
        .prefetch_related(
            "einheiten",
            "konditionen",
            Prefetch(
                "vertragsparteien",
                queryset=Vertragspartei.objects.filter(
                    rolle=Vertragspartei.Rolle.MIETER
                ).select_related("partei"),
                to_attr="mieter",
            ),
        )
        .order_by("nummer")
    )
    offen = einheitenliste.offen_je_vertrag([v.pk for v in liste], heute)
    zaehlung = Vertrag.objects.zaehlung()
    zeilen = [
        {
            "vertrag": v,
            "miete": next(
                (
                    k.miete
                    for k in v.konditionen.all()
                    if k.gueltig_ab <= max(heute, v.beginn)
                    and (k.gueltig_bis is None or k.gueltig_bis >= max(heute, v.beginn))
                ),
                None,
            ),
            "offen": offen.get(v.pk, Decimal("0.00")),
        }
        for v in liste
    ]
    basis = reverse("stammdaten:vertraege")
    return render(
        request,
        "stammdaten/vertraege.html",
        {
            "zeilen": zeilen,
            "gruppe": gruppe,
            "suche": suche,
            "reiter": [
                {
                    "schluessel": wert,
                    "titel": titel,
                    "url": f"{basis}?status={wert}",
                    "anzahl": zaehlung[wert],
                }
                for wert, titel in VERTRAG_FILTER
            ],
            "zaehlung": zaehlung,
        },
    )


def _dokument_melden(request: HttpRequest, ergebnis: schreiben.Ergebnis) -> None:
    if isinstance(ergebnis, Schreiben):
        messages.success(
            request,
            f"Vertragsdokument als PDF erzeugt (Schreiben #{ergebnis.pk}, Seite „Schreiben“).",
        )
    else:
        messages.warning(
            request, f"Kein PDF: {ergebnis.hinweis} (Arbeitsliste „Schreiben erstellen“)."
        )


@login_required
def vertrag_neu(request: HttpRequest) -> HttpResponse:
    """Neuer Vertrag als schmale Seite oder Dialog (``apps.stammdaten.neuer_vertrag``)."""
    return neuer_vertrag.seite(request)


@login_required
def partei_suche(request: HttpRequest) -> HttpResponse:
    """Treffer der Parteisuche im Formular „Neuer Vertrag“ (HTMX)."""
    return neuer_vertrag.partei_suche(request)


@login_required
def vertrag(request: HttpRequest, pk: int) -> HttpResponse:
    """Vertragsseite in der neuen Gestaltung: Kopf, Reiter, Aktionen als Dialoge
    (``apps.stammdaten.vertragsseite``)."""
    return vertragsseite.seite(request, pk)


@login_required
@require_POST
def vertragsdokument(request: HttpRequest, pk: int) -> HttpResponse:
    vertrag = get_object_or_404(Vertrag, pk=pk)
    try:
        ergebnis = services.vertragsdokument_erzeugen(vertrag)
    except services.StammdatenFehler as fehler:
        messages.error(request, str(fehler))
    else:
        _dokument_melden(request, ergebnis)
    return redirect("stammdaten:vertrag", pk)


@login_required
@require_POST
def checkpunkt(request: HttpRequest, pk: int) -> HttpResponse:
    punkt = get_object_or_404(Checkpunkt.objects.select_related("checkliste"), pk=pk)
    if punkt.erledigt_am is None:
        services.checkpunkt_erledigen(punkt, _heute())
    else:
        services.checkpunkt_oeffnen(punkt)
    ziel = request.POST.get("weiter", "")
    if not ziel.startswith("/") or ziel.startswith("//"):
        ziel = reverse("stammdaten:uebersicht")
    return redirect(ziel)


# --- Übergabe und Rückgabe am Handy (O1) ---------------------------------------------

#: Schritte der Rückgabe. Die Übergabe hat dazu den Schritt „Kaution“ vor der Unterschrift
#: (Review 2, A10): kein Schlüssel vor Unterschrift und Kaution (SOP, Grundregel 5).
SCHRITTE: tuple[tuple[str, str], ...] = (
    ("vorab", "Vorab"),
    ("zaehler", "Zählerstände"),
    ("fotos", "Fotos"),
    ("schluessel", "Schlüssel"),
    ("unterschrift", "Zustand und Unterschrift"),
    ("abschluss", "Abschluss"),
)
SCHRITTE_UEBERGABE: tuple[tuple[str, str], ...] = (
    *SCHRITTE[:4],
    ("kaution", "Kaution"),
    *SCHRITTE[4:],
)


def schritte_fuer(uebergabe: Uebergabe) -> tuple[tuple[str, str], ...]:
    return SCHRITTE_UEBERGABE if uebergabe.art == Uebergabe.Art.UEBERGABE else SCHRITTE


def _schritt_nummer(uebergabe: Uebergabe, name: str) -> int:
    for nummer, (schluessel, _) in enumerate(schritte_fuer(uebergabe), start=1):
        if schluessel == name:
            return nummer
    raise Http404("Unbekannter Schritt")


@login_required
@require_POST
def uebergabe_starten(request: HttpRequest, pk: int, art: str) -> HttpResponse:
    vertrag = get_object_or_404(Vertrag, pk=pk)
    try:
        uebergabe = services.uebergabe_beginnen(vertrag, art, _heute())
    except services.StammdatenFehler as fehler:
        messages.error(request, str(fehler))
        return redirect("stammdaten:vertrag", pk)
    return redirect("stammdaten:uebergabe", uebergabe.pk, 1)


def _datei_oder_none(datei: Any) -> Any:
    """Ein leeres Dateifeld ist kein Foto (Review 2, A1): ohne Namen oder ohne Inhalt."""
    if datei is None or not getattr(datei, "name", "") or not getattr(datei, "size", 0):
        return None
    return datei


def fotos_aus_post(request: HttpRequest, feld: str = "fotos") -> list[Any]:
    """Die gewählten Fotos eines Mehrfach-Dateifelds; ein leeres Feld heißt: kein Foto."""
    return [d for d in request.FILES.getlist(feld) if _datei_oder_none(d) is not None]


def _zaehler_aus_post(request: HttpRequest, uebergabe: Uebergabe) -> list[dict[str, Any]]:
    """Stand, Foto und Kennzeichen „geschätzt“ je Zähler der Einheiten des Vertrags."""
    zeilen: list[dict[str, Any]] = []
    for z in zaehler_services.zaehler_der_uebergabe(uebergabe):
        zeilen.append(
            {
                "zaehler": z.pk,
                "stand": request.POST.get(f"stand_{z.pk}", ""),
                "foto": _datei_oder_none(request.FILES.get(f"foto_{z.pk}")),
                "art": "geschaetzt" if request.POST.get(f"geschaetzt_{z.pk}") else "abgelesen",
            }
        )
    return zeilen


def _ueberspringen_oder_fehler(
    request: HttpRequest, uebergabe: Uebergabe, schritt: str, leer: bool
) -> str | None:
    """Ein Schritt ohne Inhalt (kein Zähler, kein Schlüssel) geht nur mit Begründung weiter."""
    begruendung = request.POST.get("begruendung", "").strip()
    if begruendung:
        services.uebergabe_ueberspringen(uebergabe, schritt, begruendung)
        return None
    if leer and schritt not in (uebergabe.uebersprungen or {}):
        titel = services.UEBERSPRINGBAR[schritt]
        return (
            f"Für „{titel}“ gibt es nichts zu erfassen. Bitte oben anlegen oder kurz "
            "begründen, warum der Schritt entfällt."
        )
    return None


def _uebergabe_post(request: HttpRequest, uebergabe: Uebergabe, schritt: str) -> str | None:
    """Speichert einen Schritt. Rückgabe: Fehlermeldung oder None."""
    if schritt == "vorab":
        form = f.UebergabeVorabForm(request.POST)
        if not form.is_valid():
            return "Bitte ein gültiges Datum angeben."
        services.uebergabe_speichern(uebergabe, datum=form.cleaned_data["datum"])
        if form.cleaned_data["vertrag_unterschrieben"] and not uebergabe.vertrag.unterschrieben_am:
            services.vertrag_unterschrieben(uebergabe.vertrag, form.cleaned_data["datum"])
    elif schritt == "zaehler":
        zeilen = _zaehler_aus_post(request, uebergabe)
        for zeile in zeilen:
            if zeile["foto"] is not None and (fehler := f.foto_pruefen(zeile["foto"])):
                return fehler
        if fehler := _ueberspringen_oder_fehler(request, uebergabe, "zaehler", not zeilen):
            return fehler
        services.uebergabe_speichern(uebergabe, zaehlerstaende=zeilen)
    elif schritt == "fotos":
        dateien = fotos_aus_post(request)
        for datei in dateien:
            if fehler := f.foto_pruefen(datei):
                return fehler
        for datei in dateien:
            services.uebergabe_foto_hinzufuegen(uebergabe, datei)
    elif schritt == "schluessel":
        eintraege = []
        alle = services.schluessel_der_uebergabe(uebergabe)
        for s in alle:
            wert = request.POST.get(f"schluessel_{s.pk}", "0") or "0"
            if not wert.isdigit() or int(wert) > s.anzahl:
                return f"Bitte für Schlüssel #{s.pk} eine Anzahl zwischen 0 und {s.anzahl} angeben."
            eintraege.append({"schluessel": s.pk, "anzahl": int(wert)})
        if fehler := _ueberspringen_oder_fehler(request, uebergabe, "schluessel", not alle):
            return fehler
        services.uebergabe_speichern(uebergabe, schluessel=eintraege)
    elif schritt == "kaution":
        kform = f.KautionSchrittForm(request.POST)
        if not kform.is_valid():
            return " ".join(" ".join(e) for e in kform.errors.values())
        d = kform.cleaned_data
        services.uebergabe_kaution_erfassen(
            uebergabe,
            keine=d["keine"],
            betrag=d["betrag"],
            art=d["art"] or "",
            eingang_am=d["eingang_am"],
            konto=d["konto"],
        )
    elif schritt == "unterschrift":
        datei = _datei_oder_none(request.FILES.get("unterschrift"))
        if datei is not None and (fehler := f.foto_pruefen(datei)):
            return fehler
        services.uebergabe_speichern(
            uebergabe,
            zustand=request.POST.get("zustand", ""),
            zurueckgelassen=request.POST.get("zurueckgelassen", ""),
            unterschrift=datei,
        )
    elif schritt == "abschluss":
        services.uebergabe_abschliessen(uebergabe)
    return None


def _schnell_anlegen(request: HttpRequest, uebergabe: Uebergabe, aktion: str) -> str | None:
    """Zähler anlegen bzw. Schlüssel erfassen direkt im Schritt. Rückgabe: Fehler oder None."""
    einheiten = uebergabe.vertrag.einheiten.all()
    if aktion == "zaehler_anlegen":
        zform = f.ZaehlerSchnellForm(request.POST, einheiten=einheiten)
        if not zform.is_valid():
            return "Bitte Einheit, Art und Zählernummer angeben."
        d = zform.cleaned_data
        try:
            zaehler = zaehler_services.zaehler_anlegen(d["nummer"], d["art"], einheit=d["einheit"])
        except zaehler_services.ZaehlerFehler as fehler:
            return str(fehler)
        messages.success(request, f"Zähler {zaehler.nummer} an {d['einheit'].nw_id} angelegt.")
        return None
    sform = f.SchluesselSchnellForm(request.POST, einheiten=einheiten)
    if not sform.is_valid():
        return "Bitte Einheit, Art und Anzahl der Schlüssel angeben."
    d = sform.cleaned_data
    services.schluessel_anlegen(
        d["einheit"],
        art=d["art"],
        anzahl=d["anzahl"],
        bezeichnung=d["bezeichnung"],
        inhaber=Schluessel.Inhaber.HOF,
    )
    messages.success(request, f"{d['anzahl']} Schlüssel für {d['einheit'].nw_id} erfasst.")
    return None


def _kaution_initial(vertrag: Vertrag) -> dict[str, Any]:
    """Vorbelegung des Kautionsschritts aus der Kaution am Vertrag."""
    kaution = getattr(vertrag, "kaution", None)
    if kaution is None or kaution.betrag is None:
        return {"art": Kaution.Art.UEBERWEISUNG}
    if kaution.betrag == 0:
        return {"keine": True}
    return {
        "betrag": kaution.betrag,
        "art": kaution.art or Kaution.Art.UEBERWEISUNG,
        "eingang_am": kaution.eingang_am,
        "konto": kaution.konto,
    }


@login_required
def uebergabe(request: HttpRequest, pk: int, schritt: int) -> HttpResponse:
    """Übergabe oder Rückgabe in Schritten; Fotos direkt aus der Kamera."""
    uebergabe = get_object_or_404(Uebergabe.objects.select_related("vertrag"), pk=pk)
    schritte = schritte_fuer(uebergabe)
    if not 1 <= schritt <= len(schritte):
        raise Http404("Unbekannter Schritt")
    schluessel_name, schritt_titel = schritte[schritt - 1]
    if uebergabe.status == Uebergabe.Status.ABGESCHLOSSEN:
        messages.info(request, f"{uebergabe} ist abgeschlossen.")
        return redirect("stammdaten:vertrag", uebergabe.vertrag_id)

    if request.method == "POST":
        aktion = request.POST.get("aktion", "")
        try:
            if aktion in ("zaehler_anlegen", "schluessel_anlegen"):
                fehler = _schnell_anlegen(request, uebergabe, aktion)
                if not fehler:
                    return redirect("stammdaten:uebergabe", uebergabe.pk, schritt)
            else:
                fehler = _uebergabe_post(request, uebergabe, schluessel_name)
        except services.StammdatenFehler as ausnahme:
            fehler = str(ausnahme)
        if fehler:
            messages.error(request, fehler)
        elif schluessel_name == "abschluss":
            messages.success(request, f"{uebergabe.get_art_display()} abgeschlossen.")
            return redirect("stammdaten:vertrag", uebergabe.vertrag_id)
        else:
            return redirect("stammdaten:uebergabe", uebergabe.pk, schritt + 1)

    uebergabe.refresh_from_db()
    vertrag = uebergabe.vertrag
    erfasst = {s.zaehler_id: s for s in uebergabe.zaehlerstaende.all()}
    zaehler_zeilen = [
        (z, erfasst.get(z.pk), z.letzter_stand(uebergabe.datum - datetime.timedelta(days=1)))
        for z in zaehler_services.zaehler_der_uebergabe(uebergabe)
    ]
    schluessel = services.schluessel_der_uebergabe(uebergabe)
    gespeichert = {int(e["schluessel"]): int(e["anzahl"]) for e in uebergabe.schluessel}
    einheiten = vertrag.einheiten.all()
    fehlt_roh = services.uebergabe_fehlt(uebergabe)
    fehlt = [
        (text, reverse("stammdaten:uebergabe", args=[uebergabe.pk, _schritt_nummer(uebergabe, n)]))
        for text, n in fehlt_roh
    ]
    zaehler_zeilen = [
        (z, stand, davor, f.zaehler_foto_feld(z.pk)) for z, stand, davor in zaehler_zeilen
    ]
    return render(
        request,
        "stammdaten/uebergabe.html",
        {
            "uebergabe": uebergabe,
            "vertrag": vertrag,
            "schritt": schritt,
            "schritt_name": schluessel_name,
            "schritt_titel": schritt_titel,
            "schritte": schritte,
            "anzahl_schritte": len(schritte),
            "vorab_form": f.UebergabeVorabForm(
                initial={
                    "datum": uebergabe.datum,
                    "vertrag_unterschrieben": vertrag.unterschrieben_am is not None,
                }
            ),
            "kaution_form": f.KautionSchrittForm(initial=_kaution_initial(vertrag)),
            "kondition": vertrag.kondition_am(max(uebergabe.datum, vertrag.beginn)),
            "zaehler_form": f.ZaehlerSchnellForm(einheiten=einheiten),
            "schluessel_form": f.SchluesselSchnellForm(einheiten=einheiten),
            "zaehler_zeilen": zaehler_zeilen,
            "zaehlerstaende": list(erfasst.values()),
            "schluessel_zeilen": [
                (s, gespeichert.get(s.pk, s.anzahl if not gespeichert else 0)) for s in schluessel
            ],
            "fotos": uebergabe.fotos.all(),
            "hindernisse": services.uebergabe_hindernisse(uebergabe),
            "fehlt": fehlt,
            "uebersprungen": uebergabe.uebersprungen or {},
            "kaution": getattr(vertrag, "kaution", None),
            "einheiten": einheiten,
            "fortschritt": _fortschritt(uebergabe, schritt, {n for _, n in fehlt_roh}),
            "fotos_form": f.UebergabeFotosForm(),
            "unterschrift_form": f.UebergabeUnterschriftForm(),
            "zurueck": (
                reverse("stammdaten:uebergabe", args=[uebergabe.pk, schritt - 1])
                if schritt > 1
                else reverse("stammdaten:vertrag", args=[vertrag.pk])
            ),
        },
    )


def _fortschritt(uebergabe: Uebergabe, schritt: int, mit_luecke: set[str]) -> list[dict[str, Any]]:
    """Fortschrittsleiste der Übergabe (Review 2, B8): je Schritt erledigt, aktuell, mit
    Lücke (schon besucht, aber es fehlt etwas) oder noch offen."""
    leiste = []
    for nummer, (name, titel) in enumerate(schritte_fuer(uebergabe), start=1):
        if nummer == schritt:
            zustand = "aktuell"
        elif nummer > schritt:
            zustand = "offen"
        elif name in mit_luecke:
            zustand = "luecke"
        else:
            zustand = "erledigt"
        leiste.append(
            {
                "nummer": nummer,
                "titel": titel,
                "zustand": zustand,
                "url": reverse("stammdaten:uebergabe", args=[uebergabe.pk, nummer]),
            }
        )
    return leiste


@login_required
def uebergabe_foto(request: HttpRequest, pk: int) -> FileResponse:
    """Liefert ein Foto nur mit Anmeldung aus (nie direkt über Caddy)."""
    foto = get_object_or_404(UebergabeFoto, pk=pk)
    return FileResponse(foto.bild.open("rb"))


# --- Leerstand und Datenstand --------------------------------------------------------


@login_required
def leerstand_seite(request: HttpRequest) -> HttpResponse:
    """Leerstand je Einheit mit Tagen frei und entgangenem Ertrag (H2.11)."""
    heute = _heute()
    try:
        bis = (
            datetime.date.fromisoformat(request.GET.get("bis", ""))
            if request.GET.get("bis")
            else heute
        )
        von = (
            datetime.date.fromisoformat(request.GET["von"])
            if request.GET.get("von")
            else bis - datetime.timedelta(days=364)
        )
    except ValueError:
        messages.error(request, "Ungültiges Datum.")
        von, bis = heute - datetime.timedelta(days=364), heute
    zeilen = leerstand.berechnen(von, bis)
    return render(
        request,
        "stammdaten/leerstand.html",
        {"zeilen": zeilen, "summe": leerstand.summe(zeilen), "von": von, "bis": bis},
    )
