"""Formulare der Stammdaten. Sie prüfen nur; gespeichert wird über ``services``."""

from __future__ import annotations

from typing import Any, ClassVar

from django import forms

from apps.kern.models import Mandant, Objekt
from apps.stammdaten import konfiguration
from apps.stammdaten.models import (
    Einheit,
    EinheitStatus,
    Kaution,
    Kondition,
    Kontaktweg,
    Kuendigung,
    Partei,
    Schluessel,
    Vertrag,
    Vertragspartei,
)

MAX_FOTO_BYTES = 15 * 1024 * 1024


class Datum(forms.DateInput):
    input_type = "date"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(format="%Y-%m-%d", **kwargs)


def _regel_auswahl(leer: str = "– noch nicht geprüft –") -> list[tuple[str, str]]:
    regeln = konfiguration.laden().kuendigungsregeln
    return [("", leer)] + [(r.schluessel, r.titel) for r in regeln.values()]


class EinheitForm(forms.ModelForm):  # type: ignore[type-arg]
    class Meta:
        model = Einheit
        fields = (
            "typ",
            "objekt",
            "bezeichnung",
            "flaeche_qm",
            "vermarktung",
            "website_slug",
            "notiz",
        )
        widgets: ClassVar[dict[str, forms.Widget]] = {"notiz": forms.Textarea(attrs={"rows": 3})}


class WohnraumForm(forms.ModelForm):  # type: ignore[type-arg]
    class Meta:
        model = Einheit
        fields = ("objekt", "bezeichnung", "notiz")
        widgets: ClassVar[dict[str, forms.Widget]] = {"notiz": forms.Textarea(attrs={"rows": 3})}


class EinheitNeuForm(forms.Form):
    objekt = forms.ModelChoiceField(Objekt.objects.filter(aktiv=True), label="Objekt")
    typ = forms.ChoiceField(choices=Einheit.Typ.choices, label="Typ")
    bezeichnung = forms.CharField(label="Bezeichnung", required=False, max_length=200)
    flaeche_qm = forms.DecimalField(label="Fläche (m²)", required=False, min_value=0)


class StatusForm(forms.Form):
    status = forms.ChoiceField(
        choices=[c for c in EinheitStatus.Status.choices if c[0] != "aufgeloest"], label="Status"
    )
    gueltig_ab = forms.DateField(label="gültig ab", widget=Datum())
    grund = forms.CharField(label="Grund", required=False, max_length=200)


class ZielmieteForm(forms.Form):
    betrag = forms.DecimalField(label="Zielmiete je Monat", min_value=0, decimal_places=2)
    gueltig_ab = forms.DateField(label="gültig ab", widget=Datum())
    begruendung = forms.CharField(
        label="Begründung", required=False, widget=forms.Textarea(attrs={"rows": 2})
    )


class SchluesselForm(forms.ModelForm):  # type: ignore[type-arg]
    class Meta:
        model = Schluessel
        fields = ("art", "bezeichnung", "anzahl", "inhaber", "partei", "inhaber_text")

    def clean(self) -> dict[str, Any]:
        daten = super().clean() or {}
        if daten.get("inhaber") == Schluessel.Inhaber.PARTEI and not daten.get("partei"):
            self.add_error("partei", "Bitte die Partei wählen.")
        return daten


class ParteiForm(forms.ModelForm):  # type: ignore[type-arg]
    class Meta:
        model = Partei
        fields = (
            "art",
            "anrede",
            "vorname",
            "nachname",
            "firma",
            "adresszusatz",
            "strasse",
            "plz",
            "ort",
            "notiz",
        )
        widgets: ClassVar[dict[str, forms.Widget]] = {"notiz": forms.Textarea(attrs={"rows": 3})}


class KontaktwegForm(forms.ModelForm):  # type: ignore[type-arg]
    class Meta:
        model = Kontaktweg
        fields = ("art", "wert", "bevorzugt")


