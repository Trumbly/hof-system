"""Formulare für Vorlagen, Zustellnachweis und Serienmitteilung."""

from __future__ import annotations

from typing import Any

from django import forms
from django.utils import timezone

from apps.dokumente import vorlagenarten
from apps.dokumente.models import Vorlage
from apps.dokumente.vorlagenarten import Versandweg


class VorlageForm(forms.Form):
    name = forms.CharField(label="Name", max_length=200)
    art = forms.ChoiceField(label="Art")
    betreff = forms.CharField(
        label="Betreff",
        max_length=300,
        required=False,
        help_text="Mit Platzhaltern, z. B. {{ vertrag.nummer }}.",
    )
    text = forms.CharField(
        label="Text (HTML mit Platzhaltern)",
        widget=forms.Textarea(attrs={"rows": 18, "spellcheck": "true"}),
        help_text="Nur Platzhalter, keine Namen, Anschriften oder Beträge.",
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["art"].choices = vorlagenarten.auswahl()  # type: ignore[attr-defined]


class AbschlussForm(forms.Form):
    zugestellt_am = forms.DateField(
        label="zugestellt am",
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    nachweis = forms.FileField(
        label="Zustellnachweis (PDF oder Foto)",
        required=False,
        widget=forms.ClearableFileInput(attrs={"accept": "application/pdf,image/*"}),
    )
    vermerk = forms.CharField(
        label="Vermerk",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="z. B. Sendungsverfolgung oder Einwurf durch Boten mit Datum und Uhrzeit.",
    )


class SerieForm(forms.Form):
    vorlage = forms.ModelChoiceField(label="Vorlage", queryset=Vorlage.objects.none())
    versandweg = forms.ChoiceField(
        label="Versandweg",
        choices=[("", "wie in der Vorlagenart"), *Versandweg.TITEL.items()],
        required=False,
    )
    auswahl = forms.MultipleChoiceField(
        label="Auswahl", widget=forms.CheckboxSelectMultiple, choices=()
    )

    def __init__(self, *args: Any, wahl: list[tuple[str, str]], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["vorlage"].queryset = (  # type: ignore[attr-defined]
            Vorlage.objects.filter(status=Vorlage.Status.FREIGEGEBEN)
            .exclude(art="briefkopf")
            .order_by("art", "name")
        )
        self.fields["auswahl"].choices = wahl  # type: ignore[attr-defined]
