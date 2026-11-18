"""Aktionen an „Zahlungen klären“ (H3.6, ``apps.kern.aktionen``).

Zuordnen, Aufteilen und Regel anlegen führen auf ihre Seite (dort wählt man Vertrag, Posten
und Beträge; der Vorschlag ist vorbelegt). Zurückstellen ist die allgemeine Aktion des Kerns.
„Nicht vertragsbezogen“ und die beiden Antworten auf eine Rückbuchung wirken sofort.
"""

from __future__ import annotations

from django.urls import reverse

from apps.abgleich import services
from apps.abgleich.models import Klaerfall
from apps.kern import aktionen
from apps.kern.models import Arbeitsposten, Benutzer


def _seite(name: str):  # type: ignore[no-untyped-def]
    def url(posten: Arbeitsposten) -> str:
        return reverse(f"abgleich:{name}", args=[posten.bezug_id])

    return url


def _klaerfall(posten: Arbeitsposten) -> Klaerfall:
    return Klaerfall.objects.get(pk=posten.bezug_id)


def _nicht_vertragsbezogen(posten: Arbeitsposten, benutzer: Benutzer) -> object:
    return services.klaerfall_nicht_vertragsbezogen(_klaerfall(posten))


def _rueckbuchung(posten: Arbeitsposten, benutzer: Benutzer) -> object:
    return services.rueckbuchung_bestaetigen(_klaerfall(posten))


def _keine_rueckbuchung(posten: Arbeitsposten, benutzer: Benutzer) -> object:
    return services.keine_rueckbuchung(_klaerfall(posten))


aktionen.registrieren("zahlung_zuordnen", "Zuordnen", seite=_seite("zuordnen"))
aktionen.registrieren("zahlung_aufteilen", "Aufteilen", seite=_seite("aufteilen"))
aktionen.registrieren("zahlerregel_anlegen", "Regel anlegen", seite=_seite("regel_aus_klaerfall"))
aktionen.registrieren("nicht_vertragsbezogen", "Nicht vertragsbezogen", _nicht_vertragsbezogen)
aktionen.registrieren("rueckbuchung_bestaetigen", "Rückbuchung bestätigen", _rueckbuchung)
aktionen.registrieren("keine_rueckbuchung", "Keine Rückbuchung", _keine_rueckbuchung)
