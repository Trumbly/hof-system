"""Migrationen passen zu den Modellen (CLAUDE.md, Regel 4.10)."""

import pytest
from django.core.management import call_command


@pytest.mark.django_db
def test_keine_fehlenden_migrationen() -> None:
    """Schritt 0b: Modelle, Protokoll-Modelle und Trigger stehen vollständig in den Migrationen."""
    call_command("makemigrations", "--check", "--dry-run", verbosity=0)
