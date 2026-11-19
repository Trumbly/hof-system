"""Seite „Fristen“ (Schritt 9; Review 2, B10): kompakte Tabelle, gruppiert in „überfällig“,
„nächste 14 Tage“ und „später“. Eichfristen stehen gebündelt in einer Zeile (aufklappbar).
„Erledigt“ und „verschieben“ (+1 Woche, +1 Monat, Datum) gehen direkt in der Zeile per HTMX;
die Zeile wird ersetzt, eine Meldung sagt, was passiert ist.

Filter nach Zeitraum, Art und Quelle stehen zugeklappt darüber. Geschrieben wird nur über
``apps.fristen.services``.
"""

from __future__ import annotations

import calendar
import datetime
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import BooleanField, Case, Count, Q, QuerySet, Value, When
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.fristen import services
from apps.fristen.models import Frist
from apps.kern import htmx

#: Zeiträume der Liste: Schlüssel, Titel. „offen“ zeigt alle offenen, auch überfällige.
ZEITRAEUME: tuple[tuple[str, str], ...] = (
    ("offen", "alle offenen"),
    ("ueberfaellig", "überfällig"),
    ("14", "offen, bis in 14 Tagen"),
    ("30", "offen, bis in 30 Tagen"),
    ("90", "offen, bis in 90 Tagen"),
    ("erledigt", "erledigt"),
    ("alle", "alle"),
)
STANDARD_ZEITRAUM = "offen"
#: Gruppen der offenen Fristen (Review 2, B10)
NAECHSTE_TAGE = 14
#: Ab so vielen Fristen einer Art in einer Gruppe stehen sie gebündelt in einer Zeile.
BUENDEL_AB = 2
BUENDEL_ARTEN = (Frist.Art.EICHFRIST,)
#: Verschieben direkt in der Zeile (B10); dazu „Datum setzen“.
VERSCHIEBUNGEN = (("woche", "+1 Woche"), ("monat", "+1 Monat"))


def _ist_htmx(request: HttpRequest) -> bool:
    return request.headers.get("HX-Request") == "true"


def _mit_angaben(fristen: QuerySet[Frist]) -> QuerySet[Frist]:
    return fristen.select_related("bezug_typ").annotate(anzahl_verschoben=Count("verschiebungen"))


