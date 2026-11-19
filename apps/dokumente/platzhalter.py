"""Platzhalter in Vorlagen: Django-Template-Syntax in einer abgeschlossenen Engine.

Eine Vorlage ist HTML mit Platzhaltern wie ``{{ empfaenger.name }}`` (CLAUDE.md,
Abschnitt 1: Texte an Mieter sind Vorlagen mit Platzhaltern). Gerendert wird mit einer
eigenen ``Engine``, die nur wenig kann:

- **Tags:** nur ``{% if %}`` (mit ``elif``/``else``) und ``{% for %}`` (mit ``empty``).
  ``{% load %}``, ``{% include %}``, ``{% extends %}``, ``{% url %}`` usw. gibt es nicht;
  sie ergeben beim Prüfen einen Fehler.
- **Filter:** eine feste Liste (``FILTER``), dazu ``euro``, ``datum`` und ``einzeilig``.
- **Kontext:** nur einfache Werte (Text, Zahl, Decimal, Datum, Wahrheitswert, Listen,
  Wörterbücher mit Text-Schlüsseln), nie Modell-Objekte (``kontext_pruefen``).
- **Unbekannte Platzhalter** sind ein Fehler, kein leerer Text: beim Prüfen gegen die
  erlaubten Platzhalter der Vorlagenart (``pruefen``) und beim Rendern gegen den
  Kontext (``rendern``).
- Werte werden für HTML maskiert (Autoescape).
"""

from __future__ import annotations

import datetime
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.template import Context, Engine, Library, Template, TemplateSyntaxError, defaultfilters
from django.template import defaulttags as dt
from django.template.base import FilterExpression, Node, Variable, VariableNode
from django.utils import translation

from apps.kern import zahlen


class PlatzhalterFehler(ValueError):
    """Eine Vorlage oder ihr Kontext passt nicht. Die Meldung nennt nur Platzhalter."""


#: Was ein Platzhalter bedeutet, für Fehlermeldungen in ganzen Sätzen (Review 2, A8):
#: „Für den Versand per Mail fehlt die E-Mail-Adresse des Empfängers.“ statt
#: „fehlt empfaenger.email“. Ein Pfad ohne Eintrag nimmt den des nächsten Oberbegriffs.
BEDEUTUNG: dict[str, str] = {
    "empfaenger": "der Empfänger",
    "empfaenger.name": "der Name des Empfängers",
    "empfaenger.anrede": "die Anrede des Empfängers",
    "empfaenger.anschrift": "die Anschrift des Empfängers",
    "empfaenger.email": "die E-Mail-Adresse des Empfängers",
    "absender": "der Absender",
    "absender.name": "der Name des Absenders",
    "absender.anschrift": "die Anschrift des Absenders",
    "absender.email": "die E-Mail-Adresse des Absenders",
    "absender.telefon": "die Telefonnummer des Absenders",
    "absender.bank": "die Bankverbindung des Absenders",
    "absender.steuernummer": "die Steuernummer des Absenders",
    "datum": "das Datum des Schreibens",
    "zeichen": "das Zeichen des Schreibens",
    "vertrag": "die Angaben zum Vertrag",
    "vertrag.nummer": "die Vertragsnummer",
    "vertrag.beginn": "der Vertragsbeginn",
    "vertrag.ende": "das Vertragsende",
    "vertrag.kuendigungsfrist": "die Kündigungsfrist",
    "einheit": "die Angaben zur Einheit",
    "einheiten": "die Einheiten des Vertrags",
    "objekt": "die Angaben zum Objekt",
    "objekt.anschrift": "die Anschrift des Objekts",
    "mandant": "der Vermieter",
    "mieter": "die Mieter",
    "zahler": "die Zahler",
    "buergen": "die Bürgen",
    "kondition": "die Miete und Zahlweise",
    "staffel": "die Mietstaffel",
    "kaution": "die Kaution",
    "betrag": "die Beträge",
    "betrag.offen": "der offene Betrag",
    "forderung": "die Aufstellung der offenen Posten",
    "frist": "die Zahlungsfrist",
    "mahnung": "die Angaben zur Mahnstufe",
    "kuendigung": "die Angaben zur Kündigung",
    "aufhebung": "die Angaben zur Aufhebung",
    "abrechnung": "die Kautionsabrechnung",
    "uebergabe": "die Angaben zur Übergabe",
}


def bedeutung(pfad: str) -> str:
    """Was ein Platzhalter bedeutet, als Satzteil („die E-Mail-Adresse des Empfängers“).

    Ohne eigenen Eintrag gilt der nächste Oberbegriff; ganz Unbekanntes heißt
    „der Platzhalter „…““."""
    teile = pfad.split(".")
    while teile:
        treffer = BEDEUTUNG.get(".".join(teile))
        if treffer:
            return treffer
        teile.pop()
    return f"der Platzhalter „{pfad}“"


