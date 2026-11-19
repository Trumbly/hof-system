"""Lauf-Meldungen mit Links (Review 2, B16).

Lauf-Meldungen nennen nur Nummern und IDs („Konto #3: Abruf #12, 4 neu; Arbeitsposten #57“),
weil sie in Logs, Notion und den Status-MCP gehen. Die Oberfläche macht aus jeder Nummer, zu
der es eine Seite gibt, einen Link zum betroffenen Posten. Der Text selbst bleibt, wie er ist.

Erkannt werden „<Wort> #<Zahl>“ (Arbeitsposten, Lauf, Konto, Schreiben, Mahnfall, Umsatz,
Klärfall, Beleg) sowie V-Nummern (V-0005) und NW-IDs (NW-G-0018).
"""

from __future__ import annotations

import re
from collections.abc import Callable

from django.urls import NoReverseMatch, reverse
from django.utils.html import conditional_escape, format_html
from django.utils.safestring import SafeString, mark_safe

MUSTER = re.compile(
    r"\b(?P<wort>Arbeitsposten|Lauf|Konto|Schreiben|Mahnfall|Umsatz|Bankumsatz|Klärfall|Beleg)"
    r" #(?P<nr>\d+)\b"
    r"|\b(?P<vertrag>V-\d{4,})\b"
    r"|\b(?P<einheit>NW-[A-Z]{1,3}-\d{4})\b"
)


def _anker(url_name: str, anker: str) -> Callable[[int], str]:
    return lambda nr: f"{reverse(url_name)}#{anker}-{nr}"


def _seite(url_name: str) -> Callable[[int], str]:
    return lambda nr: reverse(url_name, args=[nr])


#: Wort vor „#<Zahl>“ → Adresse des Postens.
ZIELE: dict[str, Callable[[int], str]] = {
    "Lauf": _anker("kern:laeufe", "lauf"),
    "Konto": _anker("bank:konten", "konto"),
    "Schreiben": _seite("dokumente:schreiben"),
    "Mahnfall": _seite("mahnwesen:fall"),
    "Umsatz": _seite("kassenbuch:umsatz"),
    "Bankumsatz": _seite("kassenbuch:umsatz"),
    "Klärfall": _seite("abgleich:klaerfall"),
    "Beleg": _seite("dokumente:beleg"),
}


def _arbeitsposten_urls(nummern: set[int]) -> dict[int, str]:
    """Arbeitslisten, gefiltert auf die Art des Postens, mit Sprung zum Posten."""
    from django.utils.http import urlencode

    from apps.kern.models import Arbeitsposten

    basis = reverse("kern:arbeitslisten")
    arten = dict(Arbeitsposten.objects.filter(pk__in=nummern).values_list("pk", "art"))
    return {
        nr: f"{basis}?{urlencode({'art': arten[nr]})}#posten-{nr}"
        if nr in arten
        else f"{basis}#posten-{nr}"
        for nr in nummern
    }


def _vertrag_urls(nummern: set[str]) -> dict[str, str]:
    from apps.stammdaten.models import Vertrag

    return {
        nummer: reverse("stammdaten:vertrag", args=[pk])
        for nummer, pk in Vertrag.objects.filter(nummer__in=nummern).values_list("nummer", "pk")
    }


def _einheit_urls(nw_ids: set[str]) -> dict[str, str]:
    from apps.stammdaten.models import Einheit

    return {
        nw_id: reverse("stammdaten:einheit", args=[pk])
        for nw_id, pk in Einheit.objects.filter(nw_id__in=nw_ids).values_list("nw_id", "pk")
    }


def verlinken(text: str) -> SafeString:
    """Der Text, maskiert, mit einem Link je erkannter Nummer."""
    sicher = str(conditional_escape(text or ""))
    treffer = list(MUSTER.finditer(sicher))
    if not treffer:
        return mark_safe(sicher)  # noqa: S308 – oben maskiert
    posten = _arbeitsposten_urls({int(t["nr"]) for t in treffer if t["wort"] == "Arbeitsposten"})
    vertraege = _vertrag_urls({t["vertrag"] for t in treffer if t["vertrag"]})
    einheiten = _einheit_urls({t["einheit"] for t in treffer if t["einheit"]})

    def ersetzen(t: re.Match[str]) -> str:
        url = ""
        if t["wort"] == "Arbeitsposten":
            url = posten.get(int(t["nr"]), "")
        elif t["wort"]:
            try:
                url = ZIELE[t["wort"]](int(t["nr"]))
            except NoReverseMatch:
                url = ""
        elif t["vertrag"]:
            url = vertraege.get(t["vertrag"], "")
        elif t["einheit"]:
            url = einheiten.get(t["einheit"], "")
        if not url:
            return t[0]
        # t[0] ist schon maskiert; format_html würde es ein zweites Mal maskieren.
        return str(format_html('<a href="{}">{}</a>', url, mark_safe(t[0])))  # noqa: S308

    return mark_safe(MUSTER.sub(ersetzen, sicher))  # noqa: S308 – Teile maskiert