def sortiert(fristen: QuerySet[Frist], heute: datetime.date) -> QuerySet[Frist]:
    """Überfällige zuerst, dann aufsteigend nach Datum (Review 2, A5).

    Ausdrücklich sortiert: Mit ``annotate(Count(...))`` gilt ``Meta.ordering`` nicht.
    """
    return fristen.annotate(
        ist_ueberfaellig=Case(
            When(Q(erledigt_am__isnull=True, datum__lt=heute), then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        )
    ).order_by("-ist_ueberfaellig", "datum", "pk")


def _nach_zeitraum(
    fristen: QuerySet[Frist], zeitraum: str, heute: datetime.date
) -> QuerySet[Frist]:
    if zeitraum == "alle":
        return sortiert(fristen, heute)
    if zeitraum == "erledigt":
        return fristen.filter(erledigt_am__isnull=False).order_by("-erledigt_am", "-pk")
    fristen = fristen.filter(erledigt_am__isnull=True)
    if zeitraum == "ueberfaellig":
        return sortiert(fristen.filter(datum__lt=heute), heute)
    if zeitraum.isdigit():
        fristen = fristen.filter(datum__lte=heute + datetime.timedelta(days=int(zeitraum)))
    return sortiert(fristen, heute)


# --- Bezug lesbar ---------------------------------------------------------------------


def _url(name: str, pk: int) -> str:
    try:
        return reverse(name, args=[pk])
    except NoReverseMatch:
        return ""


def bezug_anzeige(frist: Frist) -> tuple[str, str]:
    """(Text, Link) des Bezugs, so wie Max ihn kennt: V-Nummer, Zähler, Konto, Rechnung."""
    bezug: Any = frist.bezug
    modell = frist.bezug_typ.model
    if bezug is None:
        return f"{frist.bezug_typ.name} #{frist.bezug_id} (fehlt)", ""
    if modell == "vertrag":
        return str(bezug.nummer), _url("stammdaten:vertrag", bezug.pk)
    vertrag = getattr(bezug, "vertrag", None)
    if vertrag is not None and getattr(vertrag, "nummer", ""):
        return f"{frist.bezug_typ.name} {vertrag.nummer}", _url("stammdaten:vertrag", vertrag.pk)
    if modell == "zaehler":
        return f"Zähler {bezug.nummer}", _url("zaehler:zaehler", bezug.pk)
    if modell == "eingangsrechnung":
        nummer = bezug.rechnungsnummer or f"#{bezug.pk}"
        return f"Rechnung {nummer}", _url("dokumente:rechnung", bezug.pk)
    if modell == "bankfreigabe":
        return f"Bankfreigabe {bezug.konto.bezeichnung}", reverse("bank:konten")
    if modell == "objekt":
        return f"Objekt {bezug.kuerzel}", ""
    return f"{frist.bezug_typ.name} #{frist.bezug_id}", ""


def _vorbereiten(fristen: Iterable[Frist]) -> list[Frist]:
    liste = list(fristen)
    for frist in liste:
        text, url = bezug_anzeige(frist)
        frist.bezug_text = text  # type: ignore[attr-defined]
        frist.bezug_url = url  # type: ignore[attr-defined]
    return liste


# --- Gruppen und Bündel --------------------------------------------------------------


@dataclass
class Buendel:
    """Mehrere Fristen einer Art in einer Zeile: „14 Eichfristen 2027–2033“."""

    art: str
    fristen: list[Frist] = field(default_factory=list)

    @property
    def titel(self) -> str:
        jahre = sorted({f.datum.year for f in self.fristen})
        spanne = f"{jahre[0]}–{jahre[-1]}" if jahre[0] != jahre[-1] else str(jahre[0])
        name = self.fristen[0].get_art_display()
        mehrzahl = name if name.endswith("en") else f"{name}en"
        return f"{len(self.fristen)} {mehrzahl} {spanne}"


@dataclass
class Gruppe:
    schluessel: str
    titel: str
    #: Fristen und Bündel in der Reihenfolge der Liste
    zeilen: list[Frist | Buendel] = field(default_factory=list)
    anzahl: int = 0


def _buendeln(fristen: list[Frist]) -> list[Frist | Buendel]:
    zaehlung: dict[str, int] = {}
    for frist in fristen:
        if frist.art in BUENDEL_ARTEN:
            zaehlung[frist.art] = zaehlung.get(frist.art, 0) + 1
    zeilen: list[Frist | Buendel] = []
    buendel: dict[str, Buendel] = {}
    for frist in fristen:
        if zaehlung.get(frist.art, 0) >= BUENDEL_AB:
            if frist.art not in buendel:
                buendel[frist.art] = Buendel(art=frist.art)
                zeilen.append(buendel[frist.art])
            buendel[frist.art].fristen.append(frist)
        else:
            zeilen.append(frist)
    return zeilen


def gruppieren(fristen: list[Frist], heute: datetime.date, zeitraum: str) -> list[Gruppe]:
    """Offene Fristen: überfällig · nächste 14 Tage · später. „erledigt“ und „alle“ bleiben
    eine Liste in ihrer Sortierung."""
    if zeitraum in ("erledigt", "alle"):
        titel = dict(ZEITRAEUME)[zeitraum]
        gruppen = [Gruppe(zeitraum, titel.capitalize(), anzahl=len(fristen))]
        gruppen[0].zeilen = _buendeln(fristen)
        return gruppen
    grenze = heute + datetime.timedelta(days=NAECHSTE_TAGE)
    teile: dict[str, list[Frist]] = {"ueberfaellig": [], "bald": [], "spaeter": []}
    for frist in fristen:
        if frist.datum < heute:
            teile["ueberfaellig"].append(frist)
        elif frist.datum <= grenze:
            teile["bald"].append(frist)
        else:
            teile["spaeter"].append(frist)
    titel = {
        "ueberfaellig": "Überfällig",
        "bald": f"Nächste {NAECHSTE_TAGE} Tage",
        "spaeter": "Später",
    }
    return [
        Gruppe(schluessel, titel[schluessel], _buendeln(liste), len(liste))
        for schluessel, liste in teile.items()
        if liste
    ]


# --- Seite ---------------------------------------------------------------------------


@login_required
def fristenliste(request: HttpRequest) -> HttpResponse:
    heute = timezone.localdate()
    zeitraum = request.GET.get("zeitraum", STANDARD_ZEITRAUM)
    if zeitraum not in dict(ZEITRAEUME):
        zeitraum = STANDARD_ZEITRAUM
    art = request.GET.get("art", "")
    if art not in Frist.Art.values:
        art = ""
    quelle = request.GET.get("quelle", "")
    if quelle not in Frist.Quelle.values:
        quelle = ""

    fristen = _mit_angaben(Frist.objects.all())
    if art:
        fristen = fristen.filter(art=art)
    if quelle:
        fristen = fristen.filter(quelle=quelle)
    liste = _vorbereiten(_nach_zeitraum(fristen, zeitraum, heute).prefetch_related("bezug"))
    gruppen = gruppieren(liste, heute, zeitraum)
    ueberfaellig = sum(g.anzahl for g in gruppen if g.schluessel == "ueberfaellig")
    meta = f"{len(liste)} Frist{'en' if len(liste) != 1 else ''}"
    if ueberfaellig:
        meta += f" · {ueberfaellig} überfällig"
    return render(
        request,
        "fristen/liste.html",
        {
            "fristen": liste,
            "fristgruppen": gruppen,
            "meta": meta,
            "heute": heute,
            "zeitraeume": ZEITRAEUME,
            "arten": Frist.Art.choices,
            "quellen": Frist.Quelle.choices,
            "gewaehlt": {"zeitraum": zeitraum, "art": art, "quelle": quelle},
            "gefiltert": bool(art or quelle or zeitraum != STANDARD_ZEITRAUM),
            "verschiebungen": VERSCHIEBUNGEN,
        },
    )


def _zeile(request: HttpRequest, frist: Frist, **weiteres: Any) -> str:
    frist = _vorbereiten(_mit_angaben(Frist.objects.filter(pk=frist.pk)))[0]
    return render_to_string(
        "fristen/_frist.html",
        {
            "frist": frist,
            "heute": timezone.localdate(),
            "verschiebungen": VERSCHIEBUNGEN,
            **weiteres,
        },
        request=request,
    )


def _zurueck(request: HttpRequest) -> HttpResponse:
    zurueck = request.POST.get("zurueck", "")
    if not url_has_allowed_host_and_scheme(
        zurueck, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        zurueck = reverse("fristen:liste")
    return redirect(zurueck)


@login_required
@require_POST
def erledigt(request: HttpRequest, pk: int) -> HttpResponse:
    """Setzt das Erledigt-Datum auf heute (per HTMX oder als normales Formular)."""
    frist = get_object_or_404(Frist, pk=pk)
    if frist.ist_erledigt:
        return HttpResponseBadRequest("Die Frist ist schon erledigt.")
    frist = services.frist_erledigen(frist)
    text = f"Frist #{frist.pk} erledigt ({frist.get_art_display()}, {frist.datum:%d.%m.%Y})."
    if _ist_htmx(request):
        return HttpResponse(
            _zeile(request, frist, gerade_erledigt=True) + htmx.toast(request, text)
        )
    messages.success(request, text)
    return _zurueck(request)


def monat_spaeter(datum: datetime.date, monate: int = 1) -> datetime.date:
    """Derselbe Tag im nächsten Monat (31.01. → 28./29.02.)."""
    jahr, monat = divmod(datum.month - 1 + monate, 12)
    jahr += datum.year
    monat += 1
    tag = min(datum.day, calendar.monthrange(jahr, monat)[1])
    return datetime.date(jahr, monat, tag)


def neues_datum(frist: Frist, um: str, datum: str, heute: datetime.date) -> datetime.date | None:
    """+1 Woche oder +1 Monat ab dem Datum der Frist (eine überfällige ab heute), oder ein
    gewähltes Datum. Ungültiges: None."""
    basis = max(frist.datum, heute)
    if um == "woche":
        return basis + datetime.timedelta(days=7)
    if um == "monat":
        return monat_spaeter(basis)
    try:
        return datetime.date.fromisoformat(datum)
    except ValueError:
        return None


@login_required
@require_POST
def verschieben(request: HttpRequest, pk: int) -> HttpResponse:
    """Verschiebt eine offene Frist direkt aus der Zeile (Review 2, B10)."""
    frist = get_object_or_404(Frist, pk=pk)
    heute = timezone.localdate()
    if frist.ist_erledigt:
        return HttpResponseBadRequest("Die Frist ist erledigt und lässt sich nicht verschieben.")
    ziel = neues_datum(frist, request.POST.get("um", ""), request.POST.get("datum", ""), heute)
    if ziel is None:
        fehler = "Bitte ein gültiges Datum wählen."
        if _ist_htmx(request):
            return HttpResponse(_zeile(request, frist) + htmx.toast(request, fehler, art="fehler"))
        messages.error(request, fehler)
        return _zurueck(request)
    grund = {"woche": "um eine Woche", "monat": "um einen Monat"}.get(
        request.POST.get("um", ""), "auf ein gewähltes Datum"
    )
    frist = services.frist_verschieben(frist, ziel, grund=f"In der Liste {grund}", heute=heute)
    text = f"Frist #{frist.pk} verschoben auf {frist.datum:%d.%m.%Y}."
    if _ist_htmx(request):
        return HttpResponse(_zeile(request, frist) + htmx.toast(request, text))
    messages.success(request, text)
    return _zurueck(request)
