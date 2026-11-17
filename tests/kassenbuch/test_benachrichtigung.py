"""Anbindung an die Benachrichtigung aus Schritt 9a (Schritt 2)."""

from __future__ import annotations

from django.conf import settings

from apps.kassenbuch import services


def test_posten_des_kassenbuchs_melden_sich() -> None:
    """Schritt 2: Offene Umsätze und ein offener Monatsabschluss warten auf Max; beide Arten
    stehen im Standard der Benachrichtigung."""
    arten = set(settings.HOF_BENACHRICHTIGUNG_ARTEN)
    assert {services.ART_EINORDNEN, services.ART_ABSCHLUSS} <= arten
