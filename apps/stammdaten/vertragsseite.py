"""Vertragsseite in der neuen Gestaltung (Review 2, B7 und C).

- **Kopf:** V-Nummer, Status, Einheit, Mieter mit Telefon und E-Mail, Miete heute, Saldo,
  nächste Frist. Rechts die Aktionen; selten Gebrauchtes unter „mehr ▾“.
- **Reiter:** Übersicht · Mietkonto (Soll und Zahlungen mit laufendem Saldo) · Dokumente und
  Schreiben · Mahnwesen · Verlauf. Der Reiter steht in der Adresse (``?reiter=…``).
- **Aktionen als Dialoge** (``apps.kern.htmx``): ``?dialog=<aktion>`` liefert das Formular,
  das Speichern ersetzt Kopf und Reiter (die Seite springt nicht) und meldet mit Link, was
  passiert ist. Ohne JavaScript bleibt der alte Weg: POST mit ``aktion``, dann Umleitung.

Gespeichert wird nur über ``apps.stammdaten.services``. Die Seite zeigt Namen und Kontakt
des Mieters (Max' eigene Oberfläche); Meldungen und Logs nennen nur Nummern.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, urlsplit

from django import forms
from django.contrib import messages
from django.contrib.contenttypes.models import ContentType
from django.db.models import Model, Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.http import urlencode
from pghistory.models import Events

from apps.anbindungen import services as anbindungen
from apps.anbindungen.models import Buchungsanfrage
from apps.dokumente.models import Dokumentbezug, Schreiben
from apps.fristen.models import Frist
from apps.kern import htmx, zahlen
from apps.kern.protokoll import ART_TITEL
from apps.stammdaten import forms as f
from apps.stammdaten import konfiguration, services
from apps.stammdaten.models import (
    Kaution,
    Kondition,
    Kontaktweg,
    Kuendigung,
    Uebergabe,
    Vertrag,
    Vertragspartei,
)

NULL = Decimal("0.00")

REITER: tuple[tuple[str, str], ...] = (
    ("uebersicht", "Übersicht"),
    ("mietkonto", "Mietkonto"),
    ("dokumente", "Dokumente und Schreiben"),
    ("mahnwesen", "Mahnwesen"),
    ("verlauf", "Verlauf"),
)
VERLAUF_ANZAHL = 50


@dataclass(frozen=True)
class Dialog:
    titel: str
    knopf: str
    anker: str
    hinweis: str = ""


DIALOGE: dict[str, Dialog] = {
    "kondition": Dialog(
        "Miete ändern",
        "Ab Datum übernehmen",
        "kondition",
        "Die neue Miete gilt ab dem Datum; der alte Stand bleibt lesbar.",
    ),
    "kuendigung": Dialog(
        "Kündigung erfassen",
        "Kündigung erfassen",
        "kuendigung",
        "Das System rechnet das Fristende, legt die Fristen an und bereitet die Bestätigung vor.",
    ),
    "unterschrift": Dialog("Unterschrift und Scan", "Speichern", "unterschrift"),
    "partei": Dialog("Partei hinzufügen", "Hinzufügen", "parteien"),
    "kaution": Dialog("Kaution", "Speichern", "kaution"),
    "kaution_abrechnen": Dialog("Kaution abrechnen", "Abrechnen", "kaution"),
    "stammfelder": Dialog("Vertragsdaten bearbeiten", "Speichern", "stammfelder"),
}


@dataclass
class Rueckmeldung:
    """Was nach dem Speichern gemeldet wird (Review 2, B-Leitlinien): was passiert ist, und
    ein Link auf das Ergebnis."""

    text: str
    link: str = ""
    link_text: str = "ansehen"


# --- Website-Kündigung (Review 2, A3) ------------------------------------------------


def _kuendigung_anfrage(request: HttpRequest, vertrag: Vertrag) -> Buchungsanfrage | None:
    """Die offene Website-Kündigung, aus der der Dialog vorausgefüllt wird (``?anfrage=`` oder
    das versteckte Feld beim Speichern). Nur, wenn sie zu einer Einheit des Vertrags gehört."""
    roh = request.POST.get("kuendigung-anfrage") or request.GET.get("anfrage") or ""
    if not roh.isdigit():
        return None
    return (
        Buchungsanfrage.objects.filter(
            pk=int(roh),
            art=Buchungsanfrage.Art.KUENDIGUNG,
            status__in=anbindungen.NOCH_OFFEN,
            einheit__in=vertrag.einheiten.all(),
        )
        .select_related("einheit")
        .first()
    )


def _kuendigung_meldung(kuendigung: Kuendigung, anfrage: Buchungsanfrage | None) -> str:
    """„Kündigung erfasst · Ende 28.02.2027 · Bestätigung #3 liegt bereit → ansehen“."""
    brief = (
        Schreiben.objects.filter(
            bezug_typ=ContentType.objects.get_for_model(Kuendigung), bezug_id=kuendigung.pk
        )
        .order_by("-pk")
        .first()
    )
    teile = [
        format_html("Aufhebung zum {} erfasst", f"{kuendigung.fristende:%d.%m.%Y}")
        if kuendigung.aufhebung
        else format_html("Kündigung erfasst · Ende {}", f"{kuendigung.fristende:%d.%m.%Y}")
    ]
    art = "Aufhebungsvertrag" if kuendigung.aufhebung else "Bestätigung"
    if brief is not None:
        teile.append(
            format_html(
                '{} #{} liegt bereit → <a href="{}">ansehen</a>',
                art,
                brief.pk,
                reverse("dokumente:schreiben", args=[brief.pk]),
            )
        )
    else:
        teile.append(format_html("{} nicht erzeugt (Arbeitsliste „Schreiben erstellen“)", art))
    if anfrage is not None:
        teile.append(
            format_html(
                'Anfrage <a href="{}">#{}</a> erledigt',
                reverse("anbindungen:anfrage", args=[anfrage.pk]),
                anfrage.pk,
            )
        )
    return format_html_join(" · ", "{}", ((t,) for t in teile))