class UnbekannterPlatzhalter(PlatzhalterFehler):
    def __init__(self, pfad: str) -> None:
        self.pfad = pfad
        super().__init__(
            f"Die Vorlage nutzt den Platzhalter „{pfad}“ ({bedeutung(pfad)}), "
            "für dieses Schreiben gibt es dazu aber keinen Wert."
        )


# --- Bibliothek der Engine -----------------------------------------------------------

bibliothek = Library()
bibliothek.tag("if", dt.do_if)
bibliothek.tag("for", dt.do_for)

#: Erlaubte Filter aus Django.
FILTER = (
    "capfirst",
    "date",
    "default",
    "default_if_none",
    "first",
    "floatformat",
    "join",
    "last",
    "length",
    "linebreaks",
    "linebreaksbr",
    "lower",
    "pluralize",
    "title",
    "upper",
    "yesno",
)
for _name in FILTER:
    bibliothek.filter(_name, getattr(defaultfilters, _name), is_safe=True)


@bibliothek.filter(name="euro", is_safe=True)
def euro(wert: Any) -> str:
    """Betrag in deutscher Schreibweise: ``1234.5`` → ``1.234,50 €`` (``apps.kern.zahlen``,
    dieselbe Schreibweise wie auf den Seiten)."""
    return zahlen.euro(wert)


@bibliothek.filter(name="datum", is_safe=True)
def datum(wert: Any) -> str:
    """Datum als ``17.11.2026``."""
    if isinstance(wert, datetime.datetime | datetime.date):
        return wert.strftime("%d.%m.%Y")
    return "" if wert is None else str(wert)


@bibliothek.filter(name="einzeilig", is_safe=False)
def einzeilig(wert: Any) -> str:
    """Mehrzeiliges in eine Zeile: ``Weg 1\n12345 Ort`` → ``Weg 1 · 12345 Ort``."""
    return " · ".join(z.strip() for z in str(wert or "").splitlines() if z.strip())


class _Fehlwert(str):
    """``string_if_invalid``: Statt eines leeren Texts bricht das Rendern ab.

    Django prüft ``"%s" in string_if_invalid`` und ruft dann ``string_if_invalid % name``
    auf; genau dort wirft dieser Wert den Fehler.
    """

    def __contains__(self, _: object) -> bool:
        return True

    def __mod__(self, pfad: object) -> str:
        raise UnbekannterPlatzhalter(str(pfad))

    def __bool__(self) -> bool:
        return True


class _Engine(Engine):
    # Keine Standard-Bibliotheken: kein {% load %}, {% include %}, {% url %} …
    default_builtins: list[str] = []  # noqa: RUF012  (wie in Django)


# Engine.get_template_builtins importiert dieses Modul und liest ``register``.
register = bibliothek

ENGINE = _Engine(
    dirs=[],
    app_dirs=False,
    libraries={},
    builtins=["apps.dokumente.platzhalter"],
    autoescape=True,
    debug=False,
    string_if_invalid=_Fehlwert("‹fehlt›"),
)

# --- Prüfen --------------------------------------------------------------------------


@dataclass(frozen=True)
class Verwendung:
    """Alle Platzhalter einer Vorlage."""

    pfade: frozenset[str]
    schleifenvariablen: frozenset[str]


def _ausdruecke_in_bedingung(bedingung: Any) -> Iterator[FilterExpression]:
    if bedingung is None:
        return
    wert = getattr(bedingung, "value", None)
    if isinstance(wert, FilterExpression):
        yield wert
    yield from _ausdruecke_in_bedingung(getattr(bedingung, "first", None))
    yield from _ausdruecke_in_bedingung(getattr(bedingung, "second", None))


def _variablen(ausdruck: FilterExpression) -> Iterator[Variable]:
    if isinstance(ausdruck.var, Variable) and ausdruck.var.lookups is not None:
        yield ausdruck.var
    for _funktion, argumente in ausdruck.filters:
        for ist_variable, argument in argumente:
            if ist_variable and isinstance(argument, Variable) and argument.lookups is not None:
                yield argument


def kompilieren(text: str) -> Template:
    try:
        return Template(text, engine=ENGINE)
    except TemplateSyntaxError as fehler:
        raise PlatzhalterFehler(f"Vorlage lässt sich nicht lesen: {fehler}") from fehler


def verwendung(text: str) -> Verwendung:
    """Liest alle Platzhalter einer Vorlage, ohne sie zu rendern."""
    vorlage = kompilieren(text)
    ausdruecke: list[FilterExpression] = []
    schleife: set[str] = {"forloop"}
    for knoten in vorlage.nodelist.get_nodes_by_type(Node):
        if isinstance(knoten, VariableNode):
            ausdruecke.append(knoten.filter_expression)
        elif isinstance(knoten, dt.IfNode):
            for bedingung, _ in knoten.conditions_nodelists:
                ausdruecke.extend(_ausdruecke_in_bedingung(bedingung))
        elif isinstance(knoten, dt.ForNode):
            if isinstance(knoten.sequence, FilterExpression):
                ausdruecke.append(knoten.sequence)
            schleife.update(knoten.loopvars)
    pfade = {
        ".".join(variable.lookups or ())
        for ausdruck in ausdruecke
        for variable in _variablen(ausdruck)
    }
    return Verwendung(pfade=frozenset(pfade), schleifenvariablen=frozenset(schleife))


