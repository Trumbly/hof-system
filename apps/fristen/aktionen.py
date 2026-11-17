"""Aktion „Frist erledigt“ an Arbeitsposten „Frist fällig“.

Sie setzt das Erledigt-Datum der Frist auf heute; damit schließt sich auch der Posten.
"""

from __future__ import annotations

from apps.fristen import services
from apps.kern import aktionen
from apps.kern.models import Arbeitsposten, Benutzer


def _frist_erledigen(posten: Arbeitsposten, benutzer: Benutzer) -> object:
    return services.frist_erledigen(services.frist_zum_posten(posten))


aktionen.registrieren(services.AKTION_ERLEDIGEN, "Frist erledigt", _frist_erledigen)
