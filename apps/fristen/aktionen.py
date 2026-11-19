"""Aktion „Frist erledigt“ an Arbeitsposten „Frist fällig“.

Sie setzt das Erledigt-Datum der Frist auf heute; damit schließt sich auch der Posten.
"""

from __future__ import annotations

from django.urls import reverse

from apps.fristen import services
from apps.fristen.models import Frist
from apps.kern import aktionen, verweise
from apps.kern.models import Arbeitsposten, Benutzer


def _frist_erledigen(posten: Arbeitsposten, benutzer: Benutzer) -> object:
    return services.frist_erledigen(services.frist_zum_posten(posten))


aktionen.registrieren(services.AKTION_ERLEDIGEN, "Frist erledigt", _frist_erledigen)
# Review 1, Punkt 8: Der Posten verlinkt auf seine Frist in der Liste.
verweise.registrieren(
    Frist,
    "Frist ansehen",
    lambda f: f"{reverse('fristen:liste')}#frist-{f.pk}",
    beschreibung=lambda f: f"{f.get_art_display()} am {f.datum:%d.%m.%Y}",
)