class VertragForm(forms.ModelForm):  # type: ignore[type-arg]
    kuendigungsregel = forms.ChoiceField(label="Kündigungsfrist", required=False, choices=())

    class Meta:
        model = Vertrag
        fields = ("vertragsart", "mandant", "beginn", "ende", "kuendigungsregel", "sonderabreden")
        widgets: ClassVar[dict[str, forms.Widget]] = {
            "beginn": Datum(),
            "ende": Datum(),
            "sonderabreden": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["kuendigungsregel"].choices = _regel_auswahl()  # type: ignore[attr-defined]


class KonditionForm(forms.Form):
    gueltig_ab = forms.DateField(label="gültig ab", widget=Datum())
    miete = forms.DecimalField(label="Miete je Monat (netto)", min_value=0, decimal_places=2)
    pauschale = forms.DecimalField(
        label="Pauschale je Monat", min_value=0, decimal_places=2, required=False
    )
    steuersatz = forms.DecimalField(
        label="Umsatzsteuer (%)", min_value=0, decimal_places=2, required=False
    )
    zahlweise = forms.ChoiceField(
        choices=Kondition.Zahlweise.choices, label="Zahlweise", required=False
    )
    grund = forms.CharField(label="Grund", required=False, max_length=200)


class KautionForm(forms.ModelForm):  # type: ignore[type-arg]
    class Meta:
        model = Kaution
        fields = ("betrag", "eingang_am", "konto")
        widgets: ClassVar[dict[str, forms.Widget]] = {"eingang_am": Datum()}


class KautionAbrechnenForm(forms.Form):
    datum = forms.DateField(label="abgerechnet am", widget=Datum())
    einbehalt = forms.DecimalField(label="Einbehalt", min_value=0, decimal_places=2)
    notiz = forms.CharField(label="Notiz", required=False, widget=forms.Textarea(attrs={"rows": 2}))


class UnterschriftForm(forms.Form):
    unterschrieben_am = forms.DateField(label="unterschrieben am", widget=Datum())
    scan = forms.FileField(label="unterschriebener Scan (PDF)", required=False)


class KuendigungForm(forms.Form):
    zugang_am = forms.DateField(label="Zugang am", widget=Datum())
    von = forms.ChoiceField(choices=Kuendigung.Von.choices, label="gekündigt von")
    zum_wunsch = forms.DateField(label="gewünschtes Ende", required=False, widget=Datum())
    rueckgabetermin = forms.DateField(
        label="Rückgabetermin",
        required=False,
        widget=Datum(),
        help_text="Leer: am letzten Tag des Vertrags.",
    )


class VertragsparteiForm(forms.Form):
    partei = forms.ModelChoiceField(Partei.objects.all(), label="Partei")
    rolle = forms.ChoiceField(choices=Vertragspartei.Rolle.choices, label="Rolle")


class VertragNeuForm(forms.Form):
    einheit = forms.ModelChoiceField(
        Einheit.objects.exclude(typ=Einheit.Typ.WOHNUNG).filter(aufgeloest_am__isnull=True),
        label="Einheit",
    )
    mieter = forms.ModelChoiceField(Partei.objects.all(), label="Mieter")
    vertragsart = forms.ChoiceField(
        choices=[("", "– nach Typ der Einheit –"), *Vertrag.Art.choices],
        label="Vertragsart",
        required=False,
    )
    mandant = forms.ModelChoiceField(
        Mandant.objects.filter(aktiv=True),
        label="Mandant",
        required=False,
        help_text="Leer: Mandant des Objekts.",
    )
    beginn = forms.DateField(label="Beginn", widget=Datum())
    ende = forms.DateField(label="Ende (nur bei Befristung)", required=False, widget=Datum())
    miete = forms.DecimalField(label="Miete je Monat (netto)", min_value=0, decimal_places=2)
    pauschale = forms.DecimalField(
        label="Pauschale je Monat", min_value=0, decimal_places=2, required=False
    )
    steuersatz = forms.DecimalField(
        label="Umsatzsteuer (%)", min_value=0, decimal_places=2, required=False
    )
    zahlweise = forms.ChoiceField(
        choices=[c for c in Kondition.Zahlweise.choices if c[0]], label="Zahlweise"
    )
    kaution = forms.DecimalField(label="Kaution", min_value=0, decimal_places=2, required=False)
    kuendigungsregel = forms.ChoiceField(label="Kündigungsfrist", required=False, choices=())

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["kuendigungsregel"].choices = _regel_auswahl(  # type: ignore[attr-defined]
            "– Standard der Vertragsart –"
        )


class UebergabeVorabForm(forms.Form):
    datum = forms.DateField(label="Datum", widget=Datum())
    vertrag_unterschrieben = forms.BooleanField(
        label="Vertrag ist heute vor Ort unterschrieben", required=False
    )


def foto_pruefen(datei: Any) -> str | None:
    """Fehlermeldung, wenn die Datei kein Foto ist, sonst None."""
    art = getattr(datei, "content_type", "") or ""
    if not art.startswith("image/"):
        return "Nur Fotos (Bilddateien) sind erlaubt."
    if datei.size > MAX_FOTO_BYTES:
        return "Das Foto ist größer als 15 MB."
    return None
