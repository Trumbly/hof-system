"""Bausteine der neuen Gestaltung (Review 2, C3) als Template-Tags.

Jeder Baustein hat seine Vorlage unter ``templates/bausteine/`` und einen Eintrag auf der
Seite ``/stil/``. Verwendung::

    {% load bausteine %}
    {% badge vertrag.status %}                 Status-Badge (Text aus BADGES)
    {% badge "offen" "3 offen" %}              eigener Text
    {% feld form.miete %}                      Label oben, Hilfetext, Fehler am Feld
    {% dateiknoepfe form.fotos %}              „Foto aufnehmen“ und „Datei wählen“
    {% kennzahl "Saldo" konto.saldo|euro art="fehler" unter="2 Posten" link=url %}
    {% unternavigation "Kassenbuch" "kassenbuch:buchungen=Buchungen" … %}  Reiter
    {% pdf_vorschau url "Titel" %}             PDF groß (A4), in .geteilt mitlaufend
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django import forms, template
from django.forms.boundfield import BoundField, BoundWidget
from django.template.base import FilterExpression, Parser, Token, token_kwargs
from django.urls import reverse
from django.utils.html import format_html_join
from django.utils.safestring import SafeString, mark_safe

from apps.kern import navigation as navi

register = template.Library()


@dataclass(frozen=True)
class Badge:
    text: str
    art: str  # ok, hinweis, warnung, fehler, neutral


#: Status → (Text, Farbe). Schlüssel wie in den Modellen (ohne Umlaute).
BADGES: dict[str, Badge] = {
    "aktiv": Badge("aktiv", "ok"),
    "gekuendigt": Badge("gekündigt", "warnung"),
    "entwurf": Badge("Entwurf", "neutral"),
    "beendet": Badge("beendet", "neutral"),
    "offen": Badge("offen", "hinweis"),
    "teilweise": Badge("teilweise bezahlt", "hinweis"),
    "bezahlt": Badge("bezahlt", "ok"),
    "ueberfaellig": Badge("überfällig", "fehler"),
    "gesperrt": Badge("gesperrt", "fehler"),
    "bereit": Badge("bereit", "hinweis"),
    "versandt": Badge("versandt", "ok"),
    "gesendet": Badge("versandt", "ok"),
    "zugestellt": Badge("zugestellt", "ok"),
    "verworfen": Badge("verworfen", "neutral"),
    "storniert": Badge("storniert", "neutral"),
    "erledigt": Badge("erledigt", "ok"),
    "begonnen": Badge("begonnen", "hinweis"),
    "abgeschlossen": Badge("abgeschlossen", "ok"),
    # Läufe (kern.Lauf.Status, Review 2, B16)
    "laeuft": Badge("läuft", "hinweis"),
    "erfolgreich": Badge("erfolgreich", "ok"),
    "mit_fehlern": Badge("mit Fehlern", "warnung"),
    "abgebrochen": Badge("abgebrochen", "fehler"),
    "uebersprungen": Badge("übersprungen", "neutral"),
    "gescheitert": Badge("gescheitert", "fehler"),  # Bankabruf
    # Zählerstand (zaehler.Zaehlerstand, Schätzwert statt Ablesung)
    "geschaetzt": Badge("geschätzt", "warnung"),
}

ARTEN = ("ok", "hinweis", "warnung", "fehler", "neutral")


def badge_fuer(status: str, text: str = "") -> Badge:
    bekannt = BADGES.get(status)
    if bekannt is None:
        return Badge(text or status, status if status in ARTEN else "neutral")
    return Badge(text or bekannt.text, bekannt.art)


@register.inclusion_tag("bausteine/badge.html")
def badge(status: str, text: str = "") -> dict[str, Any]:
    """Status-Badge. ``status`` ist ein Schlüssel aus ``BADGES`` oder direkt eine Farbe."""
    return {"badge": badge_fuer(str(status), str(text))}


def _attrs(feld: BoundField) -> dict[str, str]:
    """Zusätzliche Attribute am Eingabefeld: Fehlerzustand, Hilfetext, Zahlentastatur."""
    attrs: dict[str, str] = {"class": "feld-eingabe"}
    widget = feld.field.widget
    if isinstance(feld.field, forms.DecimalField | forms.FloatField):
        attrs["inputmode"] = "decimal"
    elif isinstance(feld.field, forms.IntegerField):
        attrs["inputmode"] = "numeric"
    if isinstance(widget, forms.Select):
        attrs["class"] = "feld-auswahl"
    elif isinstance(widget, forms.MultiWidget) and all(
        isinstance(w, forms.Select) for w in widget.widgets
    ):
        attrs["class"] = "feld-auswahl"  # z. B. Monat und Jahr als zwei Auswahllisten
    if isinstance(widget, forms.CheckboxInput):
        attrs["class"] = "feld-haken"
    if feld.errors:
        attrs["aria-invalid"] = "true"
        attrs["aria-describedby"] = f"{feld.auto_id}-fehler"
    elif feld.help_text:
        attrs["aria-describedby"] = f"{feld.auto_id}-hilfe"
    return attrs


def _ist_gruppe(feld: BoundField) -> bool:
    """Radio- oder Checkbox-Gruppe: mehrere Optionen unter einer Überschrift."""
    return isinstance(feld.field.widget, forms.RadioSelect | forms.CheckboxSelectMultiple)


def _optionen(feld: BoundField) -> list[BoundWidget]:
    """Die Optionen einer Radio- oder Checkbox-Gruppe, je eine mit ihrem Eingabefeld.

    Django setzt die Attribute eines ``RadioSelect`` auch an den umschließenden ``div``; eine
    Klasse wie ``feld-haken`` (20 × 20 px) machte daraus eine winzige Box, aus der Text,
    Fehler und das nächste Feld herausquollen (Review 3, Punkt 2). Deshalb rendert der
    Baustein die Optionen selbst; die Attribute stehen nur an den Eingabefeldern.
    ``aria-describedby`` trägt das ``fieldset`` (``bausteine/feld.html``)."""
    widget = feld.field.widget
    attrs: dict[str, Any] = {"class": "feld-haken"}
    if kennung := widget.attrs.get("id") or feld.auto_id:
        attrs["id"] = kennung
    attrs = feld.build_widget_attrs(attrs)  # required, disabled, aria-invalid
    return [
        BoundWidget(widget, daten, feld.form.renderer)
        for daten in widget.subwidgets(feld.html_name, feld.value(), attrs=attrs)
    ]


def _beschrieben_von(feld: BoundField) -> str:
    """IDs von Fehler und Hilfetext für ``aria-describedby`` einer Gruppe (Fehler zuerst)."""
    teile = []
    if feld.errors:
        teile.append(f"{feld.auto_id}-fehler")
    if feld.help_text:
        teile.append(f"{feld.auto_id}-hilfe")
    return " ".join(teile)


@register.inclusion_tag("bausteine/feld.html")
def feld(feld: BoundField, **optionen: Any) -> dict[str, Any]:
    """Formularfeld: Label über dem Feld, Hilfetext darunter, Fehler direkt am Feld.

    Checkboxen stehen mit ihrem Label in einer Zeile (Ziel mindestens 44 px).
    Radio- und Checkbox-Gruppen (Review 3, Punkt 2) stehen in einem ``fieldset`` mit
    ``legend``: Optionen untereinander, Label neben dem Feld, Fehler unter der Gruppe.
    """
    widget = feld.field.widget
    if _ist_gruppe(feld):
        return {
            "feld": feld,
            "optionen": _optionen(feld),
            "beschrieben_von": _beschrieben_von(feld),
            "breit": optionen.get("breit", False),
        }
    return {
        "feld": feld,
        "eingabe": feld.as_widget(attrs=_attrs(feld)),
        "haken": isinstance(widget, forms.CheckboxInput),
        "gruppe": isinstance(widget, forms.MultiWidget),
        "breit": optionen.get("breit", False),
    }


@register.inclusion_tag("bausteine/dateiknoepfe.html")
def dateiknoepfe(
    feld: BoundField, kamera: bool = True, sofort: bool = False, datei_text: str = ""
) -> dict[str, Any]:
    """Eigene Dateiknöpfe (Review 2, C4): „Foto aufnehmen“ (Kamera am Handy) und „Datei
    wählen“, beide mit Vorschau der gewählten Bilder. Beide Eingaben tragen den Namen des
    Felds; gesendet wird, was gewählt ist (leere Dateifelder überträgt der Browser nicht
    als Datei).

    Mit ``sofort`` sendet die Wahl einer Datei das Formular gleich ab (ein Klick statt
    zwei, etwa „Beleg hochladen“, Review 2, B9); ohne JavaScript bleibt der Knopf des
    Formulars."""
    widget = feld.field.widget
    mehrere = bool(getattr(widget, "allow_multiple_selected", False))
    accept = widget.attrs.get("accept", "")
    return {
        "feld": feld,
        "name": feld.html_name,
        "id": feld.auto_id or feld.html_name,
        "mehrere": mehrere,
        "accept": accept,
        "kamera": kamera,
        "pflicht": feld.field.required,
        "sofort": sofort,
        "datei_text": datei_text,
    }


#: Attribute, die die Dateiknöpfe selbst setzen (bausteine/_datei_eingabe.html). „required“
#: fällt weg: Ein Feld hat zwei Eingaben (Kamera und Datei), geprüft wird auf dem Server.
DATEI_EIGENE_ATTRIBUTE = frozenset(
    {"name", "id", "class", "type", "accept", "capture", "multiple", "required", "data-vorschau"}
)


@register.filter
def datei_attrs(attrs: dict[str, Any] | None) -> SafeString:
    """Übrige Attribute eines Dateifelds (etwa ``aria-describedby``) für die Dateiknöpfe."""
    if not attrs:
        return mark_safe("")
    frei = [(k, v) for k, v in attrs.items() if k not in DATEI_EIGENE_ATTRIBUTE]
    an = [(k,) for k, v in frei if v is True]
    mit_wert = [(k, v) for k, v in frei if v not in (True, False, None)]
    return format_html_join("", " {}", an) + format_html_join("", ' {}="{}"', mit_wert)


@register.inclusion_tag("bausteine/kennzahl.html")
def kennzahl(
    titel: str, wert: Any, *, art: str = "", unter: str = "", link: str = ""
) -> dict[str, Any]:
    """Kennzahl-Kachel: Titel, große Zahl, Zeile darunter, optional Link."""
    return {"titel": titel, "wert": wert, "art": art, "unter": unter, "link": link}


@register.inclusion_tag("bausteine/pdf_vorschau.html")
def pdf_vorschau(url: str, titel: str = "PDF") -> dict[str, Any]:
    """PDF groß im A4-Verhältnis mit Link „in neuem Fenster öffnen“ (Review 2, B9, B13).
    In der geteilten Ansicht (``.geteilt``) läuft es beim Scrollen mit."""
    return {"url": url, "titel": titel}


# --- Bausteine mit Inhalt: {% seitenkopf %}…{% endseitenkopf %} ------------------------


class _RahmenNode(template.Node):
    """Rendert den Inhalt zwischen Anfangs- und End-Tag in eine Rahmen-Vorlage."""

    def __init__(
        self,
        vorlage: str,
        nodelist: template.NodeList,
        positionen: list[tuple[str, FilterExpression]],
        benannt: dict[str, FilterExpression],
        felder: tuple[str, ...],
    ) -> None:
        self.felder = felder
        self.vorlage = vorlage
        self.nodelist = nodelist
        self.positionen = positionen
        self.benannt = benannt

    def render(self, context: template.Context) -> str:
        # Jedes Feld der Rahmen-Vorlage ist gesetzt, damit nichts aus der Seite durchscheint.
        werte: dict[str, Any] = dict.fromkeys(self.felder, "")
        werte |= {name: ausdruck.resolve(context) for name, ausdruck in self.positionen}
        werte |= {name: ausdruck.resolve(context) for name, ausdruck in self.benannt.items()}
        # Der Inhalt ist schon gerendert und dabei maskiert worden.
        werte["inhalt"] = mark_safe(self.nodelist.render(context))  # noqa: S308
        vorlage = context.template.engine.get_template(self.vorlage)  # type: ignore[union-attr]
        with context.push(**werte):
            return str(vorlage.render(context))


def _rahmen(
    name: str, vorlage: str, positionen: tuple[str, ...], weitere: tuple[str, ...] = ()
) -> None:
    def kompilieren(parser: Parser, token: Token) -> _RahmenNode:
        teile = token.split_contents()[1:]
        benannt = token_kwargs([t for t in teile if "=" in t], parser)
        frei = [t for t in teile if "=" not in t]
        if len(frei) > len(positionen):
            raise template.TemplateSyntaxError(f"{name}: zu viele Angaben")
        if unbekannt := set(benannt) - set(positionen) - set(weitere):
            raise template.TemplateSyntaxError(f"{name}: unbekannt {sorted(unbekannt)}")
        pos = [(positionen[i], parser.compile_filter(t)) for i, t in enumerate(frei)]
        nodelist = parser.parse((f"end{name}",))
        parser.delete_first_token()
        return _RahmenNode(vorlage, nodelist, pos, benannt, positionen + weitere)

    register.tag(name, kompilieren)


#: {% seitenkopf "Titel" "Meta-Zeile" %}<Aktionen rechts>{% endseitenkopf %}
_rahmen("seitenkopf", "bausteine/seitenkopf.html", ("titel", "meta"), ("id",))
#: {% karte "Titel" link=url link_text="alle ansehen" %}<Inhalt>{% endkarte %}
_rahmen("karte", "bausteine/karte.html", ("titel",), ("link", "link_text", "zahl", "id"))
#: {% aufklapper "Zusammenfassung" offen=True %}<Inhalt>{% endaufklapper %}
_rahmen("aufklapper", "bausteine/aufklapper.html", ("titel",), ("offen", "zahl"))


# --- Unternavigation eines Bereichs (Bank, Kassenbuch, Mahnwesen, Anbindungen) --------


@register.inclusion_tag("bausteine/unternavigation.html", takes_context=True)
def unternavigation(
    context: template.Context, beschriftung: str, *eintraege: str
) -> dict[str, Any]:
    """Unternavigation als Reiter (Review 2, B11): eine Zeile, am Handy waagrecht scrollbar,
    statt loser Links mit „·“, die im Raster umbrechen.

    Jeder Eintrag ist ``"url-name=Titel"``; mehrere URL-Namen mit Komma, der erste ist das
    Ziel, alle zählen als aktiv (``"mahnwesen:faelle,mahnwesen:fall=Mahnfälle"``)::

        {% unternavigation "Bank" "bank:konten,bank:freigabe=Konten" "bank:umsaetze=Umsätze" %}
    """
    request = context.get("request")
    treffer = getattr(request, "resolver_match", None)
    aktuell = treffer.view_name if treffer is not None else ""
    reiter = []
    for eintrag in eintraege:
        namen, titel = str(eintrag).split("=", 1)
        liste = [n.strip() for n in namen.split(",")]
        reiter.append({"titel": titel, "url": reverse(liste[0]), "aktiv": aktuell in liste})
    return {"reiter": reiter, "beschriftung": beschriftung}


# --- Navigation (Seitenleiste, Leiste unten am Handy) ---------------------------------


@register.simple_tag(takes_context=True)
def navigation(context: template.Context) -> list[tuple[str, list[navi.Link]]]:
    return navi.gruppen(context["request"])


@register.simple_tag(takes_context=True)
def navigation_unten(context: template.Context) -> list[navi.Link]:
    return navi.unten(context["request"])


#: Stufen der Django-Meldungen → Farbe der Toast-Meldung.
MELDUNGSARTEN = {"success": "ok", "info": "hinweis", "warning": "warnung", "error": "fehler"}


@register.filter
def meldungsart(tags: str) -> str:
    for tag in str(tags).split():
        if tag in MELDUNGSARTEN:
            return MELDUNGSARTEN[tag]
    return "ok"