def _erlaubt(pfad: str, erlaubt: Iterable[str]) -> bool:
    return any(
        pfad == eintrag or pfad.startswith(eintrag + ".") or eintrag.startswith(pfad + ".")
        for eintrag in erlaubt
    )


def unerlaubte_platzhalter(text: str, erlaubt: Iterable[str]) -> list[str]:
    """Platzhalter der Vorlage, die nicht in ``erlaubt`` stehen (sortiert)."""
    erlaubt = list(erlaubt)
    genutzt = verwendung(text)
    return sorted(
        pfad
        for pfad in genutzt.pfade
        if pfad.split(".", 1)[0] not in genutzt.schleifenvariablen and not _erlaubt(pfad, erlaubt)
    )


def pruefen(text: str, erlaubt: Iterable[str]) -> None:
    """Wirft ``PlatzhalterFehler``, wenn die Vorlage unlesbar ist oder Unbekanntes nutzt."""
    unbekannt = unerlaubte_platzhalter(text, erlaubt)
    if unbekannt:
        liste = ", ".join(f"„{p}“" for p in unbekannt)
        raise PlatzhalterFehler(
            f"Die Vorlage nutzt Platzhalter, die es für diese Vorlagenart nicht gibt: {liste}."
        )


# --- Kontext und Rendern -------------------------------------------------------------

_EINFACH = (str, int, float, Decimal, datetime.date, bool, type(None))


def kontext_pruefen(kontext: Mapping[str, Any], pfad: str = "") -> None:
    """Nur einfache Werte: Modell-Objekte und andere Objekte sind nicht erlaubt."""
    for schluessel, wert in kontext.items():
        if not isinstance(schluessel, str) or not schluessel.isidentifier():
            raise PlatzhalterFehler(
                f"Im Kontext steht ein ungültiger Schlüssel unter „{pfad or 'oben'}“."
            )
        _wert_pruefen(wert, f"{pfad}{schluessel}")


def _wert_pruefen(wert: Any, pfad: str) -> None:
    if isinstance(wert, _EINFACH):
        return
    if isinstance(wert, Mapping):
        kontext_pruefen(wert, pfad + ".")
        return
    if isinstance(wert, list | tuple):
        for eintrag in wert:
            _wert_pruefen(eintrag, pfad)
        return
    raise PlatzhalterFehler(
        f"Im Kontext steht unter „{pfad}“ ein {type(wert).__name__}; erlaubt sind nur "
        "einfache Werte. Der Aufrufer gibt die Werte frei, die eine Vorlage lesen darf."
    )


def rendern(text: str, kontext: Mapping[str, Any]) -> str:
    """Rendert eine Vorlage. Fehlt ein Platzhalter im Kontext, gibt es einen Fehler."""
    kontext_pruefen(kontext)
    vorlage = kompilieren(text)
    genutzt = verwendung(text)
    for pfad in genutzt.pfade:
        wurzel = pfad.split(".", 1)[0]
        if wurzel not in genutzt.schleifenvariablen and wurzel not in kontext:
            raise UnbekannterPlatzhalter(pfad)
    with translation.override("de"):
        return vorlage.render(Context(dict(kontext), autoescape=True))


def beispielkontext(erlaubt: Iterable[str], text: str = "") -> dict[str, Any]:
    """Kontext für die Vorschau einer Vorlage: Jeder Platzhalter zeigt seinen Namen.

    Mit ``text`` kommen die Platzhalter dazu, die die Vorlage unterhalb eines erlaubten
    Eintrags nutzt (``betrag.offen`` unter ``betrag``).
    """
    pfade = set(erlaubt)
    if text:
        genutzt = verwendung(text)
        pfade |= {p for p in genutzt.pfade if p.split(".", 1)[0] not in genutzt.schleifenvariablen}
    # Ein Pfad, unter dem noch etwas steht, wird ein Wörterbuch statt eines Texts.
    kontext: dict[str, Any] = {}
    for pfad in sorted(pfade):
        teile = pfad.split(".")
        ziel = kontext
        for teil in teile[:-1]:
            unter = ziel.setdefault(teil, {})
            if not isinstance(unter, dict):
                unter = ziel[teil] = {}
            ziel = unter
        if not isinstance(ziel.get(teile[-1]), dict):
            ziel[teile[-1]] = f"‹{pfad}›"
    return kontext
