"""Formulare des Abgleichs. Gespeichert wird nie über das Formular, sondern über die Services."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django import forms
from django.forms import formset_factory

from apps.abgleich import services
from apps.abgleich.models import Handzahlung
from apps.kassenbuch.forms import KategorieWahl, ObjektWahl
from apps.kassenbuch.models import Kategorie
from apps.kern.models import Objekt
from apps.soll.models import Sollposten
from apps.stammdaten.models import Vertrag


class VertragWahl(forms.ModelChoiceField):  # type: ignore[type-arg]
    def label_from_instance(self, obj: Any) -> str:
        vertrag: Vertrag = obj
        nw = ", ".join(e.nw_id for e in vertrag.einheiten.all())
        return f"{vertrag.nummer} · {nw} ({vertrag.get_status_display()})"


class VertraegeWahl(forms.ModelMultipleChoiceField):  # type: ignore[type-arg]
    def label_from_instance(self, obj: Any) -> str:
        return VertragWahl.label_from_instance(self, obj)  # type: ignore[arg-type]


class PostenWahl(forms.ModelChoiceField):  # type: ignore[type-arg]
    def label_from_instance(self, obj: Any) -> str:
        posten: Sollposten = obj
        return (
            f"{posten.vertrag.nummer} · {posten.get_art_display()} {posten.zeitraum_text} "
            f"(offen {posten.offen} €)"
        )


def _geld(**kw: Any) -> forms.DecimalField:
    return forms.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"), **kw)


class ZuordnenForm(forms.Form):
    vertrag = VertragWahl(
        Vertrag.objects.prefetch_related("einheiten").order_by("nummer"), label="Vertrag"
    )
    rest_als_guthaben = forms.BooleanField(label="Rest als Guthaben", required=False, initial=True)


class AnteilForm(forms.Form):
    posten = PostenWahl(
        services.posten_im_abgleich()
        .select_related("vertrag")
        .order_by("vertrag__nummer", "faellig_am", "pk"),
        label="Posten",
        required=False,
    )
    betrag = _geld(label="Betrag", required=False)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["posten"].queryset = (  # type: ignore[attr-defined]
            services.posten_im_abgleich()
            .select_related("vertrag")
            .order_by("vertrag__nummer", "faellig_am", "pk")
        )

    def clean(self) -> dict[str, Any]:
        daten: dict[str, Any] = super().clean() or {}
        if bool(daten.get("posten")) != bool(daten.get("betrag")):
            raise forms.ValidationError("Posten und Betrag gehören zusammen.")
        return daten


AnteilFormSet = formset_factory(AnteilForm, extra=3)


class GuthabenForm(forms.Form):
    guthaben = forms.DecimalField(
        label="Rest als Guthaben", max_digits=12, decimal_places=2, min_value=0, required=False
    )
    guthaben_vertrag = VertragWahl(
        Vertrag.objects.prefetch_related("einheiten").order_by("nummer"),
        label="Guthaben für Vertrag",
        required=False,
    )


class ZahlerregelForm(forms.Form):
    vertrag = VertragWahl(
        Vertrag.objects.prefetch_related("einheiten").order_by("nummer"),
        label="Vertrag",
        required=False,
    )
    vertraege = VertraegeWahl(
        Vertrag.objects.prefetch_related("einheiten").order_by("nummer"),
        label="oder mehrere Verträge (Sammelzahler)",
        required=False,
        widget=forms.SelectMultiple(attrs={"size": 6}),
    )
    betrag = _geld(label="erwarteter Betrag", required=False)
    nicht_vertragsbezogen = forms.BooleanField(label="nicht vertragsbezogen", required=False)


class RegelAusKlaerfallForm(ZahlerregelForm):
    kategorie = KategorieWahl(
        Kategorie.objects.filter(aktiv=True).order_by("art", "bezeichnung"),
        label="Kategorie (neue Regel im Kassenbuch)",
        required=False,
    )
    objekt = ObjektWahl(
        Objekt.objects.filter(aktiv=True).order_by("kuerzel"),
        label="Objekt (neue Regel im Kassenbuch)",
        required=False,
    )


class HandzahlungForm(forms.Form):
    art = forms.ChoiceField(label="Art", choices=Handzahlung.Art.choices)
    datum = forms.DateField(label="Datum", widget=forms.DateInput(attrs={"type": "date"}))
    betrag = _geld(label="Betrag")
    grund = forms.CharField(label="Grund", widget=forms.Textarea(attrs={"rows": 3}))
    beleg = forms.FileField(label="Beleg (Quittung, Kontoauszug)")


class StornoForm(forms.Form):
    grund = forms.CharField(label="Grund", max_length=200)
