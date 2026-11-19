"""Formulare der Stammdaten. Sie prüfen nur; gespeichert wird über ``services``."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, ClassVar

from django import forms

from apps.kern import zeit
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
        fields = ("betrag", "art", "eingang_am", "konto")
        widgets: ClassVar[dict[str, forms.Widget]] = {"eingang_am": Datum()}


class KautionAbrechnenForm(forms.Form):
    datum = forms.DateField(label="abgerechnet am", widget=Datum())
    einbehalt = forms.DecimalField(label="Einbehalt", min_value=0, decimal_places=2)
    notiz = forms.CharField(label="Notiz", required=False, widget=forms.Textarea(attrs={"rows": 2}))


class UnterschriftForm(forms.Form):
    unterschrieben_am = forms.DateField(label="unterschrieben am", widget=Datum())
    scan = forms.FileField(label="unterschriebener Scan (PDF)", required=False)


class KuendigungForm(forms.Form):
    """Kündigungsdialog des Vertrags. Liegt der Wunschtermin vor dem Fristende, fragt das
    Formular nach: Fristende oder Aufhebung zum Wunschtermin (Review 2, A11)."""

    FRISTENDE = "fristende"
    AUFHEBUNG = "aufhebung"

    zugang_am = forms.DateField(label="Zugang am", widget=Datum())
    von = forms.ChoiceField(choices=Kuendigung.Von.choices, label="gekündigt von")
    zugangsweg = forms.ChoiceField(
        choices=Kuendigung.Zugangsweg.choices, label="Zugangsweg", required=False
    )
    zum_wunsch = forms.DateField(label="gewünschtes Ende", required=False, widget=Datum())
    wunsch_wahl = forms.ChoiceField(
        label="Wunschtermin vor dem Fristende",
        required=False,
        choices=(),
        widget=forms.HiddenInput,
    )
    rueckgabetermin = forms.DateField(
        label="Rückgabetermin",
        required=False,
        widget=Datum(),
        help_text="Leer: am letzten Tag des Vertrags.",
    )
    anfrage = forms.IntegerField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args: Any, vertrag: Vertrag | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.vertrag = vertrag
        self.fristende: Any = None
        self._wahl_setzen(None, None)

    def _wahl_setzen(self, ende: Any, wunsch: Any) -> None:
        feld = self.fields["wunsch_wahl"]
        if ende is None:
            feld.choices = [(self.FRISTENDE, "Fristende"), (self.AUFHEBUNG, "Aufhebung")]  # type: ignore[attr-defined]
            return
        feld.widget = forms.RadioSelect()
        feld.choices = [  # type: ignore[attr-defined]
            (self.FRISTENDE, f"Fristende {ende:%d.%m.%Y}"),
            (
                self.AUFHEBUNG,
                f"Aufhebung zum Wunschtermin {wunsch:%d.%m.%Y} (Vorlage Aufhebungsvertrag)",
            ),
        ]

    def clean(self) -> dict[str, Any]:
        from apps.stammdaten import services

        daten: dict[str, Any] = super().clean() or {}
        zugang, wunsch = daten.get("zugang_am"), daten.get("zum_wunsch")
        if self.vertrag is None or zugang is None:
            return daten
        ende = services.wunsch_vor_fristende(self.vertrag, zugang, wunsch)
        if ende is None:
            daten["wunsch_wahl"] = ""
            return daten
        self.fristende = ende
        self._wahl_setzen(ende, wunsch)
        if daten.get("wunsch_wahl") not in (self.FRISTENDE, self.AUFHEBUNG):
            self.add_error(
                "wunsch_wahl",
                f"Der Wunschtermin {wunsch:%d.%m.%Y} liegt vor dem Fristende "
                f"{ende:%d.%m.%Y}. Bitte wählen: Kündigung zum Fristende oder Aufhebung "
                "zum Wunschtermin.",
            )
        return daten


class VertragsparteiForm(forms.Form):
    partei = forms.ModelChoiceField(Partei.objects.all(), label="Partei")
    rolle = forms.ChoiceField(choices=Vertragspartei.Rolle.choices, label="Rolle")


class EinheitAuswahl(forms.Select):
    """Auswahl, deren Optionen Werte zum Vorbelegen tragen (``data-werte``), z. B. die
    Zielmiete einer Einheit für das Feld „Miete“ (``static/js/bausteine.js``)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.werte: dict[str, dict[str, str]] = {}

    def create_option(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        option: dict[str, Any] = super().create_option(*args, **kwargs)
        werte = self.werte.get(str(option["value"]))
        if werte:
            option["attrs"]["data-werte"] = json.dumps(werte)
        return option


class EinheitFeld(forms.ModelChoiceField):  # type: ignore[type-arg]
    """Einheit mit Typ, Bezeichnung und Zielmiete im Text (Review 2, B6)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.lagen: dict[int, Any] = {}

    def label_from_instance(self, obj: Any) -> str:
        from apps.kern import zahlen

        teile = [obj.nw_id, str(obj.get_typ_display())]
        if obj.bezeichnung:
            teile.append(obj.bezeichnung)
        lage = self.lagen.get(obj.pk)
        if lage is not None:
            if lage.zielmiete is not None:
                teile.append(f"Ziel {zahlen.euro(lage.zielmiete)}")
            if not lage.ist_frei:
                teile.append(lage.status_text)
        return " · ".join(teile)


class VertragNeuForm(forms.Form):
    """Neuer Vertrag (Review 2, B5 und B6).

    - Einheit: nur freie Einheiten, mit Typ und Zielmiete; ``alle`` zeigt alle vermietbaren.
    - Miete: aus der Zielmiete vorbelegt (beim Wechsel der Einheit im Browser nachgezogen).
    - Mieter: ID einer vorhandenen Partei (Suchfeld mit Namen) oder „neu“; dann legen die
      Felder ``neu_*`` die Partei mit E-Mail und Telefon gleich mit an.
    - Kaution: Betrag oder „keine Kaution“ (Betrag 0); leer heißt „noch offen“.
    """

    NEU = "neu"

    einheit = EinheitFeld(Einheit.objects.none(), label="Einheit", widget=EinheitAuswahl)
    alle = forms.BooleanField(label="auch vermietete Einheiten zeigen", required=False)
    mieter = forms.CharField(label="Mieter", required=False)
    partei_suche = forms.CharField(label="Partei suchen", required=False, max_length=100)
    neu_vorname = forms.CharField(label="Vorname", required=False, max_length=100)
    neu_nachname = forms.CharField(label="Nachname", required=False, max_length=100)
    neu_firma = forms.CharField(
        label="Firma", required=False, max_length=200, help_text="Nur bei einer Firma."
    )
    neu_email = forms.EmailField(
        label="E-Mail",
        required=False,
        max_length=200,
        error_messages={"invalid": "Bitte eine gültige E-Mail-Adresse angeben."},
    )
    neu_telefon = forms.CharField(label="Telefon", required=False, max_length=50)
    beginn = forms.DateField(label="Beginn", widget=Datum())
    miete = forms.DecimalField(
        label="Miete je Monat (netto)",
        min_value=0,
        decimal_places=2,
        localize=True,
        widget=forms.TextInput(attrs={"inputmode": "decimal", "autocomplete": "off"}),
        help_text="Vorbelegt mit der Zielmiete der Einheit.",
    )
    kaution = forms.DecimalField(
        label="Kaution",
        min_value=0,
        decimal_places=2,
        required=False,
        localize=True,
        widget=forms.TextInput(attrs={"inputmode": "decimal", "autocomplete": "off"}),
        help_text="Leer lassen, wenn sie noch nicht feststeht.",
    )
    keine_kaution = forms.BooleanField(label="keine Kaution", required=False)
    ende = forms.DateField(label="Ende (nur bei Befristung)", required=False, widget=Datum())
    zahlweise = forms.ChoiceField(
        choices=[c for c in Kondition.Zahlweise.choices if c[0]],
        label="Zahlweise",
        initial=Kondition.Zahlweise.MONATLICH,
    )
    pauschale = forms.DecimalField(
        label="Pauschale je Monat", min_value=0, decimal_places=2, required=False
    )
    steuersatz = forms.DecimalField(
        label="Umsatzsteuer (%)", min_value=0, decimal_places=2, required=False
    )
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
    kuendigungsregel = forms.ChoiceField(label="Kündigungsfrist", required=False, choices=())
    anfrage = forms.IntegerField(required=False, widget=forms.HiddenInput)

    #: Felder unter „Weitere Angaben“ (zugeklappt).
    WEITERE = ("ende", "zahlweise", "pauschale", "steuersatz", "vertragsart", "mandant")

    def __init__(self, *args: Any, heute: Any = None, **kwargs: Any) -> None:

        from apps.stammdaten import einheitenliste

        super().__init__(*args, **kwargs)
        self.fields["kuendigungsregel"].choices = _regel_auswahl(  # type: ignore[attr-defined]
            "– Standard der Vertragsart –"
        )
        heute = heute or zeit.heute()
        vermietbar = Einheit.objects.exclude(typ=Einheit.Typ.WOHNUNG).filter(
            aufgeloest_am__isnull=True
        )
        self.lagen = {lage.einheit.pk: lage for lage in einheitenliste.lagen(heute, vermietbar)}
        feld: EinheitFeld = self.fields["einheit"]  # type: ignore[assignment]
        feld.lagen = self.lagen
        self.alle_zeigen = bool(self._wert("alle"))
        gewaehlt = str(self._wert("einheit") or "")
        if self.alle_zeigen:
            ids = list(self.lagen)
        else:
            ids = [pk for pk, lage in self.lagen.items() if lage.ist_frei]
            if gewaehlt.isdigit() and int(gewaehlt) in self.lagen:
                ids.append(int(gewaehlt))
        feld.queryset = vermietbar.filter(pk__in=ids).order_by("nw_id")
        widget: EinheitAuswahl = feld.widget  # type: ignore[assignment]
        widget.werte = {
            str(pk): {"miete": f"{lage.zielmiete:.2f}".replace(".", ",")}
            for pk, lage in self.lagen.items()
            if lage.zielmiete is not None
        }
        self.partei: Partei | None = None

    def _wert(self, name: str) -> Any:
        if self.is_bound:
            return self.data.get(self.add_prefix(name))
        return self.initial.get(name, self.fields[name].initial)

    @property
    def mieter_wahl(self) -> str:
        """Gewählter Mieter: ID, „neu“ oder leer (für die Vorlage)."""
        return str(self._wert("mieter") or "")

    @property
    def mieter_vorschlag(self) -> Partei | None:
        """Die vorgewählte Partei (aus der Anfrage oder nach einem Fehler), für die Treffer."""
        wahl = self.mieter_wahl
        return Partei.objects.filter(pk=int(wahl)).first() if wahl.isdigit() else None

    def clean_mieter(self) -> str:
        wahl = (self.cleaned_data.get("mieter") or "").strip()
        if wahl == self.NEU:
            return wahl
        if wahl.isdigit():
            self.partei = Partei.objects.filter(pk=int(wahl)).first()
            if self.partei is not None:
                return wahl
        raise forms.ValidationError(
            "Bitte eine Partei über die Suche wählen oder „Neue Partei“ anlegen."
        )

    def clean(self) -> dict[str, Any]:
        daten = super().clean() or {}
        if daten.get("mieter") == self.NEU and not any(
            (daten.get(f) or "").strip() for f in ("neu_vorname", "neu_nachname", "neu_firma")
        ):
            self.add_error("neu_nachname", "Bitte den Namen der neuen Partei angeben.")
        if daten.get("keine_kaution"):
            daten["kaution"] = Decimal("0.00")
        return daten


class UebergabeVorabForm(forms.Form):
    datum = forms.DateField(label="Datum", widget=Datum())
    vertrag_unterschrieben = forms.BooleanField(
        label="Vertrag ist heute vor Ort unterschrieben", required=False
    )


class KautionSchrittForm(forms.Form):
    """Schritt „Kaution“ der Übergabe (Review 2, A10)."""

    keine = forms.BooleanField(label="keine Kaution vereinbart", required=False)
    betrag = forms.DecimalField(
        label="Betrag (Euro)",
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        required=False,
        localize=True,
        widget=forms.TextInput(attrs={"inputmode": "decimal", "autocomplete": "off"}),
    )
    art = forms.ChoiceField(
        label="Art",
        choices=[c for c in Kaution.Art.choices if c[0]],
        initial=Kaution.Art.UEBERWEISUNG,
        required=False,
    )
    eingang_am = forms.DateField(
        label="eingegangen am",
        required=False,
        widget=Datum(),
        help_text="Bar: heute. Bürgschaft oder Sparbuch: Tag, an dem die Urkunde vorliegt.",
    )
    konto = forms.CharField(
        label="Konto",
        max_length=100,
        required=False,
        help_text="Bezeichnung des Kontos, z. B. „Vivid Kautionen“. Keine IBAN.",
    )

    def clean(self) -> dict[str, Any]:
        daten = super().clean() or {}
        if not daten.get("keine") and daten.get("betrag") is None and "betrag" not in self.errors:
            self.add_error(
                "betrag",
                "Bitte den Betrag der Kaution angeben oder „keine Kaution vereinbart“ anhaken.",
            )
        return daten


class ZaehlerSchnellForm(forms.Form):
    """Zähler direkt in der Übergabe anlegen, wenn an der Einheit keiner hängt."""

    einheit = forms.ModelChoiceField(Einheit.objects.none(), label="Einheit", empty_label=None)
    art = forms.ChoiceField(label="Art", choices=())
    nummer = forms.CharField(label="Zählernummer", max_length=50)

    def __init__(self, *args: Any, einheiten: Any, **kwargs: Any) -> None:
        from apps.zaehler.models import Zaehler

        super().__init__(*args, **kwargs)
        self.fields["einheit"].queryset = einheiten  # type: ignore[attr-defined]
        self.fields["art"].choices = Zaehler.Art.choices  # type: ignore[attr-defined]


class SchluesselSchnellForm(forms.Form):
    """Schlüssel direkt in der Übergabe erfassen (Bestand beim Hof)."""

    einheit = forms.ModelChoiceField(Einheit.objects.none(), label="Einheit", empty_label=None)
    art = forms.ChoiceField(label="Art", choices=Schluessel.Art.choices)
    anzahl = forms.IntegerField(label="Anzahl", min_value=1, max_value=50, initial=2)
    bezeichnung = forms.CharField(
        label="Bezeichnung", max_length=100, required=False, help_text="z. B. „Tor“. Keine Namen."
    )

    def __init__(self, *args: Any, einheiten: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["einheit"].queryset = einheiten  # type: ignore[attr-defined]


def foto_pruefen(datei: Any) -> str | None:
    """Fehlermeldung, wenn die Datei kein Foto ist, sonst None."""
    art = getattr(datei, "content_type", "") or ""
    if not art.startswith("image/"):
        return "Nur Fotos (Bilddateien) sind erlaubt."
    if datei.size > MAX_FOTO_BYTES:
        return "Das Foto ist größer als 15 MB."
    return None


#: Vorlagenarten, aus denen sich ein einzelnes Schreiben zum Vertrag erzeugen lässt
#: (Review 2, B7): Ihre Platzhalter füllt der Kontext des Vertrags allein. Kündigungs-
#: bestätigung, Protokolle und Mahnstufen entstehen an ihren Vorgängen, das Vertragsdokument
#: über „Vertragsdokument als PDF erzeugen“.
EINZELSCHREIBEN_ARTEN = ("mitteilung",)


class EinzelschreibenForm(forms.Form):
    """„Schreiben erstellen“ an der Vertragsseite: Vorlage und Versandweg wählen."""

    vorlage = forms.ModelChoiceField(
        label="Vorlage",
        queryset=None,
        empty_label=None,
        error_messages={"required": "Bitte eine Vorlage wählen."},
    )
    versandweg = forms.ChoiceField(label="Versandweg", required=False, choices=())

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        from apps.dokumente.models import Vorlage
        from apps.dokumente.vorlagenarten import Versandweg

        super().__init__(*args, **kwargs)
        self.fields["vorlage"].queryset = Vorlage.objects.filter(  # type: ignore[attr-defined]
            status=Vorlage.Status.FREIGEGEBEN, art__in=EINZELSCHREIBEN_ARTEN
        ).order_by("art", "name")
        self.fields["versandweg"].choices = [  # type: ignore[attr-defined]
            ("", "wie in der Vorlage vorgesehen"),
            *Versandweg.TITEL.items(),
        ]


class MehrereDateien(forms.ClearableFileInput):
    allow_multiple_selected = True


class UebergabeFotosForm(forms.Form):
    """Nur für die Dateiknöpfe im Schritt „Fotos“ (gelesen wird ``request.FILES``)."""

    fotos = forms.FileField(
        label="Fotos vom Zustand",
        required=False,
        widget=MehrereDateien(attrs={"accept": "image/*"}),
        help_text="Ohne neues Foto geht es einfach weiter.",
    )


class UebergabeUnterschriftForm(forms.Form):
    """Nur für die Dateiknöpfe im Schritt „Zustand und Unterschrift“."""

    unterschrift = forms.FileField(
        label="Unterschrift (Foto der Quittung)",
        required=False,
        widget=forms.ClearableFileInput(attrs={"accept": "image/*"}),
    )


def zaehler_foto_feld(zaehler_pk: int) -> Any:
    """Dateiknöpfe für das Foto eines Zählers im Schritt „Zählerstände“ (Name ``foto_<id>``)."""
    form = forms.Form()
    form.fields[f"foto_{zaehler_pk}"] = forms.FileField(
        label="Foto des Zählers",
        required=False,
        widget=forms.ClearableFileInput(attrs={"accept": "image/*"}),
    )
    return form[f"foto_{zaehler_pk}"]