# --- Formulare und Handler -----------------------------------------------------------


def _gebunden(request: HttpRequest, prefix: str, klasse: Any, **kwargs: Any) -> Any:
    if request.method == "POST" and request.POST.get("aktion") == prefix:
        return klasse(request.POST, request.FILES, prefix=prefix, **kwargs)
    return klasse(prefix=prefix, **kwargs)


def formulare(
    request: HttpRequest, vertrag: Vertrag, anfrage: Buchungsanfrage | None = None
) -> dict[str, forms.Form]:
    kaution = getattr(vertrag, "kaution", None)
    heute = timezone.localdate()
    ergebnis: dict[str, forms.Form] = {
        "stammfelder": _gebunden(request, "stammfelder", f.VertragForm, instance=vertrag),
        # Vorausgefüllt (Review 2, B): nächster Monatserster, Miete und Zahlweise von heute.
        "kondition": _gebunden(
            request, "kondition", f.KonditionForm, initial=_kondition_vorschlag(vertrag)
        ),
        "unterschrift": _gebunden(
            request, "unterschrift", f.UnterschriftForm, initial={"unterschrieben_am": heute}
        ),
        "partei": _gebunden(request, "partei", f.VertragsparteiForm),
    }
    if kaution is not None:
        ergebnis["kaution"] = _gebunden(request, "kaution", f.KautionForm, instance=kaution)
        if kaution.abrechnung_faellig_am and not kaution.abgerechnet_am:
            ergebnis["kaution_abrechnen"] = _gebunden(
                request, "kaution_abrechnen", f.KautionAbrechnenForm, initial={"datum": heute}
            )
    if vertrag.status == Vertrag.Status.AKTIV:
        ergebnis["kuendigung"] = _gebunden(
            request,
            "kuendigung",
            f.KuendigungForm,
            vertrag=vertrag,
            initial=(
                anbindungen.kuendigung_vorschlag(anfrage) if anfrage else {"zugang_am": heute}
            ),
        )
    return ergebnis


