"""Beleg erfassen in der neuen Gestaltung (Review 2, B9): prüfen und ein Klick.

- **Geteilte Ansicht:** links das PDF groß (mindestens die halbe Breite, läuft beim Scrollen
  mit), rechts das Formular. Am Handy untereinander.
- **Vorausgefüllt:** Datum, Betrag, Rechnungsnummer und Lieferant kommen aus der Textschicht
  des PDFs (``apps.dokumente.textschicht``, keine KI); Kostenart, Objekt und Leistungsjahr
  vom letzten Beleg desselben Lieferanten (``services.vorschlag``).
- **Passende Abbuchung daneben:** Gibt es eine Ausgabe mit dem gelesenen Betrag um das
  Belegdatum, steht sie mit „verknüpfen“ im Formular und ist vorgewählt. „Bestätigen“
  bestätigt und verknüpft in einem.
- **Danach der nächste offene Beleg:** Die Rückmeldung nennt, was passiert ist, mit Link.
- **Hochladen:** Foto oder PDF wählen genügt (``neu``); der Beleg entsteht offen und die
  Seite öffnet sich mit allem, was das PDF hergibt.

Gespeichert wird nur über ``apps.dokumente.services``. Die Seite zeigt den Namen des
Lieferanten (Max' Oberfläche); Meldungen und Logs nennen nur IDs.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.views.decorators.http import require_POST

from apps.dokumente import services, textschicht, umwandeln
from apps.dokumente.forms import BelegBestaetigenForm, BelegUmwidmenForm
from apps.dokumente.models import Beleg, Eingangsrechnung
from apps.kassenbuch.models import Buchung
from apps.kern import htmx, zeit

ABLAGE_FEHLER = (services.AblageFehler, umwandeln.DateiUngueltig)

#: Felder des Belegs in der Reihenfolge der Seite (auch für den Vorschlag per HTMX).
BELEG_FELDER = (
    "lieferant",
    "lieferant_neu",
    "datum",
    "betrag",
    "rechnungsnummer",
    "objekt",
    "kategorie",
    "leistungsjahr",
    "umlagefaehiger_anteil",
    "faellig_am",
)
#: Felder, die ein Vorschlag aus früheren Belegen füllen kann.
VORSCHLAG_FELDER = ("objekt", "kategorie", "leistungsjahr", "umlagefaehiger_anteil")


@dataclass
class Vorbelegung:
    """Was im Formular steht, bevor Max etwas tut, und woher es kommt."""

    initial: dict[str, Any]
    #: Titel der Angaben, die aus dem PDF gelesen sind („Datum, Betrag …“).
    gelesen: list[str] = field(default_factory=list)
    vorschlag_text: str = ""


def vorschlag_text(aus_beleg: Beleg | None, werte: dict[str, Any], gefuellt: list[str]) -> str:
    """Hinweis über dem Formular: welche Felder der Vorschlag gefüllt hat, mit genau den
    Werten, die im Formular stehen (Review 2, A6). Leer, wenn er nichts gefüllt hat."""
    gefuellt = [f for f in VORSCHLAG_FELDER if f in set(gefuellt)]
    if aus_beleg is None or not gefuellt:
        return ""
    teile = []
    if "objekt" in gefuellt and werte.get("objekt") is not None:
        teile.append(str(werte["objekt"].kuerzel))
    if "kategorie" in gefuellt and werte.get("kategorie") is not None:
        teile.append(str(werte["kategorie"].bezeichnung))
    if "leistungsjahr" in gefuellt and werte.get("leistungsjahr"):
        teile.append(f"Leistungsjahr {werte['leistungsjahr']}")
    if not teile:
        return ""
    return f"Vorschlag aus Beleg #{aus_beleg.pk} desselben Lieferanten: {', '.join(teile)}."


def rechnung_zu(eintrag: Beleg) -> Eingangsrechnung | None:
    return Eingangsrechnung.objects.filter(beleg__dokument=eintrag.dokument_id).first()


def _gespeichert(eintrag: Beleg) -> dict[str, Any]:
    rechnung = rechnung_zu(eintrag)
    return {
        "lieferant": eintrag.lieferant,
        "lieferant_neu": "",
        "datum": eintrag.dokument.datum,
        "betrag": eintrag.betrag,
        "objekt": eintrag.objekt,
        "kategorie": eintrag.kategorie,
        "leistungsjahr": eintrag.leistungsjahr,
        "umlagefaehiger_anteil": eintrag.umlagefaehiger_anteil,
        "faellig_am": rechnung.faellig_am if rechnung else None,
        "rechnungsnummer": eintrag.rechnungsnummer
        or (rechnung.rechnungsnummer if rechnung else ""),
    }


def _gespeicherter_vorschlag(eintrag: Beleg) -> str:
    """Der Vorschlag, den ``beleg_anlegen`` schon eingetragen hat (nie aus sich selbst)."""
    quelle = eintrag.vorschlag_aus
    if quelle is None or quelle.pk == eintrag.pk:
        return ""
    werte = {f: getattr(eintrag, f) for f in VORSCHLAG_FELDER}
    gleich = [f for f in VORSCHLAG_FELDER if getattr(quelle, f, None) is not None]
    return vorschlag_text(quelle, werte, gleich)


def vorbelegung(eintrag: Beleg) -> Vorbelegung:
    """Die Werte des Formulars: gespeicherte Werte, bei einem offenen Beleg ergänzt um die
    Textschicht des PDFs und den Vorschlag des Lieferanten (Review 2, B9)."""
    initial = _gespeichert(eintrag)
    if eintrag.status != Beleg.Status.OFFEN:
        return Vorbelegung(initial=initial)
    ergebnis = Vorbelegung(initial=initial, vorschlag_text=_gespeicherter_vorschlag(eintrag))
    angaben = textschicht.angaben_aus_dokument(eintrag.dokument)
    if initial["lieferant"] is None and angaben.lieferant is not None:
        initial["lieferant"] = angaben.lieferant
        ergebnis.gelesen.append("Lieferant")
    elif initial["lieferant"] is None and angaben.lieferant_name:
        initial["lieferant_neu"] = angaben.lieferant_name
        ergebnis.gelesen.append("Lieferant (neu)")
    if angaben.datum is not None:
        initial["datum"] = angaben.datum
        ergebnis.gelesen.append("Datum")
    if initial["betrag"] is None and angaben.betrag is not None:
        initial["betrag"] = angaben.betrag
        ergebnis.gelesen.append("Betrag")
    if not initial["rechnungsnummer"] and angaben.rechnungsnummer:
        initial["rechnungsnummer"] = angaben.rechnungsnummer
        ergebnis.gelesen.append("Rechnungsnummer")
    lieferant = initial["lieferant"]
    if lieferant is not None and (initial["objekt"] is None or initial["kategorie"] is None):
        treffer = services.vorschlag(lieferant, initial["datum"], ohne=eintrag)
        if treffer is not None:
            gefuellt = []
            for feld_name in VORSCHLAG_FELDER:
                if initial.get(feld_name) is None:
                    initial[feld_name] = getattr(treffer, feld_name)
                    gefuellt.append(feld_name)
            ergebnis.vorschlag_text = vorschlag_text(treffer.aus_beleg, initial, gefuellt)
    if initial["leistungsjahr"] is None and initial["datum"] is not None:
        initial["leistungsjahr"] = initial["datum"].year
    return ergebnis


def naechster_offener(ausser: Beleg | None = None) -> Beleg | None:
    """Der nächste offene Beleg (ältester Eingang zuerst)."""
    offen = Beleg.objects.filter(status=Beleg.Status.OFFEN).order_by("dokument__datum", "pk")
    if ausser is not None:
        offen = offen.exclude(pk=ausser.pk)
    return offen.first()


def _kandidaten(eintrag: Beleg, initial: dict[str, Any]) -> list[Buchung]:
    if eintrag.status == Beleg.Status.VERWORFEN or eintrag.buchung_id is not None:
        return []
    if eintrag.status == Beleg.Status.OFFEN:
        betrag = initial.get("betrag")
        datum = initial.get("datum")
        if not isinstance(datum, datetime.date):
            datum = None
        return services.buchung_kandidaten(eintrag, betrag=betrag, datum=datum)
    return services.buchung_kandidaten(eintrag)


def vorschlag_per_htmx(form: Any, beleg: Beleg | None = None) -> None:
    """Die Wahl des Lieferanten holt den Vorschlag aus früheren Belegen (HTMX)."""
    import json

    attrs = {
        "hx-get": reverse("dokumente:beleg_vorschlag"),
        "hx-trigger": "change",
        "hx-target": "#beleg-felder",
        "hx-include": "closest form",
        "hx-swap": "outerHTML",
    }
    if beleg is not None:
        attrs["hx-vals"] = json.dumps({"beleg": beleg.pk})
    form.fields["lieferant"].widget.attrs.update(attrs)


def _lieferant_aus(daten: dict[str, Any]) -> Any:
    neu = (daten.get("lieferant_neu") or "").strip()
    if neu:
        return services.lieferant_anlegen(neu)
    return daten.get("lieferant")


def _rueckmeldung(eintrag: Beleg, buchung: Buchung | None, naechster: Beleg | None) -> str:
    """„Beleg #23 bestätigt · an Buchung #88 · weiter mit dem nächsten offenen Beleg →
    ansehen“ (Review 2, B-Leitlinien)."""
    teile = [f"Beleg #{eintrag.pk} bestätigt"]
    if buchung is not None:
        teile.append(f"an Buchung #{buchung.pk}")
    if naechster is not None:
        teile.append("weiter mit dem nächsten offenen Beleg")
    else:
        teile.append("keine offenen Belege mehr")
    return format_html(
        '{} · <a href="{}">ansehen</a>',
        " · ".join(teile),
        reverse("dokumente:beleg", args=[eintrag.pk]),
    )


def _bestaetigen(
    request: HttpRequest, eintrag: Beleg, form: BelegBestaetigenForm, kandidaten: list[Buchung]
) -> HttpResponse | None:
    daten = form.cleaned_data
    gewaehlt = request.POST.get("buchung", "")
    buchung = next((b for b in kandidaten if str(b.pk) == gewaehlt), None)
    try:
        with transaction.atomic():
            services.beleg_bestaetigen(
                eintrag,
                lieferant=_lieferant_aus(daten),
                betrag=daten["betrag"],
                objekt=daten["objekt"],
                kategorie=daten["kategorie"],
                leistungsjahr=daten["leistungsjahr"],
                umlagefaehiger_anteil=daten["umlagefaehiger_anteil"],
                datum=daten["datum"],
                faellig_am=daten["faellig_am"],
                rechnungsnummer=daten["rechnungsnummer"],
            )
            if buchung is not None:
                services.beleg_an_buchung(eintrag, buchung)
    except ABLAGE_FEHLER as fehler:
        form.add_error(None, str(fehler))
        return None
    eintrag.refresh_from_db()
    if services.offene_abweichungen(eintrag):
        messages.warning(
            request,
            format_html(
                "Beleg #{} bestätigt, aber eine passende Abbuchung hat einen anderen Betrag. "
                "Bitte hier prüfen; in der Arbeitsliste steht „Beleg und Abbuchung weichen ab“.",
                eintrag.pk,
            ),
        )
        return redirect("dokumente:beleg", pk=eintrag.pk)
    naechster = naechster_offener(eintrag)
    messages.success(request, _rueckmeldung(eintrag, buchung, naechster))
    if naechster is not None:
        return redirect("dokumente:beleg", pk=naechster.pk)
    return redirect("dokumente:beleg", pk=eintrag.pk)


def _umwidmen_dialog(request: HttpRequest, eintrag: Beleg, form: BelegUmwidmenForm) -> HttpResponse:
    return render(
        request,
        "bausteine/dialog_formular.html",
        {
            "titel": "Kein Beleg: am Lieferanten ablegen",
            "form": form,
            "url": reverse("dokumente:beleg_umwidmen", args=[eintrag.pk]),
            "knopf": "Am Lieferanten ablegen",
            "hinweis": "Mahnungen, Rücklastschriften und Vollstreckungen liegen am Lieferanten, "
            "ohne Buchung.",
        },
    )


@login_required
def seite(request: HttpRequest, pk: int) -> HttpResponse:
    """Seite eines Belegs: prüfen, bestätigen (mit Abbuchung), weiter zum nächsten."""
    eintrag = get_object_or_404(
        Beleg.objects.select_related(
            "dokument", "lieferant", "objekt", "kategorie", "buchung__umsatz", "vorschlag_aus"
        ),
        pk=pk,
    )
    if request.method == "GET" and request.GET.get("dialog") == "umwidmen":
        if eintrag.status != Beleg.Status.OFFEN:
            raise Http404("Nur offene Belege lassen sich umwidmen.")
        return _umwidmen_dialog(
            request, eintrag, BelegUmwidmenForm(initial={"lieferant": eintrag.lieferant})
        )
    belegung = vorbelegung(eintrag)
    kandidaten = _kandidaten(eintrag, belegung.initial)
    form = BelegBestaetigenForm(request.POST or None, initial=belegung.initial)
    vorschlag_per_htmx(form, eintrag)
    if request.method == "POST":
        if eintrag.status == Beleg.Status.VERWORFEN:
            raise Http404("Der Beleg ist verworfen.")
        if form.is_valid():
            antwort = _bestaetigen(request, eintrag, form, kandidaten)
            if antwort is not None:
                return antwort
    gewaehlt = request.POST.get("buchung") if request.method == "POST" else None
    if gewaehlt is None and eintrag.status == Beleg.Status.OFFEN and kandidaten:
        gewaehlt = str(kandidaten[0].pk)
    offen = Beleg.objects.filter(status=Beleg.Status.OFFEN).exclude(pk=eintrag.pk).count()
    kopf_titel = f"Beleg #{eintrag.pk}"
    if eintrag.lieferant is not None:
        kopf_titel += f" · {eintrag.lieferant.name}"
    meta = [
        eintrag.get_status_display(),
        eintrag.dokument.get_herkunft_display(),
        f"Eingang {eintrag.dokument.eingegangen_am:%d.%m.%Y}",
    ]
    if offen:
        meta.append(f"noch {offen} weitere offen")
    return render(
        request,
        "dokumente/beleg.html",
        {
            "kopf_titel": kopf_titel,
            "kopf_meta": " · ".join(meta),
            "beleg": eintrag,
            "form": form,
            "felder": BELEG_FELDER,
            "kandidaten": kandidaten,
            "gewaehlt": gewaehlt or "",
            "gelesen": belegung.gelesen,
            "vorschlag_text": belegung.vorschlag_text,
            "geschwister": eintrag.dokument.belege.exclude(pk=eintrag.pk).order_by("pk"),
            "abweichungen": services.offene_abweichungen(eintrag),
            "rechnung": rechnung_zu(eintrag),
            "weitere_offen": offen,
            "naechster": naechster_offener(eintrag)
            if eintrag.status != Beleg.Status.OFFEN
            else None,
        },
    )


@login_required
@require_POST
def neu(request: HttpRequest) -> HttpResponse:
    """„Beleg hochladen“: Foto oder PDF genügt (Review 2, B9).

    Datum und Lieferant kommen aus der Textschicht (ohne: heute, ohne Lieferant). Der Beleg
    entsteht offen; seine Seite zeigt alles, was das PDF hergibt, zum Prüfen und Bestätigen.
    Eine Datei, die schon in der Ablage liegt, legt nichts Neues an.
    """
    datei = request.FILES.get("datei")
    if datei is None:
        messages.error(request, "Bitte ein Foto oder ein PDF wählen.")
        return redirect("dokumente:ablage")
    inhalt = datei.read()
    angaben = textschicht.angaben_aus_pdf(inhalt) if inhalt.startswith(b"%PDF") else None
    datum = (angaben.datum if angaben else None) or zeit.heute()
    try:
        ergebnis = services.beleg_hochladen(
            inhalt, datum=datum, lieferant=angaben.lieferant if angaben else None
        )
    except ABLAGE_FEHLER as fehler:
        messages.error(request, f"Nicht abgelegt: {fehler}")
        return redirect("dokumente:ablage")
    if not ergebnis.neu:
        ziel = (
            reverse("dokumente:beleg", args=[ergebnis.beleg.pk])
            if ergebnis.beleg is not None
            else reverse("dokumente:dokument", args=[ergebnis.dokument.pk])
        )
        messages.warning(
            request,
            format_html(
                'Diese Datei liegt schon in der Ablage (Dokument #{}) · <a href="{}">ansehen</a>',
                ergebnis.dokument.pk,
                ziel,
            ),
        )
        return redirect(ziel)
    assert ergebnis.beleg is not None  # noqa: S101 (ein neuer Beleg entsteht immer)
    return redirect("dokumente:beleg", pk=ergebnis.beleg.pk)


def weiter(request: HttpRequest, url: str) -> HttpResponse:
    """Nach einer Aktion aus einem Dialog: HTMX lädt die Zielseite ganz (HX-Redirect)."""
    if htmx.ist_htmx(request):
        antwort = HttpResponse(status=204)
        antwort["HX-Redirect"] = url
        return antwort
    return redirect(url)
