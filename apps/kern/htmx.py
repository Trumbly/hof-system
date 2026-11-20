"""Das HTMX-Muster der neuen Seiten (Review 2, B-Leitlinien und C3): Dialog und Toast.

- Ein Knopf lädt den Inhalt des Dialogs (``hx-get`` mit Ziel ``#dialog-inhalt``); das
  Skript ``static/js/bausteine.js`` öffnet den Dialog, sobald der Inhalt da ist.
- Das Formular im Dialog sendet per ``hx-post`` in denselben Dialog. Ist etwas falsch,
  kommt das Formular mit den Fehlern am Feld zurück (Status 200, damit HTMX es einsetzt).
- Gelingt es, antwortet die Seite mit ``erfolg()``: Sie ersetzt die betroffenen Teile der
  Seite per ``hx-swap-oob`` (die Seite springt nicht), hängt eine Toast-Meldung mit Link an
  und schließt den Dialog über das Ereignis ``dialogSchliessen``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from django.http import HttpRequest, HttpResponse
from django.template.loader import render_to_string

EREIGNIS_SCHLIESSEN = "dialogSchliessen"


def ist_htmx(request: HttpRequest) -> bool:
    return request.headers.get("HX-Request") == "true"


def toast(
    request: HttpRequest,
    text: str,
    *,
    link: str = "",
    link_text: str = "ansehen",
    art: str = "ok",
) -> str:
    """Eine Toast-Meldung als HTML, die sich per ``hx-swap-oob`` an ``#toasts`` hängt."""
    return render_to_string(
        "bausteine/toast_oob.html",
        {"text": text, "link": link, "link_text": link_text, "art": art},
        request=request,
    )


def erfolg(
    request: HttpRequest,
    teile: Iterable[str],
    meldung: str,
    *,
    link: str = "",
    link_text: str = "ansehen",
    ereignisse: Iterable[str] = (),
) -> HttpResponse:
    """Antwort auf ein gelungenes Speichern im Dialog.

    ``teile`` sind fertige HTML-Stücke, jedes mit ``id`` und ``hx-swap-oob="true"``. Der
    Dialog selbst wird geleert und geschlossen. ``ereignisse`` löst die Seite zusätzlich aus,
    etwa ``posteingangNeu`` (die Arbeitsliste lädt neu).
    """
    html = "".join(teile) + toast(request, meldung, link=link, link_text=link_text)
    antwort = HttpResponse(html)
    antwort["HX-Trigger"] = json.dumps(
        {EREIGNIS_SCHLIESSEN: True, **dict.fromkeys(ereignisse, True)}
    )
    return antwort