def _kondition_vorschlag(vertrag: Vertrag) -> dict[str, Any]:
    heute = timezone.localdate()
    erster = (heute.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)
    jetzt = vertrag.kondition_am(max(heute, vertrag.beginn))
    vorschlag: dict[str, Any] = {"gueltig_ab": max(erster, vertrag.beginn)}
    if jetzt is not None:
        vorschlag |= {
            "miete": jetzt.miete,
            "pauschale": jetzt.pauschale,
            "steuersatz": jetzt.steuersatz,
            "zahlweise": jetzt.zahlweise,
        }
    return vorschlag


def _url(vertrag: Vertrag, reiter: str = "", anker: str = "") -> str:
    basis = reverse("stammdaten:vertrag", args=[vertrag.pk])
    if reiter and reiter != "uebersicht":
        basis = f"{basis}?{urlencode({'reiter': reiter})}"
    return f"{basis}#{anker}" if anker else basis


def _speichern_kondition(v: Vertrag, form: forms.Form) -> Rueckmeldung:
    d = form.cleaned_data
    k = services.kondition_ab(
        v,
        d["gueltig_ab"],
        miete=d["miete"],
        pauschale=d["pauschale"],
        steuersatz=d["steuersatz"],
        zahlweise=d["zahlweise"] or None,
        grund=d["grund"],
    )
    return Rueckmeldung(
        f"Neue Miete {zahlen.euro(k.miete)} ab {k.gueltig_ab:%d.%m.%Y} · alter Stand bleibt lesbar",
        _url(v, "mietkonto"),
        "Mietkonto",
    )


def _speichern_kuendigung(
    v: Vertrag, form: forms.Form, anfrage: Buchungsanfrage | None = None
) -> Rueckmeldung:
    d = form.cleaned_data
    felder: dict[str, Any] = {
        "von": d["von"],
        "zum_wunsch": d["zum_wunsch"],
        "rueckgabetermin": d["rueckgabetermin"],
        "zugangsweg": d["zugangsweg"] or "",
        "aufhebung": d["wunsch_wahl"] == f.KuendigungForm.AUFHEBUNG,
    }
    if anfrage is not None:
        try:
            erfasst = anbindungen.kuendigung_aus_anfrage(anfrage, v, d["zugang_am"], **felder)
        except anbindungen.AnbindungFehler as fehler:
            raise services.StammdatenFehler(str(fehler)) from fehler
    else:
        erfasst = services.kuendigung_erfassen(v, d["zugang_am"], **felder)
    # Die Meldung trägt ihre Links selbst (Bestätigung, Anfrage).
    return Rueckmeldung(_kuendigung_meldung(erfasst, anfrage))


def _speichern_unterschrift(v: Vertrag, form: forms.Form) -> Rueckmeldung:
    d = form.cleaned_data
    services.vertrag_unterschrieben(v, d["unterschrieben_am"], d["scan"])
    return Rueckmeldung(
        f"Unterschrift vom {d['unterschrieben_am']:%d.%m.%Y} gespeichert"
        + (" · Scan abgelegt" if d["scan"] else ""),
        _url(v, anker="unterschrift"),
    )


def _speichern_partei(v: Vertrag, form: forms.Form) -> Rueckmeldung:
    vp = services.vertragspartei_hinzufuegen(
        v, form.cleaned_data["partei"], form.cleaned_data["rolle"]
    )
    return Rueckmeldung(
        f"{vp.partei.anzeigename} als {vp.get_rolle_display()} hinzugefügt",
        reverse("stammdaten:partei", args=[vp.partei_id]),
        "Partei",
    )


def _speichern_kaution(v: Vertrag, form: forms.Form) -> Rueckmeldung:
    services.kaution_aendern(v.kaution, **form.cleaned_data)
    return Rueckmeldung("Kaution gespeichert", _url(v, anker="kaution"))


def _speichern_kaution_abrechnen(v: Vertrag, form: forms.Form) -> Rueckmeldung:
    d = form.cleaned_data
    services.kaution_abrechnen(v.kaution, d["datum"], d["einbehalt"], d["notiz"])
    return Rueckmeldung(
        f"Kaution abgerechnet · Einbehalt {zahlen.euro(d['einbehalt'])}",
        _url(v, anker="kaution"),
    )


def _speichern_stammfelder(v: Vertrag, form: forms.Form) -> Rueckmeldung:
    services.vertrag_aendern(v, **form.cleaned_data)
    return Rueckmeldung("Vertragsdaten gespeichert", _url(v, anker="stammfelder"))


