"""Formulare für Ablage und Belege, Vorlagen, Zustellnachweis und Serienmitteilung.

Gespeichert wird nie über das Formular, sondern über die Services.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django import forms
from django.utils import timezone

from apps.dokumente import dokumentarten, vorlagenarten
from apps.dokumente.models import Lieferant, Vorlage
from apps.dokumente.vorlagenarten import Versandweg
from apps.kassenbuch.models import Kategorie
from apps.kern.models import Objekt


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


# --- Ablage und Belege (Schritt 3a) --------------------------------------------------


def _ausgabe_kategorien() -> Any:
    return Kategorie.objects.filter(aktiv=True, art=Kategorie.Art.AUSGABE).order_by("bezeichnung")


def _aktive_objekte() -> Any:
    return Objekt.objects.filter(aktiv=True).order_by("kuerzel")


def _aktive_lieferanten() -> Any:
    return Lieferant.objects.filter(aktiv=True).order_by("name")


class LieferantWahl(forms.ModelChoiceField):  # type: ignore[type-arg]
    def label_from_instance(self, obj: Any) -> str:
        return str(obj.name)


class KostenartWahl(forms.ModelChoiceField):  # type: ignore[type-arg]
    def label_from_instance(self, obj: Any) -> str:
        return str(obj.bezeichnung)


class ObjektAuswahl(forms.ModelChoiceField):  # type: ignore[type-arg]
    def label_from_instance(self, obj: Any) -> str:
        return f"{obj.kuerzel} · {obj.bezeichnung}"


def _zahl() -> forms.TextInput:
    """Betragsfeld fürs Handy: Ziffernblock, Komma oder Punkt (``localize=True``)."""
    return forms.TextInput(attrs={"inputmode": "decimal", "autocomplete": "off"})


class BelegFelder(forms.Form):
    """Die Felder eines Belegs (H6.2). Was leer bleibt, füllt der Vorschlag des Lieferanten."""

    lieferant = LieferantWahl(
        queryset=Lieferant.objects.none(),
        label="Lieferant",
        required=False,
        empty_label="– wählen –",
    )
    lieferant_neu = forms.CharField(label="oder neuer Lieferant", max_length=200, required=False)
    datum = forms.DateField(
        label="Belegdatum",
        widget=forms.DateInput(attrs={"type": "date"}),
        initial=timezone.localdate,
    )
    betrag = forms.DecimalField(
        label="Betrag (Euro)",
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        required=False,
        widget=_zahl(),
        localize=True,
    )
    objekt = ObjektAuswahl(
        queryset=Objekt.objects.none(), label="Objekt", required=False, empty_label="– wählen –"
    )
    kategorie = KostenartWahl(
        queryset=Kategorie.objects.none(),
        label="Kostenart",
        required=False,
        empty_label="– wählen –",
    )
    leistungsjahr = forms.IntegerField(
        label="Leistungsjahr",
        min_value=1990,
        max_value=2100,
        required=False,
        widget=forms.NumberInput(attrs={"inputmode": "numeric"}),
    )
    umlagefaehiger_anteil = forms.DecimalField(
        label="umlagefähiger Anteil (%)",
        max_digits=5,
        decimal_places=2,
        min_value=Decimal("0"),
        max_value=Decimal("100"),
        required=False,
        help_text="Leer: Vorgabe der Kostenart.",
        widget=_zahl(),
        localize=True,
    )
    faellig_am = forms.DateField(
        label="fällig am",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text="Noch zu zahlen? Dann wird der Beleg ein offener Posten mit Erinnerung.",
    )
    rechnungsnummer = forms.CharField(
        label="Rechnungsnummer",
        max_length=100,
        required=False,
        help_text="Erkennt die Abbuchung im Verwendungszweck.",
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["lieferant"].queryset = _aktive_lieferanten()  # type: ignore[attr-defined]
        self.fields["objekt"].queryset = _aktive_objekte()  # type: ignore[attr-defined]
        self.fields["kategorie"].queryset = _ausgabe_kategorien()  # type: ignore[attr-defined]

    def clean(self) -> dict[str, Any]:
        daten = super().clean() or {}
        if daten.get("lieferant") and (daten.get("lieferant_neu") or "").strip():
            self.add_error("lieferant_neu", "Entweder einen Lieferanten wählen oder einen neuen.")
        return daten


class BelegHochladenForm(BelegFelder):
    """Upload am Handy: Foto oder PDF, dazu die Felder von Hand (keine Texterkennung)."""

    datei = forms.FileField(
        label="Foto oder PDF",
        widget=forms.ClearableFileInput(
            attrs={"accept": "image/*,application/pdf", "capture": "environment"}
        ),
    )
    art = forms.ChoiceField(label="Art", initial=dokumentarten.BELEG)
    bemerkung = forms.CharField(
        label="Bemerkung", required=False, widget=forms.Textarea(attrs={"rows": 2})
    )

    field_order = ("datei", "art", "lieferant", "lieferant_neu", "datum", "betrag", "objekt")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["art"].choices = [  # type: ignore[attr-defined]
            (a.schluessel, a.titel)
            for a in dokumentarten.DOKUMENTARTEN.values()
            if a.beleg or a.schluessel in dokumentarten.AM_LIEFERANTEN
        ]

    def clean(self) -> dict[str, Any]:
        daten = super().clean()
        am_lieferanten = daten.get("art") in dokumentarten.AM_LIEFERANTEN
        if am_lieferanten and not (daten.get("lieferant") or daten.get("lieferant_neu")):
            self.add_error("lieferant", "Eine Mahnung oder Rücklastschrift gehört zum Lieferanten.")
        return daten


class BelegBestaetigenForm(BelegFelder):
    """Seite des Belegs: alle Felder sind Pflicht."""

    def clean(self) -> dict[str, Any]:
        daten = super().clean()
        if not (daten.get("lieferant") or (daten.get("lieferant_neu") or "").strip()):
            self.add_error("lieferant", "Der Lieferant fehlt.")
        for feld in ("betrag", "objekt", "kategorie", "leistungsjahr"):
            if daten.get(feld) is None and feld not in self.errors:
                self.add_error(feld, "Pflichtfeld.")
        return daten


class BelegUmwidmenForm(forms.Form):
    art = forms.ChoiceField(label="Ablegen als")
    lieferant = LieferantWahl(
        queryset=Lieferant.objects.none(), label="Lieferant", empty_label="– wählen –"
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["art"].choices = [  # type: ignore[attr-defined]
            (s, dokumentarten.holen(s).titel) for s in dokumentarten.AM_LIEFERANTEN
        ]
        self.fields["lieferant"].queryset = _aktive_lieferanten()  # type: ignore[attr-defined]


class BelegteilForm(forms.Form):
    betrag = forms.DecimalField(
        label="Betrag",
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        widget=_zahl(),
        localize=True,
    )
    objekt = ObjektAuswahl(queryset=Objekt.objects.none(), label="Objekt", empty_label="– wählen –")
    kategorie = KostenartWahl(
        queryset=Kategorie.objects.none(), label="Kostenart", empty_label="– wählen –"
    )
    leistungsjahr = forms.IntegerField(label="Leistungsjahr", min_value=1990, max_value=2100)
    umlagefaehiger_anteil = forms.DecimalField(
        label="umlagefähig (%)",
        max_digits=5,
        decimal_places=2,
        min_value=Decimal("0"),
        max_value=Decimal("100"),
        required=False,
        widget=_zahl(),
        localize=True,
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["objekt"].queryset = _aktive_objekte()  # type: ignore[attr-defined]
        self.fields["kategorie"].queryset = _ausgabe_kategorien()  # type: ignore[attr-defined]


BelegteilFormset = forms.formset_factory(BelegteilForm, extra=0, min_num=2, validate_min=True)


class LieferantForm(forms.Form):
    name = forms.CharField(label="Name", max_length=200)
    email = forms.EmailField(
        label="E-Mail (Absender der Rechnungen)",
        required=False,
        help_text="Erkennt den Lieferanten im Rechnungspostfach.",
    )
    iban = forms.CharField(
        label="IBAN", max_length=34, required=False, help_text="Erkennt die Abbuchung."
    )
    aktiv = forms.BooleanField(label="aktiv", required=False, initial=True)


class BelegmappeForm(forms.Form):
    objekt = ObjektAuswahl(queryset=Objekt.objects.none(), label="Objekt", empty_label="– wählen –")
    jahr = forms.IntegerField(label="Leistungsjahr", min_value=1990, max_value=2100)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["objekt"].queryset = Objekt.objects.order_by("kuerzel")  # type: ignore[attr-defined]
        self.fields["jahr"].initial = timezone.localdate().year - 1
