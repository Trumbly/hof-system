"""Links an den Arbeitsposten des Kassenbuchs (``apps.kern.verweise``)."""

from __future__ import annotations

from django.urls import reverse

from apps.bank.models import Bankumsatz
from apps.kassenbuch.models import Monatsabschluss
from apps.kern import verweise


def _umsatz(umsatz: Bankumsatz) -> str:
    return reverse("kassenbuch:umsatz", args=[umsatz.pk])


def _abschluss(abschluss: Monatsabschluss) -> str:
    return f"{reverse('kassenbuch:abschluesse')}#abschluss-{abschluss.pk}"


def _umsatz_text(umsatz: Bankumsatz) -> str:
    """Datum, Konto und Gegenpartei (nur für die Oberfläche)."""
    teile = [f"{umsatz.buchungsdatum:%d.%m.%Y}", umsatz.konto.bezeichnung, umsatz.gegenpartei]
    return " · ".join(t for t in teile if t)


verweise.registrieren(Bankumsatz, "Umsatz ansehen", _umsatz, beschreibung=_umsatz_text)
verweise.registrieren(
    Monatsabschluss,
    "Abschluss ansehen",
    _abschluss,
    beschreibung=lambda a: f"{a.mandant.kuerzel} · {a.monat:%m/%Y}",
)
