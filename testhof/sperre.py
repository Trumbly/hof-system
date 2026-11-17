"""Sperre für die Produktion: Der Testhof entsteht nur in Entwicklung und Tests.

``hof/settings/prod.py`` setzt ``DJANGO_ENV=prod`` als Standard. Der Generator legt
ein Testkonto mit bekanntem Passwort und TOTP-Geheimnis an und schreibt erfundene
Daten; beides darf nie auf den Server.

Dieses Modul importiert nur die Standardbibliothek, damit die Sperre auch ohne die
Entwicklungs-Abhängigkeiten (Faker) mit klarer Meldung greift.
"""

from __future__ import annotations

import os

UMGEBUNGSVARIABLE = "DJANGO_ENV"
PRODUKTION = "prod"

MELDUNG = (
    "Der Testhof-Generator läuft nicht in der Produktion (DJANGO_ENV=prod). "
    "Er schreibt erfundene Daten und ein Testkonto mit bekanntem Passwort und "
    "TOTP-Geheimnis; das gehört nur in Entwicklung und Tests."
)


class TesthofGesperrt(RuntimeError):
    """Der Generator wurde in der Produktion aufgerufen."""


def umgebung() -> str:
    """Die gesetzte Umgebung, klein geschrieben (leer, wenn nicht gesetzt)."""
    return os.environ.get(UMGEBUNGSVARIABLE, "").strip().lower()


def pruefen() -> None:
    """Wirft ``TesthofGesperrt``, wenn ``DJANGO_ENV=prod`` gesetzt ist."""
    if umgebung() == PRODUKTION:
        raise TesthofGesperrt(MELDUNG)
