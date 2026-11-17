"""Formulare der Zähler. Sie prüfen nur; gespeichert wird über ``services``."""

from __future__ import annotations

import datetime
from typing import Any

from django import forms

from apps.kern.models import Objekt
from apps.stammdaten.forms import Datum, foto_pruefen
from apps.stammdaten.models import Einheit
from apps.zaehler.models import Zaehler

FOTO_ATTRS = {"accept": "image/*", "capture": "environment"}


class FotoFeld(forms.FileField):
    """Foto direkt aus der Kamera; nur Bilddateien bis 15 MB."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("required", False)
        kwargs.setdefault("widget", forms.ClearableFileInput(attrs=FOTO_ATTRS))
        super().__init__(**kwargs)

    def clean(self, data: Any, initial: Any = None) -> Any:
        datei = super().clean(data, initial)
        if datei and (fehler := foto_pruefen(datei)):
            raise forms.ValidationError(fehler)
        return datei


def _einheiten() -> Any:
    return (
        Einheit.objects.filter(aufgeloest_am__isnull=True)
        .exclude(typ=Einheit.Typ.WOHNUNG)
        .select_related("objekt")
        .order_by("nw_id")
    )


def _nicht_in_der_zukunft(datum: Any) -> None:
    if datum is not None and datum > datetime.date.today():
        raise forms.ValidationError("Das Datum liegt in der Zukunft.")


class ZaehlerNeuForm(forms.Form):
    nummer = forms.CharField(label="Zählernummer", max_length=50)
    art = forms.ChoiceField(label="Art", choices=Zaehler.Art.choices)
    einheit = forms.ModelChoiceField(
        _einheiten(), label="Einheit", required=False, help_text="Entweder Einheit …"
    )
    objekt = forms.ModelChoiceField(
        Objekt.objects.filter(aktiv=True),
        label="Objekt (Hauptzähler)",
        required=False,
        help_text="… oder Objekt.",
    )
    bezeichnung = forms.CharField(label="Lage", max_length=200, required=False)
    einbaudatum = forms.DateField(label="Einbaudatum", required=False, widget=Datum())
    eichfrist_bis = forms.DateField(label="Eichfrist bis", required=False, widget=Datum())
    einbau_stand = forms.CharField(
        label="Stand beim Einbau",
        required=False,
        widget=forms.TextInput(attrs={"inputmode": "decimal"}),
    )

    def clean(self) -> dict[str, Any]:
        daten: dict[str, Any] = super().clean() or {}
        if bool(daten.get("einheit")) == bool(daten.get("objekt")):
            raise forms.ValidationError("Bitte genau eins wählen: Einheit oder Objekt.")
        if daten.get("einbau_stand") and not daten.get("einbaudatum"):
            self.add_error("einbaudatum", "Zum Einbaustand gehört das Einbaudatum.")
        return daten


class ZaehlerForm(forms.Form):
    nummer = forms.CharField(label="Zählernummer", max_length=50)
    bezeichnung = forms.CharField(label="Lage", max_length=200, required=False)
    einbaudatum = forms.DateField(label="Einbaudatum", required=False, widget=Datum())
    eichfrist_bis = forms.DateField(label="Eichfrist bis", required=False, widget=Datum())


class StandForm(forms.Form):
    datum = forms.DateField(label="Datum", widget=Datum(), validators=[_nicht_in_der_zukunft])
    stand = forms.CharField(label="Stand", widget=forms.TextInput(attrs={"inputmode": "decimal"}))
    geschaetzt = forms.BooleanField(label="geschätzt", required=False)
    foto = FotoFeld(label="Foto")
    vermerk = forms.CharField(
        label="Vermerk",
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text="z. B. wie geschätzt wurde. Keine Namen.",
    )


class WechselForm(forms.Form):
    datum = forms.DateField(
        label="Datum des Wechsels", widget=Datum(), validators=[_nicht_in_der_zukunft]
    )
    ausbau_stand = forms.CharField(
        label="Stand des alten Zählers (Ausbau)",
        widget=forms.TextInput(attrs={"inputmode": "decimal"}),
    )
    ausbau_foto = FotoFeld(label="Foto des alten Zählers")
    neue_nummer = forms.CharField(label="Nummer des neuen Zählers", max_length=50)
    einbau_stand = forms.CharField(
        label="Stand des neuen Zählers (Einbau)",
        initial="0",
        widget=forms.TextInput(attrs={"inputmode": "decimal"}),
    )
    einbau_foto = FotoFeld(label="Foto des neuen Zählers")
    eichfrist_bis = forms.DateField(
        label="Eichfrist des neuen Zählers bis", required=False, widget=Datum()
    )
