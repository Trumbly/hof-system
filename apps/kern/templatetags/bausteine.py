"""Bausteine der neuen Gestaltung (Review 2, C3) als Template-Tags.

Jeder Baustein hat seine Vorlage unter ``templates/bausteine/`` und einen Eintrag auf der
Seite ``/stil/``. Verwendung::

    {% load bausteine %}
    {% badge vertrag.status %}                 Status-Badge (Text aus BADGES)
    {% badge "offen" "3 offen" %}              eigener Text
    {% feld form.miete %}                      Label oben, Hilfetext, Fehler am Feld
    {% dateiknoepfe form.fotos %}              „Foto aufnehmen“ und „Datei wählen“
    {% kennzahl "Saldo" konto.saldo|euro art="fehler" unter="2 Posten" link=url %}
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django import forms, template
from django.forms.boundfield import BoundField
from django.template.base import FilterExpression, Parser, Token, token_kwargs
from django.utils.safestring import mark_safe

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
    if isinstance(widget, forms.RadioSelect | forms.CheckboxSelectMultiple):
        attrs["class"] = "feld-haken"
    elif isinstance(widget, forms.Select):
        attrs["class"] = "feld-auswahl"
    if isinstance(widget, forms.CheckboxInput):
        attrs["class"] = "feld-haken"
    if feld.errors:
        attrs["aria-invalid"] = "true"
        attrs["aria-describedby"] = f"{feld.auto_id}-fehler"
    elif feld.help_text:
        attrs["aria-describedby"] = f"{feld.auto_id}-hilfe"
    return attrs


@register.inclusion_tag("bausteine/feld.html")
def feld(feld: BoundField, **optionen: Any) -> dict[str, Any]:
    """Formularfeld: Label über dem Feld, Hilfetext darunter, Fehler direkt am Feld.

    Checkboxen stehen mit ihrem Label in einer Zeile (Ziel mindestens 44 px).
    """
    widget = feld.field.widget
    return {
        "feld": feld,
        "eingabe": feld.as_widget(attrs=_attrs(feld)),
        "haken": isinstance(widget, forms.CheckboxInput),
        "optionen": isinstance(widget, forms.RadioSelect | forms.CheckboxSelectMultiple),
        "breit": optionen.get("breit", False),
    }


@register.inclusion_tag("bausteine/dateiknoepfe.html")
def dateiknoepfe(feld: BoundField, kamera: bool = True) -> dict[str, Any]:
    """Eigene Dateiknöpfe (Review 2, C4): „Foto aufnehmen“ (Kamera am Handy) und „Datei
    wählen“, beide mit Vorschau der gewählten Bilder. Beide Eingaben tragen den Namen des
    Felds; gesendet wird, was gewählt ist (leere Dateifelder überträgt der Browser nicht
    als Datei)."""
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
    }


@register.inclusion_tag("bausteine/kennzahl.html")
def kennzahl(
    titel: str, wert: Any, *, art: str = "", unter: str = "", link: str = ""
) -> dict[str, Any]:
    """Kennzahl-Kachel: Titel, große Zahl, Zeile darunter, optional Link."""
    return {"titel": titel, "wert": wert, "art": art, "unter": unter, "link": link}


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
