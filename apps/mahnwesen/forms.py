"""Formulare des Mahnwesens. Gespeichert wird nie über das Formular, sondern über die
Services."""

from __future__ import annotations

from django import forms

from apps.abgleich.forms import VertragWahl
from apps.dokumente import vorlagenarten
from apps.dokumente.vorlagenarten import Versandweg
from apps.mahnwesen.models import Mahnsperre, Mahnstufe
from apps.stammdaten.models import Einheit, Vertrag


def _datum(**kw: object) -> forms.DateField:
    return forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), **kw)  # type: ignore[arg-type]


def _vorlagenarten() -> list[tuple[str, str]]:
    return [(a.schluessel, a.titel) for a in vorlagenarten.alle() if not a.briefkopf]


class StufeForm(forms.Form):
    titel = forms.CharField(label="Titel", max_length=100)
    ausloeser = forms.ChoiceField(label="Auslöser", choices=Mahnstufe.Ausloeser.choices)
    ausloeser_wert = forms.IntegerField(
        label="Wert des Auslösers",
        min_value=0,
        max_value=999,
        help_text="Tage oder Anzahl der Monatsmieten.",
    )
    frist_tage = forms.IntegerField(label="Frist (Tage)", min_value=0, max_value=365)
    vorlagenart = forms.ChoiceField(label="Vorlagenart", choices=_vorlagenarten)
    vorlage_name = forms.CharField(
        label="Name der Vorlage",
        max_length=200,
        required=False,
        help_text="Leer: die neueste freigegebene Vorlage der Art.",
    )
    versandweg = forms.ChoiceField(label="Versandweg", choices=list(Versandweg.TITEL.items()))
    einseitige_erklaerung = forms.BooleanField(
        label="einseitige Erklärung (belegt erst mit Zustellnachweis)", required=False
    )
    aktiv = forms.BooleanField(label="aktiv", required=False)


#: Gründe, die der Benutzer setzt; der Klärfall sperrt automatisch über den Lauf.
GRUENDE_HAND = [(g.value, g.label) for g in Mahnsperre.Grund if g != Mahnsperre.Grund.KLAERFALL]


class SperreForm(forms.Form):
    vertrag = VertragWahl(
        Vertrag.objects.exclude(einheiten__typ=Einheit.Typ.WOHNUNG)
        .prefetch_related("einheiten")
        .order_by("nummer"),
        label="Vertrag",
    )
    grund = forms.ChoiceField(label="Grund", choices=GRUENDE_HAND)
    von = _datum(label="gilt ab")
    bis = _datum(label="bis", required=False, help_text="Leer: bis auf Weiteres.")
    hinweis = forms.CharField(
        label="Hinweis",
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Kurz und ohne Namen, z. B. „Ratenplan bis März“.",
    )

    def clean(self) -> dict[str, object]:
        daten = super().clean() or {}
        von, bis = daten.get("von"), daten.get("bis")
        if von and bis and bis < von:  # type: ignore[operator]
            raise forms.ValidationError("„bis“ liegt vor „gilt ab“.")
        return daten


class AufhebenForm(forms.Form):
    am = _datum(label="aufgehoben am")


class BelegenForm(forms.Form):
    am = _datum(label="belegt am (gesendet oder zugestellt)")
    vermerk = forms.CharField(
        label="Vermerk",
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Bei einseitigen Erklärungen Pflicht: Zustellnachweis, z. B. Einwurf durch "
        "Boten oder Sendungsverfolgung.",
    )


class KuendigungForm(forms.Form):
    zugang_am = _datum(label="Zugang beim Mieter (angenommen)")
