"""Formulare für Ablage und Belege, Vorlagen, Zustellnachweis und Serienmitteilung.

Gespeichert wird nie über das Formular, sondern über die Services.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, ClassVar

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
    ohne_mail = forms.ChoiceField(
        label="Wer keine E-Mail-Adresse hat",
        choices=[("brief", "bekommt einen Brief"), ("auslassen", "wird ausgelassen")],
        initial="brief",
        widget=forms.RadioSelect,
        help_text="Gilt nur, wenn die Serie per Mail geht. In der Vorschau lässt sich jeder "
        "Empfänger einzeln umstellen.",
    )
    freitext = forms.CharField(
        label="Mitteilung an Nutzer",
        required=False,
        max_length=4000,
        widget=forms.Textarea(attrs={"rows": 5}),
        help_text="Steht im Schreiben an der Stelle {{ mitteilung.text }} der Vorlage. "
        "Namen, Anschrift und Beträge setzt das System selbst ein.",
    )
    auswahl = forms.MultipleChoiceField(
        label="Auswahl",
        widget=forms.CheckboxSelectMultiple,
        choices=(),
        error_messages={"required": "Bitte mindestens einen Empfänger auswählen."},
    )

    def __init__(self, *args: Any, wahl: list[tuple[str, str]], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Review 2, B13: nur Arten, die als „seriengeeignet“ markiert sind (keine Verträge,
        # Übergabeprotokolle oder Mahnungen).
        self.fields["vorlage"].queryset = (  # type: ignore[attr-defined]
            Vorlage.objects.filter(
                status=Vorlage.Status.FREIGEGEBEN, art__in=vorlagenarten.seriengeeignet()
            ).order_by("art", "name")
        )
        self.fields["vorlage"].label_from_instance = (  # type: ignore[attr-defined]
            lambda v: f"{v.name} ({v.vorlagenart.titel})"
        )
        self.fields["auswahl"].choices = wahl  # type: ignore[attr-defined]


class SerieEingrenzen(forms.Form):
    """Auswahl der Serie nach Objekt und Typ der Einheit (Review 2, B13). Was passt, ist
    vorgewählt; einzelne Einheiten lassen sich danach abwählen."""

    objekt = forms.ModelChoiceField(
        label="Objekt", queryset=Objekt.objects.none(), required=False, empty_label="alle"
    )
    typ = forms.ChoiceField(label="Typ der Einheit", required=False)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        from apps.stammdaten.models import Einheit

        self.fields["objekt"].queryset = Objekt.objects.filter(aktiv=True).order_by("kuerzel")  # type: ignore[attr-defined]
        self.fields["objekt"].label_from_instance = (  # type: ignore[attr-defined]
            lambda o: f"{o.kuerzel} · {o.bezeichnung}"
        )
        self.fields["typ"].choices = [  # type: ignore[attr-defined]
            ("", "alle"),
            *[(w, t) for w, t in Einheit.Typ.choices if w != Einheit.Typ.WOHNUNG],
        ]


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

    #: Pflichtfelder mit ihrem Fehlersatz (Review 2: Fehler als deutsche Sätze am Feld)
    PFLICHT: ClassVar[dict[str, str]] = {
        "betrag": "Bitte den Betrag angeben.",
        "objekt": "Bitte das Objekt wählen.",
        "kategorie": "Bitte die Kostenart wählen.",
        "leistungsjahr": "Bitte das Leistungsjahr angeben.",
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for name, satz in self.PFLICHT.items():
            self.fields[name].required = True
            self.fields[name].error_messages["required"] = satz
        self.fields["lieferant"].help_text = "Neu? Dann rechts den Namen eintragen."

    def clean(self) -> dict[str, Any]:
        daten = super().clean()
        if not (daten.get("lieferant") or (daten.get("lieferant_neu") or "").strip()):
            self.add_error("lieferant", "Bitte den Lieferanten wählen oder einen neuen eintragen.")
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
        self.fields["jahr"].initial = timezone.localdate().year
