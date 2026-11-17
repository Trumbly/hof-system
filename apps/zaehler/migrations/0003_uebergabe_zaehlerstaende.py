"""Rückverweis ``uebergabe.zaehlerstaende``, nachdem stammdaten/0002 das JSONField entfernt hat."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("stammdaten", "0002_uebergabe_zaehlerstaende_entfernen"),
        ("zaehler", "0002_json_zaehlerstaende_uebernehmen"),
    ]

    operations = [
        migrations.AlterField(
            model_name="zaehlerstand",
            name="uebergabe",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="zaehlerstaende",
                to="stammdaten.uebergabe",
                verbose_name="Übergabe oder Rückgabe",
            ),
        ),
    ]
