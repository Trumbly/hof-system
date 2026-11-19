"""Formulare der Sollstellung. Sie prüfen nur; gespeichert wird über ``services``."""

from __future__ import annotations

from typing import Any

from django import forms

from apps.kern.models import Mandant
from apps.soll.models import Sollposten, Sollregel
from apps.stammdaten.forms import Datum

FILTER_STATUS = (
    ("offen", "offen und teilweise bezahlt"),
    ("ueberfaellig", "überfällig"),
    ("nicht_pruefbar", "nicht prüfbar"),
    ("noch_nicht_faellig", "noch nicht fällig"),
    ("bezahlt", "bezahlt"),
    ("storniert", "storniert"),
    ("alle", "alle"),
)


class FilterForm(forms.Form):
    status = forms.ChoiceField(label="Status", choices=FILTER_STATUS, required=False)
    art = forms.ChoiceField(
        label="Art", choices=[("", "alle"), *Sollposten.Art.choices], required=False
    )
    mandant = forms.ModelChoiceField(
        label="Mandant",
        queryset=Mandant.objects.filter(aktiv=True),
        required=False,
        empty_label="alle",
    )
    faellig_bis = forms.DateField(label="fällig bis", required=False, widget=Datum())
    suche = forms.CharField(label="Vertrag oder NW-ID", required=False, max_length=30, strip=True)


class SollregelForm(forms.Form):
    faelligkeit = forms.ChoiceField(
        label="Fälligkeit", choices=Sollregel.Faelligkeit.choices, required=False
    )
    faelligkeit_tag = forms.IntegerField(
        label="Tag", min_value=1, max_value=31, required=False, help_text="z. B. 3 für den dritten"
    )
    soll_ab = forms.DateField(
        label="Sollstellung ab",
        required=False,
        widget=Datum(),
        help_text="Leer: HOF_SOLL_BEGINN bzw. Vertragsbeginn.",
    )

    def clean(self) -> dict[str, Any]:
        daten = super().clean() or {}
        if daten.get("faelligkeit") and not daten.get("faelligkeit_tag"):
            self.add_error("faelligkeit_tag", "Bitte den Tag angeben.")
        return daten


class PostenForm(forms.Form):
    art = forms.ChoiceField(
        label="Art",
        choices=[
            (Sollposten.Art.SONSTIGES, Sollposten.Art.SONSTIGES.label),
            (Sollposten.Art.KAUTION, Sollposten.Art.KAUTION.label),
        ],
    )
    bezeichnung = forms.CharField(label="Bezeichnung", max_length=200, required=False)
    von = forms.DateField(label="Datum", widget=Datum())
    netto = forms.DecimalField(label="Betrag netto", min_value=0, decimal_places=2)
    steuersatz = forms.DecimalField(
        label="Umsatzsteuer (%)", min_value=0, decimal_places=2, initial=0, required=False
    )
    faellig_am = forms.DateField(label="fällig am", required=False, widget=Datum())


class StornoForm(forms.Form):
    grund = forms.CharField(
        label="Grund",
        max_length=200,
        help_text="Kurz, z. B. „doppelt gestellt“; steht im Protokoll.",
        error_messages={"required": "Ein Storno braucht einen Grund."},
    )
