"""Links an den Arbeitsposten der Bank (``apps.kern.verweise``)."""

from __future__ import annotations

import datetime

from django.urls import reverse
from django.utils.http import urlencode

from apps.bank.models import Bankfreigabe, Bankkonto, Kontostand
from apps.kern import verweise


def _konto(konto: Bankkonto) -> str:
    return f"{reverse('bank:konten')}#konto-{konto.pk}"


def _freigabe(freigabe: Bankfreigabe) -> str:
    return reverse("bank:freigabe", args=[freigabe.konto_id])


def _kontostand(stand: Kontostand) -> str:
    von = stand.datum - datetime.timedelta(days=7)
    parameter: dict[str, str | int] = {
        "konto": stand.konto_id,
        "von": von.isoformat(),
        "bis": stand.datum.isoformat(),
    }
    return f"{reverse('bank:umsaetze')}?{urlencode(parameter)}"


verweise.registrieren(Bankkonto, "Konto ansehen", _konto, beschreibung=lambda k: k.bezeichnung)
verweise.registrieren(
    Bankfreigabe,
    "Freigabe erneuern",
    _freigabe,
    beschreibung=lambda f: f"{f.konto.bezeichnung}, gültig bis {f.gueltig_bis:%d.%m.%Y}",
)
verweise.registrieren(
    Kontostand,
    "Umsätze ansehen",
    _kontostand,
    beschreibung=lambda s: f"{s.konto.bezeichnung}, Stand {s.datum:%d.%m.%Y}",
)