HANDLER: dict[str, Callable[..., Rueckmeldung]] = {
    "kondition": _speichern_kondition,
    "kuendigung": _speichern_kuendigung,
    "unterschrift": _speichern_unterschrift,
    "partei": _speichern_partei,
    "kaution": _speichern_kaution,
    "kaution_abrechnen": _speichern_kaution_abrechnen,
    "stammfelder": _speichern_stammfelder,
}


# --- Kopf ----------------------------------------------------------------------------


@dataclass
class Mieter:
    partei_id: int
    name: str
    rolle: str
    telefon: list[str] = field(default_factory=list)
    email: list[str] = field(default_factory=list)


def _mieter(vertrag: Vertrag) -> list[Mieter]:
    ergebnis = []
    for vp in vertrag.vertragsparteien.select_related("partei").prefetch_related(
        "partei__kontaktwege"
    ):
        wege = list(vp.partei.kontaktwege.all())
        ergebnis.append(
            Mieter(
                partei_id=vp.partei_id,
                name=vp.partei.anzeigename,
                rolle=vp.get_rolle_display(),
                telefon=[
                    k.wert for k in wege if k.art in (Kontaktweg.Art.TELEFON, Kontaktweg.Art.MOBIL)
                ],
                email=[k.wert for k in wege if k.art == Kontaktweg.Art.EMAIL],
            )
        )
    # Mieter zuerst, dann Zahler und weitere Rollen
    ergebnis.sort(key=lambda m: m.rolle != Vertragspartei.Rolle.MIETER.label)
    return ergebnis


def _bezuege(vertrag: Vertrag) -> list[tuple[type[Model], list[int]]]:
    """Alles, was am Vertrag hängt und eigene Fristen, Schreiben oder Ereignisse hat."""
    return [
        (Vertrag, [vertrag.pk]),
        (Kuendigung, list(vertrag.kuendigungen.values_list("pk", flat=True))),
        (Uebergabe, list(vertrag.uebergaben.values_list("pk", flat=True))),
        (Kaution, list(Kaution.objects.filter(vertrag=vertrag).values_list("pk", flat=True))),
    ]


def _bezug_q(vertrag: Vertrag) -> Q:
    q = Q(pk__in=[])
    for modell, ids in _bezuege(vertrag):
        if ids:
            q |= Q(bezug_typ=ContentType.objects.get_for_model(modell), bezug_id__in=ids)
    return q


def _saldo(konto: Any) -> dict[str, Any]:
    saldo: Decimal = konto.saldo
    if saldo < 0:
        return {"wert": -saldo, "text": "offen", "art": "fehler"}
    if saldo > 0:
        return {"wert": saldo, "text": "Guthaben", "art": "ok"}
    return {"wert": NULL, "text": "ausgeglichen", "art": ""}


def kopf_kontext(vertrag: Vertrag) -> dict[str, Any]:
    from apps.abgleich import services as abgleich

    heute = timezone.localdate()
    konto = abgleich.mietkonto(vertrag)
    naechste = (
        Frist.objects.filter(_bezug_q(vertrag), erledigt_am__isnull=True)
        .order_by("datum", "pk")
        .first()
    )
    kaution = getattr(vertrag, "kaution", None)
    return {
        "vertrag": vertrag,
        "heute": heute,
        "einheiten": list(vertrag.einheiten.select_related("objekt")),
        "mieter": _mieter(vertrag),
        "kondition_heute": vertrag.kondition_am(max(heute, vertrag.beginn)),
        "konto": konto,
        "saldo": _saldo(konto),
        "naechste_frist": naechste,
        "frist_ueberfaellig": naechste is not None and naechste.datum < heute,
        "kaution_obj": kaution,
        "dialoge": DIALOGE,
        # P8 (R2-3): ab dieser Monatsmiete Bonitätsnachweis vor der Unterschrift
        "bonitaet": services.bonitaet_noetig(vertrag),
        "bonitaet_ab": konfiguration.laden().bonitaet_ab_miete,
        "buchungsanfragen": vertrag.buchungsanfragen.order_by("pk"),  # type: ignore[attr-defined]
    }


