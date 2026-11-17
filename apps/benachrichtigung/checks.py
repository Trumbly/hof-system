"""Prüfung für ``manage.py check --deploy``: Ist die Benachrichtigung eingerichtet?"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.checks import CheckMessage, Tags, Warning, register


@register(Tags.compatibility, deploy=True)
def benachrichtigung_eingerichtet(app_configs: Any = None, **kwargs: Any) -> list[CheckMessage]:
    meldungen: list[CheckMessage] = []
    if not settings.HOF_BASIS_URL:
        meldungen.append(
            Warning(
                "HOF_BASIS_URL ist leer: Links in Benachrichtigungen haben keine Adresse.",
                id="benachrichtigung.W001",
            )
        )
    if not settings.HOF_BENACHRICHTIGUNG_MAIL and not (
        settings.HOF_NTFY_URL and settings.HOF_NTFY_THEMA
    ):
        meldungen.append(
            Warning(
                "Kein Kanal für Benachrichtigungen: HOF_BENACHRICHTIGUNG_MAIL oder "
                "HOF_NTFY_URL und HOF_NTFY_THEMA setzen.",
                id="benachrichtigung.W002",
            )
        )
    return meldungen
