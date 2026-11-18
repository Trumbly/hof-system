"""Startseite des Hof-Systems: das Hof-Cockpit (H10.1).

Die Kacheln kommen aus dem Register ``apps.cockpit.kacheln``; jede liest bei jedem Aufruf
direkt aus der Datenbank. Die Adresse bleibt ``kern:startseite``.
"""

from __future__ import annotations

import logging

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string

from apps.cockpit import kacheln
from apps.kern.models import Objekt

logger = logging.getLogger(__name__)


def _kachel_html(request: HttpRequest, kachel: kacheln.Kachel) -> str:
    try:
        kontext = kachel.kontext_funktion()
        return render_to_string(kachel.template, kontext, request=request)
    except Exception:
        # Eine kaputte Kachel nimmt nicht die ganze Startseite mit.
        logger.exception("Kachel %s nicht darstellbar", kachel.name)
        return render_to_string("cockpit/_kachel_fehler.html", {"kachel": kachel}, request=request)


@login_required
def startseite(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "cockpit/startseite.html",
        {
            "kacheln": [_kachel_html(request, k) for k in kacheln.kacheln()],
            "objekte": Objekt.objects.filter(aktiv=True).select_related("mandant"),
        },
    )