# --- Reiter --------------------------------------------------------------------------


@dataclass
class Kontozeile:
    datum: datetime.date
    text: str
    soll: Decimal | None = None
    zahlung: Decimal | None = None
    saldo: Decimal | None = None
    kuenftig: bool = False
    status: str = ""
    link: str = ""


def mietkonto_zeilen(konto: Any, heute: datetime.date) -> list[Kontozeile]:
    """Sollposten und Zahlungen nach Datum, mit laufendem Saldo (positiv: offen).

    Eine Zahlung, die auf mehrere Posten verteilt ist, steht als eine Zeile. Posten, die
    erst nach heute fällig sind, stehen leise am Ende und zählen nicht zum Saldo.
    """
    zeilen: list[Kontozeile] = []
    zahlungen: dict[tuple[str, int, datetime.date], Kontozeile] = {}
    for z in konto.zeilen:
        p = z.posten
        zeilen.append(
            Kontozeile(
                datum=p.faellig_am,
                text=f"{p.get_art_display()} {p.zeitraum_text}",
                soll=p.betrag,
                kuenftig=p.faellig_am >= heute,
                status=p.status,
            )
        )
        for zu in z.zuordnungen:
            if zu.buchung_id:
                schluessel = ("bank", zu.buchung_id, zu.datum)
                text = "Zahlung über die Bank" + (" (aus Guthaben)" if zu.guthaben_id else "")
            else:
                schluessel = ("hand", zu.handzahlung_id, zu.datum)
                text = f"Handzahlung ({zu.handzahlung.get_art_display()})"
            zeile = zahlungen.get(schluessel)
            if zeile is None:
                zeile = zahlungen[schluessel] = Kontozeile(datum=zu.datum, text=text, zahlung=NULL)
            zeile.zahlung = (zeile.zahlung or NULL) + zu.betrag
    for g in konto.guthaben:
        quelle = g.buchung if g.buchung_id else g.handzahlung
        datum = getattr(quelle, "datum", None) or g.angelegt_am.date()
        zeilen.append(Kontozeile(datum=datum, text="Guthaben (nicht verrechnet)", zahlung=g.betrag))
    zeilen.extend(zahlungen.values())
    zeilen.sort(key=lambda z: (z.kuenftig, z.datum, z.zahlung is not None))
    laufend = NULL
    for zeile in zeilen:
        if zeile.kuenftig:
            continue
        laufend += (zeile.soll or NULL) - (zeile.zahlung or NULL)
        zeile.saldo = laufend
    return zeilen


def _schreiben(vertrag: Vertrag) -> list[Schreiben]:
    return list(Schreiben.objects.filter(_bezug_q(vertrag)).order_by("-erzeugt_am", "-pk"))


def _dokumente(vertrag: Vertrag) -> list[Any]:
    return [
        b.dokument
        for b in Dokumentbezug.objects.filter(
            bezug_typ=ContentType.objects.get_for_model(Vertrag), bezug_id=vertrag.pk
        ).select_related("dokument")
    ]


@dataclass
class Verlaufszeile:
    zeit: datetime.datetime
    art: str
    was: str
    aenderungen: list[tuple[str, Any, Any]]


def _feldname(modell: type[Model] | None, feld: str) -> str:
    if modell is None:
        return feld
    try:
        return str(modell._meta.get_field(feld).verbose_name)
    except Exception:
        return feld


def verlauf(vertrag: Vertrag) -> list[Verlaufszeile]:
    """Änderungen am Vertrag und an allem, was an ihm hängt, mit deutschen Feldnamen."""
    modelle: dict[str, type[Model]] = {}
    q = Q(pk__in=[])
    bezuege = [
        *_bezuege(vertrag),
        (Kondition, list(vertrag.konditionen.values_list("pk", flat=True))),
        (Vertragspartei, list(vertrag.vertragsparteien.values_list("pk", flat=True))),
    ]
    for modell, ids in bezuege:
        if ids:
            modelle[modell._meta.label] = modell
            q |= Q(pgh_obj_model=modell._meta.label, pgh_obj_id__in=[str(i) for i in ids])
    zeilen = []
    for e in Events.objects.filter(q).order_by("-pgh_created_at", "-pgh_id")[:VERLAUF_ANZAHL]:
        modell = modelle.get(e.pgh_obj_model)
        titel = str(modell._meta.verbose_name) if modell else e.pgh_obj_model
        aenderungen = [
            (_feldname(modell, feld), werte[0], werte[1])
            for feld, werte in sorted((e.pgh_diff or {}).items())
            if e.pgh_label == "update"
        ]
        zeilen.append(
            Verlaufszeile(
                zeit=e.pgh_created_at,
                art=ART_TITEL.get(e.pgh_label, e.pgh_label),
                was=f"{titel} #{e.pgh_obj_id}",
                aenderungen=aenderungen,
            )
        )
    return zeilen


