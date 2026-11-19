"""Startseite des Hof-Systems: das Hof-Cockpit (H10.1), in der neuen Gestaltung (Review 2, C).

Die Kacheln kommen aus dem Register ``apps.cockpit.kacheln``; jede liest bei jedem Aufruf
direkt aus der Datenbank. Die Kennzahlen oben lesen dieselben Kontexte (keine zweite
Abfrage). Die Adresse bleibt ``kern:startseite``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString

from apps.cockpit import kacheln
from apps.kern import zahlen, zeit
from apps.kern.models import Objekt
from apps.kern.templatetags.bausteine import begriff_html

logger = logging.getLogger(__name__)


def _kachel(request: HttpRequest, kachel: kacheln.Kachel) -> tuple[str, dict[str, Any] | None]:
    """HTML der Kachel und ihr Kontext (``None``, wenn sie nicht darstellbar ist)."""
    try:
        kontext = kachel.kontext_funktion()
        return render_to_string(kachel.template, kontext, request=request), kontext
    except Exception:
        # Eine kaputte Kachel nimmt nicht die ganze Startseite mit.
        logger.exception("Kachel %s nicht darstellbar", kachel.name)
        html = render_to_string("cockpit/_kachel_fehler.html", {"kachel": kachel}, request=request)
        return html, None


@dataclass(frozen=True)
class Kennzahl:
    titel: str
    wert: str
    unter: str | SafeString = ""
    art: str = ""
    link: str = ""


def _rueckstand_unter(k: dict[str, Any]) -> SafeString:
    """Zeile unter dem Rückstand: Verträge, Klärung und, mit Erklärung, was nicht prüfbar ist
    (Review 3, Punkt 1: dieselbe Aufteilung wie die Kachel Offene Posten)."""
    teile = [format_html("{} Vertr{}", k["anzahl"], "äge" if k["anzahl"] != 1 else "ag")]
    if k.get("in_klaerung"):
        teile.append(format_html("{} in Klärung", k["in_klaerung"]))
    nicht_pruefbar = k.get("nicht_pruefbar")
    if nicht_pruefbar is not None and nicht_pruefbar.summe:
        teile.append(
            format_html("{} {}", zahlen.euro(nicht_pruefbar.summe), begriff_html("nicht_pruefbar"))
        )
    return format_html_join(" · ", "{}", ((teil,) for teil in teile))


def _kennzahlen(kontexte: dict[str, dict[str, Any]]) -> list[Kennzahl]:
    """Vier Zahlen für den ersten Blick, aus den Kontexten der Kacheln."""
    zahl: list[Kennzahl] = []
    if (k := kontexte.get("freigaben")) is not None:
        zahl.append(
            Kennzahl(
                "Offene Freigaben",
                str(k["summe"]),
                f"{k['anzahl_arten']} Art{'en' if k['anzahl_arten'] != 1 else ''}",
                link=reverse("kern:arbeitslisten"),
            )
        )
    if (k := kontexte.get("rueckstaende")) is not None and "anzahl" in k:
        zahl.append(
            Kennzahl(
                "Rückstand",
                zahlen.euro(k["summe"]),
                _rueckstand_unter(k),
                art="fehler" if k["anzahl"] else "",
                link=reverse("abgleich:rueckstaende"),
            )
        )
    if (k := kontexte.get("fristen")) is not None:
        zahl.append(
            Kennzahl(
                "Fristen überfällig",
                str(k["ueberfaellig"]),
                f"{k['bald_anzahl']} in den nächsten {k['tage']} Tagen",
                art="warnung" if k["ueberfaellig"] else "",
                link=reverse("fristen:liste") + "?zeitraum=14",
            )
        )
    if (k := kontexte.get("leerstand")) is not None:
        zahl.append(
            Kennzahl(
                "Leerstand",
                str(k["frei_anzahl"]),
                f"Einheit{'en' if k['frei_anzahl'] != 1 else ''} frei",
                link=reverse("stammdaten:leerstand"),
            )
        )
    return zahl


@login_required
def startseite(request: HttpRequest) -> HttpResponse:
    html: list[str] = []
    kontexte: dict[str, dict[str, Any]] = {}
    for kachel in kacheln.kacheln():
        teil, kontext = _kachel(request, kachel)
        html.append(teil)
        if kontext is not None:
            kontexte[kachel.name] = kontext
    return render(
        request,
        "cockpit/startseite.html",
        {
            "kacheln": html,
            "kennzahlen": _kennzahlen(kontexte),
            "heute": zeit.heute(),
            "objekte": Objekt.objects.filter(aktiv=True).select_related("mandant"),
        },
    )
