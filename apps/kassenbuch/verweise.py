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


verweise.registrieren(Bankumsatz, "Umsatz ansehen", _umsatz)
verweise.registrieren(Monatsabschluss, "Abschluss ansehen", _abschluss)
