"""„Neuer Vertrag“ als schmale Seite oder als Dialog (Review 2, B5 und B6).

- **Aus einer Buchungsanfrage** (``?anfrage=<id>``, Knopf „Annehmen und Vertrag anlegen“):
  Einheit, Beginn (Wunschtermin), Miete (Zielmiete) und Partei sind vorausgefüllt. Gibt es
  eine Partei mit derselben E-Mail-Adresse, ist sie gewählt; sonst „Neue Partei“ mit den
  Kontaktdaten der Anfrage. Nach dem Speichern verweisen Anfrage und Vertrag aufeinander
  (``anbindungen.vertrag_zur_anfrage``), Ziel: drei Klicks ab der Arbeitsliste.
- **Von der Einheit** (``?einheit=<id>``): Einheit und Miete vorausgefüllt.
- **Einheitenauswahl:** nur freie Einheiten mit Typ und Zielmiete; „auch vermietete
  Einheiten zeigen“ lädt die Auswahl neu (HTMX, ``?teil=einheit``).
- **Partei:** Suchfeld mit Namen (``partei_suche``), Treffer als Auswahl; „Neue Partei“
  legt sie im selben Formular an.

Als Dialog (HTMX mit Ziel ``#dialog-inhalt``) kommt bei Fehlern das Formular mit den Fehlern
am Feld zurück; gelingt es, führt ``HX-Redirect`` zur Vertragsseite, die Meldung sagt mit
Link, was entstanden ist. Ohne JavaScript ist es eine normale Seite.
"""

from __future__ import annotations

import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from django.contrib import messages
from django.db import transaction
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.html import format_html

from apps.anbindungen import services as anbindungen
from apps.anbindungen.models import Buchungsanfrage
from apps.dokumente.models import Schreiben
from apps.kern import htmx, zeit
from apps.stammdaten import forms as f
from apps.stammdaten import konfiguration, schreiben, services, suche
from apps.stammdaten.models import Einheit, Kontaktweg, Partei, Vertrag

#: Höchstens so viele Treffer zeigt die Parteisuche.
TREFFER = 8


def betrag_lesen(wert: Any) -> Decimal | None:
    """Ein Betrag aus Formular oder Vorbelegung: Decimal, „120,50“ oder „120.50“."""
    if isinstance(wert, Decimal):
        return wert
    text = str(wert or "").strip()
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def naechster_monatserster(heute: datetime.date) -> datetime.date:
    return (heute.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)


def _anfrage(request: HttpRequest) -> Buchungsanfrage | None:
    roh = request.POST.get("anfrage") or request.GET.get("anfrage") or ""
    if not roh.isdigit():
        return None
    return (
        Buchungsanfrage.objects.filter(
            pk=int(roh), art=Buchungsanfrage.Art.BUCHUNG, status__in=anbindungen.NOCH_OFFEN
        )
        .select_related("einheit")
        .first()
    )


def _anfrage_der_einheit(request: HttpRequest) -> Buchungsanfrage | None:
    """Review 2, A11: Hat die Einheit eine angenommene Buchungsanfrage, gehört der neue
    Vertrag zu ihr, auch wenn er von der Einheitenseite aus entsteht."""
    roh = request.GET.get("einheit", "")
    einheit = Einheit.objects.filter(pk=int(roh)).first() if roh.isdigit() else None
    return anbindungen.angenommene_anfrage(einheit) if einheit is not None else None


def vorbelegung(
    request: HttpRequest, heute: datetime.date, anfrage: Buchungsanfrage | None
) -> dict[str, Any]:
    """Was das Formular schon weiß: aus der Anfrage, sonst aus der Einheit."""
    if anfrage is not None:
        return anbindungen.vertrag_vorschlag(anfrage, heute)
    initial: dict[str, Any] = {"beginn": naechster_monatserster(heute)}
    roh = request.GET.get("einheit", "")
    einheit = Einheit.objects.filter(pk=int(roh)).first() if roh.isdigit() else None
    if einheit is not None:
        initial["einheit"] = einheit.pk
        zielmiete = einheit.zielmiete_am(heute)
        if zielmiete is not None:
            initial["miete"] = zielmiete.betrag
    if request.GET.get("alle"):
        initial["alle"] = True
    return initial