def reiter_kontext(vertrag: Vertrag, reiter: str, kopf: dict[str, Any]) -> dict[str, Any]:
    kontext: dict[str, Any] = {
        "reiter_aktiv": reiter,
        "reiter": [{"schluessel": s, "titel": t, "url": _url(vertrag, s)} for s, t in REITER],
    }
    if reiter == "uebersicht":
        kontext |= {
            "konditionen": vertrag.konditionen.order_by("-gueltig_ab"),
            "vertragsparteien": vertrag.vertragsparteien.select_related("partei"),
            "kuendigungen": vertrag.kuendigungen.all(),
            "kuendigungsregel": services.kuendigungsregel_fuer(vertrag),
            "uebergaben": vertrag.uebergaben.all(),
            "checklisten": vertrag.checklisten.prefetch_related("punkte").order_by("-pk"),
            "fristen": list(
                Frist.objects.filter(_bezug_q(vertrag), erledigt_am__isnull=True).order_by(
                    "datum", "pk"
                )
            ),
        }
    elif reiter == "mietkonto":
        kontext["kontozeilen"] = mietkonto_zeilen(kopf["konto"], kopf["heute"])
    elif reiter == "dokumente":
        kontext |= {"schreiben": _schreiben(vertrag), "dokumente": _dokumente(vertrag)}
    elif reiter == "mahnwesen":
        kontext |= {
            "mahnfaelle": list(vertrag.mahnfaelle.select_related("stufe")),  # type: ignore[attr-defined]
            "sperren": list(vertrag.mahnsperren.all()),  # type: ignore[attr-defined]
        }
    elif reiter == "verlauf":
        kontext["verlauf"] = verlauf(vertrag)
    return kontext


# --- Seite ---------------------------------------------------------------------------


def _reiter_wahl(request: HttpRequest) -> str:
    """Der Reiter aus der Adresse; bei Dialogen aus der Adresse der Seite, auf der der Dialog
    geöffnet wurde (HTMX schickt sie mit), damit nach dem Speichern derselbe Reiter steht."""
    reiter = request.GET.get("reiter")
    if reiter is None and (aktuell := request.headers.get("HX-Current-URL")):
        reiter = parse_qs(urlsplit(aktuell).query).get("reiter", [""])[0]
    return reiter if reiter in dict(REITER) else "uebersicht"


def _anfrage_kontext(vertrag: Vertrag, anfrage: Buchungsanfrage | None) -> dict[str, Any]:
    """Website-Kündigung, aus der der Kündigungsdialog vorausgefüllt ist (Review 2, A3)."""
    return {
        "kuendigung_anfrage": anfrage,
        "absender_abweichungen": (
            anbindungen.absender_abweichungen(anfrage, vertrag) if anfrage else []
        ),
    }


def _dialog(
    request: HttpRequest,
    vertrag: Vertrag,
    aktion: str,
    form: forms.Form,
    reiter: str,
    anfrage: Buchungsanfrage | None = None,
) -> HttpResponse:
    dialog = DIALOGE[aktion]
    url = reverse("stammdaten:vertrag", args=[vertrag.pk])
    if reiter != "uebersicht":
        url = f"{url}?{urlencode({'reiter': reiter})}"
    hinweis = dialog.hinweis
    warnungen: list[str] = []
    if aktion == "kuendigung" and anfrage is not None:
        hinweis = (
            f"Aus der Website-Anfrage #{anfrage.pk}. Die Anfrage ist erst erledigt, wenn die "
            "Kündigung gespeichert ist."
        )
        warnungen = [
            f"Absender weicht ab: {satz}"
            for satz in anbindungen.absender_abweichungen(anfrage, vertrag)
        ]
    return render(
        request,
        "bausteine/dialog_formular.html",
        {
            "titel": f"{dialog.titel} · {vertrag.nummer}",
            "form": form,
            "url": url,
            "aktion": aktion,
            "knopf": dialog.knopf,
            "hinweis": hinweis,
            "warnungen": warnungen,
        },
    )


