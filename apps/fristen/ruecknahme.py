"""Rücknahme eines Vorgangs und Fristen (Review 2, A4).

Eine Frist, die der Vorgang angelegt hat, **entfernt** die Rücknahme (allgemeiner Weg des
Kerns), wenn sie seither unverändert ist. Begründung: Eine Frist ist nichts Gebuchtes; sie
folgt aus dem, was der Vorgang erfasst hat (etwa aus der Kündigung). Eine erledigte Frist
bliebe in der Liste und behauptete, es sei etwas getan worden. Das Protokoll behält Anlage
und Entfernen. Ein Arbeitsposten „Frist fällig“ dazu wird erledigt (Kern).

Offene Fristen an einem Objekt, das die Rücknahme entfernt und die ein anderer Vorgang
angelegt hat, werden erledigt: Ihr Bezug ist weg.
"""

from __future__ import annotations

from typing import Any

from django.db import models

from apps.fristen import services
from apps.fristen.models import Frist
from apps.kern import services as kern
from apps.kern import zeit


def _fristen_zu_entferntem(modell: type[models.Model], objekt_id: Any) -> list[kern.Schritt]:
    from django.contrib.contenttypes.models import ContentType

    schritte = []
    for frist in Frist.objects.filter(
        bezug_typ=ContentType.objects.get_for_model(modell),
        bezug_id=objekt_id,
        erledigt_am__isnull=True,
    ):
        services.frist_erledigen(frist, zeit.heute())
        schritte.append(kern.Schritt(f"Frist #{frist.pk} ({frist.get_art_display()}) erledigt"))
    return schritte


def registrieren() -> None:
    kern.ruecknahme_aufraeumen_registrieren(_fristen_zu_entferntem)
