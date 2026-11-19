"""Aktionen an den Arbeitsposten der Bank (Review 1, Punkt 8; ``apps.kern.aktionen``).

- „Erneut abrufen“ startet den Lauf ``bank_abruf`` für das Konto des Postens. Gelingt der
  Abruf, erledigt er den Posten selbst (``services.abruf_gelungen``); scheitert er wieder,
  bleibt der Posten offen, und die Meldung nennt den Grund.
- „Freigabe erneuern“ führt auf die Seite der Bankfreigabe des Kontos.

„Erledigt“ gibt es an „Bankabruf gescheitert“ und den Posten zur Freigabe nicht: Erledigt ist
so ein Posten erst, wenn Abruf oder Freigabe wieder stimmen.
"""

from __future__ import annotations

from django.urls import reverse

from apps.bank import services
from apps.bank.models import Bankabruf, Bankfreigabe, Bankkonto
from apps.kern import aktionen
from apps.kern.models import Arbeitsposten, Benutzer
from apps.kern.posteingang import Ergebnis, PostenFehler

ERNEUT_ABRUFEN = "bank_erneut_abrufen"
FREIGABE_ERNEUERN = "bank_freigabe_erneuern"


def _konto(posten: Arbeitsposten) -> Bankkonto:
    bezug = posten.bezug
    if isinstance(bezug, Bankfreigabe):
        return bezug.konto
    if isinstance(bezug, Bankkonto):
        return bezug
    raise ValueError(f"Arbeitsposten #{posten.pk} gehört zu keinem Bankkonto.")


def _erneut_abrufen(posten: Arbeitsposten, benutzer: Benutzer) -> Ergebnis:
    konto = _konto(posten)
    try:
        ergebnis = services.abruf_starten(konto)
    except services.BankFehler as fehler:
        raise PostenFehler(str(fehler)) from None
    abruf = ergebnis.abruf
    posten.refresh_from_db()
    if posten.status == Arbeitsposten.Status.OFFEN:
        grund = abruf.meldung if abruf is not None else ergebnis.lauf.meldung
        return Ergebnis(
            text=f"Der Abruf für Konto #{konto.pk} ist wieder gescheitert: {grund}",
            link=reverse("bank:freigabe", args=[konto.pk]),
            link_text="Freigabe erneuern",
        )
    gelungen = abruf is not None and abruf.status == Bankabruf.Status.ERFOLGREICH
    neu = abruf.neu if abruf is not None and gelungen else 0
    return Ergebnis(
        text=f"Abruf für Konto #{konto.pk} gelungen, {neu} neue Umsätze.",
        link=f"{reverse('bank:umsaetze')}?konto={konto.pk}",
        link_text="Umsätze ansehen",
    )


def _freigabe_seite(posten: Arbeitsposten) -> str:
    return reverse("bank:freigabe", args=[_konto(posten).pk])


aktionen.registrieren(ERNEUT_ABRUFEN, "Erneut abrufen", _erneut_abrufen)
aktionen.registrieren(FREIGABE_ERNEUERN, "Freigabe erneuern", seite=_freigabe_seite)