def _teile(
    request: HttpRequest,
    vertrag: Vertrag,
    reiter: str,
    oob: bool,
    anfrage: Buchungsanfrage | None = None,
) -> dict[str, Any]:
    kopf = kopf_kontext(vertrag)
    return {
        **kopf,
        **reiter_kontext(vertrag, reiter, kopf),
        **_anfrage_kontext(vertrag, anfrage),
        "oob": oob,
    }


def _mit_formular(
    kontext: dict[str, Any], alle: dict[str, forms.Form], aktion: str
) -> dict[str, Any]:
    """Seite mit einem offenen Formular (ohne JavaScript oder aus einer Website-Anfrage)."""
    return (
        kontext
        | alle
        | {
            "offenes_formular": alle[aktion],
            "offene_aktion": aktion,
            "offener_dialog": DIALOGE[aktion],
        }
    )


def seite(request: HttpRequest, pk: int) -> HttpResponse:
    vertrag = get_object_or_404(Vertrag.objects.select_related("mandant"), pk=pk)
    reiter = _reiter_wahl(request)
    anfrage = _kuendigung_anfrage(request, vertrag)
    alle = formulare(request, vertrag, anfrage)

    if request.method == "GET":
        if aktion := request.GET.get("dialog"):
            if aktion not in alle:
                raise Http404("Diese Aktion gibt es für diesen Vertrag nicht.")
            return _dialog(request, vertrag, aktion, alle[aktion], reiter, anfrage)
        if htmx.ist_htmx(request) and request.headers.get("HX-Target") == "vertrag-reiter":
            return render(
                request, "stammdaten/_vertrag_reiter.html", _teile(request, vertrag, reiter, False)
            )
        kontext = _teile(request, vertrag, reiter, False, anfrage)
        if anfrage is not None and "kuendigung" in alle:
            # Aus der Website-Anfrage: Kündigungsdialog vorausgefüllt gleich offen (A3).
            kontext = _mit_formular(kontext, alle, "kuendigung")
        else:
            kontext |= alle
        return render(request, "stammdaten/vertrag.html", kontext)

    aktion = request.POST.get("aktion", "")
    if aktion not in alle:
        raise Http404("Unbekannte Aktion")
    form = alle[aktion]
    if form.is_valid():
        try:
            if aktion == "kuendigung":
                meldung = _speichern_kuendigung(vertrag, form, anfrage)
            else:
                meldung = HANDLER[aktion](vertrag, form)
        except services.StammdatenFehler as fehler:
            form.add_error(None, str(fehler))
        else:
            if htmx.ist_htmx(request):
                vertrag.refresh_from_db()
                kontext = _teile(request, vertrag, reiter, True)
                teile = [
                    render_to_string("stammdaten/_vertrag_kopf.html", kontext, request=request),
                    render_to_string("stammdaten/_vertrag_reiter.html", kontext, request=request),
                ]
                return htmx.erfolg(
                    request, teile, meldung.text, link=meldung.link, link_text=meldung.link_text
                )
            messages.success(request, meldung.text)
            return redirect(_url(vertrag, reiter, DIALOGE[aktion].anker))
    if htmx.ist_htmx(request):
        return _dialog(request, vertrag, aktion, form, reiter, anfrage)
    # Ohne JavaScript: Seite mit dem Formular und seinen Fehlern
    for fehler in form.non_field_errors():
        messages.error(request, fehler)
    if not form.non_field_errors():
        messages.error(request, "Bitte die markierten Angaben prüfen.")
    kontext = _teile(request, vertrag, reiter, False, anfrage)
    return render(request, "stammdaten/vertrag.html", _mit_formular(kontext, alle, aktion))