# --- Parteisuche ---------------------------------------------------------------------


def parteien_suchen(text: str) -> QuerySet[Partei]:
    """Parteien zu einem Suchtext wie in der Suche der Kopfzeile (``stammdaten.suche``):
    Name, Firma, Ort, PLZ, Straße, E-Mail oder Telefon; jedes Wort muss vorkommen. „#12“
    findet Partei 12."""
    text = text.strip()
    if text.startswith("#") and text[1:].isdigit():
        return Partei.objects.filter(pk=int(text[1:]))
    ids = Partei.objects.filter(suche.parteien_bedingung(text)).values("pk").distinct()
    return Partei.objects.filter(pk__in=ids).order_by("nachname", "vorname", "firma", "pk")


def treffer_kontext(text: str, gewaehlt: Partei | None = None) -> dict[str, Any]:
    treffer = list(parteien_suchen(text)[:TREFFER]) if text.strip() else []
    if gewaehlt is not None and gewaehlt not in treffer:
        treffer.insert(0, gewaehlt)
    wege: dict[int, list[str]] = {}
    for k in Kontaktweg.objects.filter(partei__in=treffer).order_by("-bevorzugt", "pk"):
        wege.setdefault(k.partei_id, []).append(k.wert)
    return {
        "treffer": [{"partei": p, "kontakt": " · ".join(wege.get(p.pk, [])[:2])} for p in treffer],
        "suchtext": text.strip(),
        "gewaehlt": str(gewaehlt.pk) if gewaehlt else "",
    }


def partei_suche(request: HttpRequest) -> HttpResponse:
    """Treffer der Parteisuche als Auswahl (HTMX-Teil des Formulars „Neuer Vertrag“)."""
    text = request.GET.get("partei_suche") or request.GET.get("q") or ""
    return render(request, "stammdaten/_partei_treffer.html", treffer_kontext(text))


# --- Speichern -----------------------------------------------------------------------


def _anlegen(form: f.VertragNeuForm, anfrage: Buchungsanfrage | None) -> tuple[Vertrag, Any, Any]:
    d = form.cleaned_data
    with transaction.atomic():
        if d["mieter"] == f.VertragNeuForm.NEU:
            partei = services.partei_mit_kontakt_anlegen(
                vorname=d["neu_vorname"],
                nachname=d["neu_nachname"],
                firma=d["neu_firma"],
                email=d["neu_email"],
                telefon=d["neu_telefon"],
            )
        elif form.partei is not None:
            partei = form.partei
        else:  # clean_mieter lässt das nicht zu
            raise services.StammdatenFehler("Bitte eine Partei wählen.")
        vertrag = services.vertrag_anlegen(
            einheiten=[d["einheit"]],
            mieter=[partei],
            vertragsart=d["vertragsart"] or None,
            mandant=d["mandant"],
            beginn=d["beginn"],
            ende=d["ende"],
            miete=d["miete"],
            pauschale=d["pauschale"] or Decimal("0.00"),
            steuersatz=d["steuersatz"] or Decimal("0.00"),
            zahlweise=d["zahlweise"],
            kaution=d["kaution"],
            kuendigungsregel=d["kuendigungsregel"] or None,
        )
        services.checkliste_anlegen("vertrag", vertrag=vertrag)
        dokument = services.vertragsdokument_erzeugen(vertrag)
        # Review 2, A11 und B5: Die Anfrage gehört zu diesem Vertrag.
        verknuepft = anbindungen.vertrag_zur_anfrage(vertrag, anfrage)
    return vertrag, dokument, verknuepft


