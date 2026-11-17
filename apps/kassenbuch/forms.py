"""Formulare des Kassenbuchs. Gespeichert wird nie über das Formular, sondern über die Services."""

from __future__ import annotations

import datetime
import re
from decimal import Decimal
from typing import Any

from django import forms
from django.forms import formset_factory

from apps.bank.models import Bankkonto
from apps.kassenbuch.models import Kategorie, Zuordnungsregel
from apps.kern.models import Mandant, Objekt


def _aktive_kategorien() -> Any:
    return Kategorie.objects.filter(aktiv=True).order_by("art", "bezeichnung")


def _aktive_objekte() -> Any:
    return Objekt.objects.filter(aktiv=True).order_by("kuerzel")


class KategorieWahl(forms.ModelChoiceField):  # type: ignore[type-arg]
    def label_from_instance(self, obj: Any) -> str:
        kategorie: Kategorie = obj
        return f"{kategorie.bezeichnung} ({kategorie.get_art_display()})"


class ObjektWahl(forms.ModelChoiceField):  # type: ignore[type-arg]
    def label_from_instance(self, obj: Any) -> str:
        objekt: Objekt = obj
        return f"{objekt.kuerzel} · {objekt.bezeichnung}"


class MandantWahl(forms.ModelChoiceField):  # type: ignore[type-arg]
    def label_from_instance(self, obj: Any) -> str:
        mandant: Mandant = obj
        return f"{mandant.kuerzel} ({mandant.get_art_display()})"


def _datum_widget() -> forms.DateInput:
    return forms.DateInput(attrs={"type": "date"})


class BuchungFilter(forms.Form):
    von = forms.DateField(required=False, widget=_datum_widget())
    bis = forms.DateField(required=False, widget=_datum_widget())
    mandant = MandantWahl(Mandant.objects.order_by("kuerzel"), required=False, empty_label="alle")
    objekt = ObjektWahl(Objekt.objects.order_by("kuerzel"), required=False, empty_label="alle")
    kategorie = KategorieWahl(
        Kategorie.objects.order_by("art", "bezeichnung"), required=False, empty_label="alle"
    )
    art = forms.ChoiceField(
        label="Art", choices=[("", "alle"), *Kategorie.Art.choices], required=False
    )
    konto = forms.ModelChoiceField(
        Bankkonto.objects.order_by("bezeichnung"), required=False, empty_label="alle Konten"
    )
    ohne_storno = forms.BooleanField(label="ohne Stornos und stornierte Buchungen", required=False)


class ZuordnenForm(forms.Form):
    """Ein Umsatz ganz auf eine Kategorie (Aktion „Zuordnen“)."""

    kategorie = KategorieWahl(_aktive_kategorien())
    objekt = ObjektWahl(_aktive_objekte())
    mandant = MandantWahl(
        Mandant.objects.filter(aktiv=True).order_by("kuerzel"),
        required=False,
        empty_label="Mandant des Objekts",
    )
    leistungsjahr = forms.IntegerField(min_value=1990, max_value=2100, required=False)
    umlagefaehiger_anteil = forms.DecimalField(
        label="umlagefähiger Anteil (%)",
        min_value=Decimal("0"),
        max_value=Decimal("100"),
        decimal_places=2,
        required=False,
        help_text="leer: Vorgabe der Kategorie",
    )


class TeilForm(ZuordnenForm):
    """Ein Teil beim Aufteilen; der Betrag ist positiv, die Richtung kommt vom Umsatz."""

    betrag = forms.DecimalField(min_value=Decimal("0.01"), decimal_places=2, max_digits=12)

    field_order = ("betrag", "kategorie", "objekt", "mandant", "leistungsjahr")


TeilFormSet = formset_factory(TeilForm, extra=0, min_num=2, validate_min=True)


class RegelForm(forms.ModelForm):  # type: ignore[type-arg]
    """Bedingungen und Ergebnis einer Zuordnungsregel."""

    kategorie = KategorieWahl(_aktive_kategorien())
    objekt = ObjektWahl(_aktive_objekte())
    mandant = MandantWahl(
        Mandant.objects.filter(aktiv=True).order_by("kuerzel"),
        required=False,
        empty_label="Mandant des Objekts",
    )

    class Meta:
        model = Zuordnungsregel
        fields = (
            "bezeichnung",
            "gegenpartei",
            "gegen_iban",
            "zweck",
            "zweck_regex",
            "betrag_von",
            "betrag_bis",
            "richtung",
            "konto",
            "kategorie",
            "objekt",
            "mandant",
            "prioritaet",
            "aktiv",
        )

    def clean_zweck(self) -> str:
        zweck: str = self.cleaned_data.get("zweck", "")
        if self.data.get(self.add_prefix("zweck_regex")) and zweck:
            try:
                re.compile(zweck)
            except re.error as fehler:
                raise forms.ValidationError(
                    f"Ungültiger regulärer Ausdruck: {fehler.msg}"
                ) from None
        return zweck

    def _post_clean(self) -> None:
        # Mandant ist im Modell Pflicht; ohne Angabe setzt der Service den des Objekts.
        if not self.cleaned_data.get("mandant") and self.cleaned_data.get("objekt"):
            self.cleaned_data["mandant"] = self.cleaned_data["objekt"].mandant
        super()._post_clean()


MONAT_MUSTER = re.compile(r"^(\d{4})-(\d{2})$")


def monat_aus_text(text: str | None) -> datetime.date | None:
    """„2026-01“ → 1.1.2026; sonst None."""
    treffer = MONAT_MUSTER.match(text or "")
    if not treffer:
        return None
    jahr, monat = int(treffer.group(1)), int(treffer.group(2))
    if not 1 <= monat <= 12:
        return None
    return datetime.date(jahr, monat, 1)


class BerichtForm(forms.Form):
    objekt = ObjektWahl(Objekt.objects.order_by("kuerzel"))
    monat = forms.CharField(widget=forms.TextInput(attrs={"type": "month"}))

    def clean_monat(self) -> datetime.date:
        monat = monat_aus_text(self.cleaned_data.get("monat"))
        if monat is None:
            raise forms.ValidationError("Monat als JJJJ-MM angeben.")
        return monat
