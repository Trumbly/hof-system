"""Seite ``/stil/``: alle Bausteine der neuen Gestaltung mit Beispielen (Review 2, C3).

Nur angemeldet erreichbar, in jeder Umgebung: Sie liest keine Daten aus der Datenbank,
zeigt nur feste, erfundene Beispiele ohne Personen und schreibt nichts. So lässt sich die
Gestaltung auch auf dem Server am eigenen Handy prüfen.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any

from django import forms
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from apps.kern import htmx, zahlen
from apps.kern.templatetags.bausteine import BADGES, BEGRIFFE

D = datetime.date


class MehrereDateien(forms.ClearableFileInput):
    allow_multiple_selected = True


class BeispielForm(forms.Form):
    """Nur für die Stilseite; wird nie gespeichert."""

    miete = forms.DecimalField(
        label="Miete je Monat (netto)",
        min_value=Decimal("0"),
        decimal_places=2,
        help_text="Gilt ab dem gewählten Datum; der alte Stand bleibt lesbar.",
    )
    gueltig_ab = forms.DateField(label="gültig ab", widget=forms.DateInput(attrs={"type": "date"}))
    zahlweise = forms.ChoiceField(
        label="Zahlweise",
        choices=[("monatlich", "monatlich"), ("vierteljaehrlich", "vierteljährlich")],
    )
    grund = forms.CharField(label="Grund", required=False, max_length=200)
    notiz = forms.CharField(label="Notiz", required=False, widget=forms.Textarea(attrs={"rows": 3}))
    bestaetigt = forms.BooleanField(label="Vertrag ist unterschrieben", required=False)


class OptionenForm(forms.Form):
    """Radio- und Checkbox-Gruppe (Review 3, Punkt 2); nur für die Stilseite."""

    ohne_mail = forms.ChoiceField(
        label="Wer keine E-Mail-Adresse hat",
        choices=[("brief", "bekommt einen Brief"), ("auslassen", "wird ausgelassen")],
        widget=forms.RadioSelect,
        help_text="Gilt nur, wenn die Serie per Mail geht.",
        error_messages={"required": "Bitte wählen, was mit Empfängern ohne E-Mail geschieht."},
    )
    wunsch = forms.ChoiceField(
        label="Wunschtermin vor dem Fristende",
        choices=[
            ("fristende", "Fristende 31.01.2027"),
            ("aufhebung", "Aufhebung zum Wunschtermin 15.11.2026 (Vorlage Aufhebungsvertrag)"),
        ],
        widget=forms.RadioSelect,
    )
    objekte = forms.MultipleChoiceField(
        label="Empfänger",
        choices=[("feld", "FELD · Außenflächen"), ("halle", "HALLE · Gewerbehalle")],
        widget=forms.CheckboxSelectMultiple,
        error_messages={"required": "Bitte mindestens einen Empfänger auswählen."},
    )

    def clean_wunsch(self) -> str:
        wahl: str = self.cleaned_data["wunsch"]
        if wahl != "fristende":
            raise forms.ValidationError(
                "Der Wunschtermin 15.11.2026 liegt vor dem Fristende 31.01.2027. Bitte wählen: "
                "Kündigung zum Fristende oder Aufhebung zum Wunschtermin."
            )
        return wahl


class FotoForm(forms.Form):
    fotos = forms.FileField(
        label="Fotos der Einheit",
        required=False,
        widget=MehrereDateien(attrs={"accept": "image/*,application/pdf"}),
        help_text="Am Handy öffnet „Foto aufnehmen“ direkt die Kamera.",
    )


class DialogForm(forms.Form):
    miete = forms.DecimalField(
        label="Neue Miete je Monat", min_value=Decimal("1"), decimal_places=2
    )
    gueltig_ab = forms.DateField(label="gültig ab", widget=forms.DateInput(attrs={"type": "date"}))

    def clean_gueltig_ab(self) -> datetime.date:
        tag: datetime.date = self.cleaned_data["gueltig_ab"]
        if tag.day != 1:
            raise forms.ValidationError("Eine neue Miete beginnt am Ersten eines Monats.")
        return tag


class FilterBeispielForm(forms.Form):
    """Filterleiste und Formular auf der Seite (R2-9); wird nie gespeichert."""

    suche = forms.CharField(
        label="Suche",
        required=False,
        widget=forms.TextInput(
            attrs={"type": "search", "placeholder": "V-Nummer, NW-ID oder Name"}
        ),
    )
    status = forms.ChoiceField(
        label="Status", required=False, choices=[("", "alle"), ("offen", "offen")]
    )
    von = forms.DateField(
        label="von", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )


FARBEN = (
    ("Grund", "--grund"),
    ("Fläche", "--flaeche"),
    ("Fläche 2", "--flaeche-2"),
    ("Linie", "--linie"),
    ("Text", "--text"),
    ("Text 2", "--text-2"),
    ("Text 3", "--text-3"),
    ("Primär", "--primaer"),
    ("ok", "--ok"),
    ("Hinweis", "--hinweis"),
    ("Warnung", "--warnung"),
    ("Fehler", "--fehler"),
    ("neutral", "--neutral"),
)

#: Kontrast nach WCAG 2.1, gerechnet (siehe static/css/werte.css).
KONTRASTE = (
    ("Text auf Fläche", "16,7:1", "14,0:1"),
    ("Text 2 auf Fläche", "6,8:1", "7,8:1"),
    ("Text 3 auf Fläche 2", "4,6:1", "5,3:1"),
    ("Schrift auf Primär (Knopf)", "6,4:1", "7,9:1"),
    ("Link auf Grund", "6,0:1", "8,6:1"),
    ("ok auf ok-Grund", "6,2:1", "8,2:1"),
    ("Hinweis auf Hinweis-Grund", "6,5:1", "8,1:1"),
    ("Warnung auf Warnung-Grund", "6,6:1", "8,6:1"),
    ("Fehler auf Fehler-Grund", "6,3:1", "7,9:1"),
    ("neutral auf neutral-Grund", "6,8:1", "7,7:1"),
    ("Feldrahmen auf Fläche", "3,1:1", "3,5:1"),
)

#: Dieselben Paare im Apple-Design (static/css/apple/basis.css), gerechnet wie oben.
KONTRASTE_APPLE = (
    ("Text auf Fläche", "16,8:1", "15,5:1"),
    ("Text 2 auf Fläche", "6,4:1", "8,3:1"),
    ("Text 3 auf Fläche 2", "4,6:1", "5,5:1"),
    ("Schrift auf Primär (Knopf)", "5,3:1", "8,1:1"),
    ("Schrift auf sekundärem Knopf", "13,3:1", "11,7:1"),
    ("Link auf Grund", "4,8:1", "10,2:1"),
    ("Auswahl (Seitenleiste)", "5,9:1", "8,3:1"),
    ("Text 2 auf Material", "6,0:1", "8,3:1"),
    ("ok auf ok-Grund", "6,2:1", "8,2:1"),
    ("Hinweis auf Hinweis-Grund", "6,5:1", "8,1:1"),
    ("Warnung auf Warnung-Grund", "6,6:1", "8,6:1"),
    ("Fehler auf Fehler-Grund", "6,3:1", "7,9:1"),
    ("neutral auf neutral-Grund", "6,8:1", "7,7:1"),
    ("Feldrahmen auf Fläche", "3,4:1", "3,7:1"),
)

BEISPIEL_ZEILEN = (
    {
        "nummer": "V-0001",
        "einheit": "NW-G-0001",
        "status": "aktiv",
        "miete": Decimal("85.00"),
        "offen": Decimal("0.00"),
    },
    {
        "nummer": "V-0002",
        "einheit": "NW-S-0004",
        "status": "gekuendigt",
        "miete": Decimal("40.00"),
        "offen": Decimal("40.00"),
    },
    {
        "nummer": "V-0003",
        "einheit": "NW-H-0001",
        "status": "aktiv",
        "miete": Decimal("1250.00"),
        "offen": Decimal("2500.00"),
    },
    {
        "nummer": "V-0004",
        "einheit": "NW-L-0002",
        "status": "entwurf",
        "miete": Decimal("120.00"),
        "offen": Decimal("0.00"),
    },
)


def _reiter(aktiv: str) -> list[dict[str, Any]]:
    basis = reverse("kern:stil")
    return [
        {"schluessel": s, "titel": t, "url": f"{basis}?reiter={s}#reiter", "anzahl": n}
        for s, t, n in (
            ("uebersicht", "Übersicht", None),
            ("mietkonto", "Mietkonto", 12),
            ("dokumente", "Dokumente und Schreiben", 3),
            ("verlauf", "Verlauf", None),
        )
    ]


@login_required
def stilseite(request: HttpRequest) -> HttpResponse:
    reiter = request.GET.get("reiter", "uebersicht")
    fehlerhaft = BeispielForm(
        {
            "fehler-miete": "12,5x",
            "fehler-gueltig_ab": "2026-12-01",
            "fehler-zahlweise": "monatlich",
        },
        prefix="fehler",
    )
    fehlerhaft.is_valid()
    optionen_fehler = OptionenForm(
        {"optionen_fehler-wunsch": "aufhebung"}, prefix="optionen_fehler"
    )
    optionen_fehler.is_valid()
    apple = getattr(request, "hof_design", "standard") == "apple"
    return render(
        request,
        "kern/stil.html",
        {
            "farben": FARBEN,
            "untertitel": (
                "Alle Bausteine im Apple-Design mit Beispielen · Werte in static/css/werte.css "
                "und static/css/apple/basis.css, Bausteine in static/css/apple/bausteine.css"
                if apple
                else "Alle Bausteine der neuen Gestaltung mit Beispielen · Werte in "
                "static/css/werte.css"
            ),
            "kontraste": KONTRASTE_APPLE if apple else KONTRASTE,
            # Apple-Design: Stufen nach den iOS-Textstilen (Beschriftung bis großer Titel)
            "groessen": (12, 13, 15, 17, 20, 22, 28, 34) if apple else (13, 15, 17, 20, 24, 30),
            "abstaende": (4, 8, 12, 16, 20, 24, 32, 40, 48),
            "badges": [(schluessel, b) for schluessel, b in BADGES.items()][:8],
            "alle_badges": BADGES,
            "begriffe": list(BEGRIFFE.items()),
            "zeilen": BEISPIEL_ZEILEN,
            "summe_offen": sum((z["offen"] for z in BEISPIEL_ZEILEN), Decimal("0")),
            "form": BeispielForm(prefix="beispiel"),
            "fehlerhaft": fehlerhaft,
            "optionen": OptionenForm(prefix="optionen", initial={"ohne_mail": "brief"}),
            "optionen_fehler": optionen_fehler,
            "fotos": FotoForm(),
            "filter": FilterBeispielForm(prefix="filter"),
            "reiter": _reiter(reiter),
            "reiter_aktiv": reiter,
            "beispiele_zahlen": (
                ("euro", Decimal("209533.70"), zahlen.euro(Decimal("209533.70"))),
                ("euro", Decimal("-14.6"), zahlen.euro(Decimal("-14.6"))),
                ("zahl", Decimal("9602.7"), zahlen.zahl(Decimal("9602.7"))),
                ("zahl:2", 1234567, zahlen.zahl(1234567, 2)),
            ),
            "stichtag_beispiel": D(2026, 11, 1),
            "beispiel_betrag": Decimal("48.90"),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def stil_dialog(request: HttpRequest) -> HttpResponse:
    """Beispiel des Dialog-Musters: GET liefert das Formular, POST prüft es. Gespeichert wird
    nichts; bei Erfolg ersetzt die Antwort nur das Feld „Ergebnis“ und zeigt einen Toast."""
    form = DialogForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        text = f"Neue Miete {zahlen.euro(d['miete'])} ab {d['gueltig_ab']:%d.%m.%Y} (Beispiel)"
        teil = render_to_string(
            "kern/_stil_ergebnis.html", {"ergebnis": text, "oob": True}, request=request
        )
        return htmx.erfolg(
            request,
            [teil],
            f"{text} · nichts gespeichert",
            link="#dialog-beispiel",
            link_text="zum Ergebnis",
        )
    return render(
        request,
        "bausteine/dialog_formular.html",
        {
            "titel": "Miete ändern",
            "form": form,
            "url": reverse("kern:stil_dialog"),
            "knopf": "Ab Datum übernehmen",
            "hinweis": "Beispiel: Nur der Erste eines Monats gilt. Es wird nichts gespeichert.",
        },
    )