def _meldung(vertrag: Vertrag, dokument: schreiben.Ergebnis, anfrage: Any) -> str:
    """„Vertrag V-0047 als Entwurf angelegt · Vertragsdokument #12 liegt bereit → ansehen ·
    Anfrage #5 verknüpft“."""
    teile = [format_html("Vertrag {} als Entwurf angelegt", vertrag.nummer)]
    if isinstance(dokument, Schreiben):
        teile.append(
            format_html(
                'Vertragsdokument #{} liegt bereit → <a href="{}">ansehen</a>',
                dokument.pk,
                reverse("dokumente:schreiben", args=[dokument.pk]),
            )
        )
    else:
        teile.append(
            format_html("Kein PDF: {} (Arbeitsliste „Schreiben erstellen“)", dokument.hinweis)
        )
    if anfrage is not None:
        teile.append(
            format_html(
                'Buchungsanfrage <a href="{}">#{}</a> verknüpft',
                reverse("anbindungen:anfrage", args=[anfrage.pk]),
                anfrage.pk,
            )
        )
    return format_html(" · ".join(["{}"] * len(teile)), *teile)


def _kontext(
    request: HttpRequest, form: f.VertragNeuForm, anfrage: Buchungsanfrage | None, dialog: bool
) -> dict[str, Any]:
    grenze = konfiguration.laden().bonitaet_ab_miete
    suchtext = str(form["partei_suche"].value() or "")
    miete = betrag_lesen(form["miete"].value())
    return {
        "form": form,
        "anfrage": anfrage,
        "dialog": dialog,
        "url": reverse("stammdaten:vertrag_neu"),
        "bonitaet_ab": grenze,
        "bonitaet_sichtbar": grenze is not None and miete is not None and miete >= grenze,
        "weitere": [form[name] for name in f.VertragNeuForm.WEITERE],
        "weitere_fehler": any(form[name].errors for name in f.VertragNeuForm.WEITERE),
        **treffer_kontext(suchtext, form.mieter_vorschlag),
    }


def seite(request: HttpRequest) -> HttpResponse:
    heute = zeit.heute()
    anfrage = _anfrage(request)
    if anfrage is None and request.method == "GET":
        anfrage = _anfrage_der_einheit(request)
    dialog = htmx.ist_htmx(request) and (
        request.headers.get("HX-Target") == "dialog-inhalt" or request.POST.get("dialog") == "1"
    )
    if request.method == "POST":
        form = f.VertragNeuForm(request.POST, heute=heute)
        if form.is_valid():
            try:
                vertrag, dokument, verknuepft = _anlegen(form, anfrage)
            except (services.StammdatenFehler, anbindungen.AnbindungFehler) as fehler:
                form.add_error(None, str(fehler))
            else:
                messages.success(request, _meldung(vertrag, dokument, verknuepft))
                if services.bonitaet_noetig(vertrag):
                    messages.warning(
                        request,
                        "Die Monatsmiete liegt bei 100 € oder darüber: Bitte vor der "
                        "Unterschrift Selbstauskunft und Bonitätsnachweis prüfen (P8).",
                    )
                ziel = reverse("stammdaten:vertrag", args=[vertrag.pk])
                if htmx.ist_htmx(request):
                    antwort = HttpResponse(status=204)
                    antwort["HX-Redirect"] = ziel
                    return antwort
                return redirect(ziel)
    else:
        form = f.VertragNeuForm(initial=vorbelegung(request, heute, anfrage), heute=heute)
        if request.GET.get("teil") == "einheit":
            # Schalter „auch vermietete Einheiten zeigen“: nur die Auswahl neu.
            return render(request, "stammdaten/_vertrag_neu_einheit.html", {"form": form})
    kontext = _kontext(request, form, anfrage, dialog)
    if dialog:
        return render(request, "stammdaten/_vertrag_neu_formular.html", kontext)
    return render(request, "stammdaten/vertrag_neu.html", kontext)
