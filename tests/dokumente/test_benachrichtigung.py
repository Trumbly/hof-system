"""Anbindung an die Benachrichtigung aus Schritt 9a (Schritt 3a und 3c)."""

from __future__ import annotations

from django.conf import settings

from apps.dokumente import services


def test_posten_des_versands_melden_sich() -> None:
    """Schritt 3c: Fehlt die Kopie in „Gesendet“ oder ist ein Brief storniert, wartet ein
    Arbeitsposten auf Max; beide Arten stehen im Standard der Benachrichtigung."""
    arten = set(settings.HOF_BENACHRICHTIGUNG_ARTEN)
    assert {services.ART_KOPIE_FEHLT, services.ART_BRIEF_STORNIERT} <= arten


def test_belege_zuordnen_meldet_sich() -> None:
    """Schritt 3a: Ein neuer Beleg aus dem Postfach wartet als „Belege zuordnen“ auf Max;
    die Art steht im Standard der Benachrichtigung."""
    assert services.ART_BELEGE_ZUORDNEN in settings.HOF_BENACHRICHTIGUNG_ARTEN
