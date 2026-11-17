"""Anbindung an die Benachrichtigung aus Schritt 9a (Schritt 3c)."""

from __future__ import annotations

from django.conf import settings

from apps.dokumente import services


def test_posten_des_versands_melden_sich() -> None:
    """Schritt 3c: Fehlt die Kopie in „Gesendet“ oder ist ein Brief storniert, wartet ein
    Arbeitsposten auf Max; beide Arten stehen im Standard der Benachrichtigung."""
    arten = set(settings.HOF_BENACHRICHTIGUNG_ARTEN)
    assert {services.ART_KOPIE_FEHLT, services.ART_BRIEF_STORNIERT} <= arten
