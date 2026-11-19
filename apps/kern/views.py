"""Seiten des Kerns: Gesundheits-Endpunkt.

Arbeitslisten stehen in ``arbeitslisten``, Protokoll und Läufe in ``protokoll_seite`` und
``laeufe_seite``.
"""

from __future__ import annotations

from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.http import HttpRequest, HttpResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

PROTOKOLL_ANZAHL = 50
LAEUFE_ANZAHL = 50


# --- Gesundheit ----------------------------------------------------------------------


@transaction.non_atomic_requests
@never_cache
@require_GET
def gesund(request: HttpRequest) -> HttpResponse:
    """Gesundheits-Endpunkt für deploy.sh und die Healthchecks der Container.

    Ohne Anmeldung, ohne Daten: nur „ok“ und die Version (``HOF_VERSION``). Erreichbar ist
    er nur über Tailscale, weil Caddy und die Firewall nichts anderes durchlassen. Kann die
    Datenbank nicht antworten, kommt 503 mit „fehler“ und wieder nur der Version.
    """
    version = settings.HOF_VERSION
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except DatabaseError:
        return HttpResponse(
            f"fehler {version}\n", status=503, content_type="text/plain; charset=utf-8"
        )
    return HttpResponse(f"ok {version}\n", content_type="text/plain; charset=utf-8")
