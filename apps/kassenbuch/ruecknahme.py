"""Rücknahme eines Vorgangs im Kassenbuch (Review 2, A4).

Eine Buchung, die der Vorgang angelegt hat, wird storniert (wie im Stornoweg). Teilt ein
Vorgang einen Umsatz auf mehrere Buchungen auf, storniert die erste Rücknahme alle; die
weiteren haben dann nichts mehr zu tun.
"""

from __future__ import annotations

from apps.kassenbuch import services
from apps.kassenbuch.models import Buchung
from apps.kern import services as kern


def _buchung_aus_vorgang(buchung: Buchung) -> kern.Schritt | None:
    if buchung.ist_storno:
        raise kern.RuecknahmeNichtMoeglich(
            f"Buchung #{buchung.pk} ist eine Stornobuchung. Den Umsatz bitte neu buchen."
        )
    if Buchung.objects.filter(storno_von=buchung).exists():
        return None  # schon storniert, etwa mit einer anderen Buchung desselben Umsatzes
    services.umsatz_stornieren(buchung.umsatz)
    return kern.Schritt(f"Buchungen zu Umsatz #{buchung.umsatz_id} storniert")


def registrieren() -> None:
    kern.ruecknahmeweg_registrieren(Buchung, anlage=_buchung_aus_vorgang)
