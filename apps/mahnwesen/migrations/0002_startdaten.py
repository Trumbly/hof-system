"""Startdaten der Stufenfolge nach der SOP je Vertragsart (``apps.mahnwesen.startdaten``)."""

from __future__ import annotations

from typing import Any

from django.db import migrations


def anlegen(apps: Any, schema_editor: Any) -> None:
    from apps.mahnwesen import startdaten

    mahnstufe = apps.get_model("mahnwesen", "Mahnstufe")
    vorhanden = set(mahnstufe.objects.values_list("vertragsart", flat=True))
    for felder in startdaten.fehlende(vorhanden):
        mahnstufe.objects.create(**felder)


def entfernen(apps: Any, schema_editor: Any) -> None:
    # Die Stufen bleiben; ein Rückwärtsschritt löscht keine Konfiguration.
    pass


class Migration(migrations.Migration):
    dependencies = [("mahnwesen", "0001_initial")]

    operations = [migrations.RunPython(anlegen, entfernen)]
