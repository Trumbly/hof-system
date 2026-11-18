"""Rolle ``hof_statusmcp`` (NOLOGIN): ``SELECT`` nur auf die Sichten ``sm_*``, sonst nichts.

Idempotent (siehe ``apps.statusmcp.rechte``). Rückwärts bleibt die Rolle stehen: Rollen
gelten für den ganzen Postgres-Server, und der Login-Benutzer ist ihr Mitglied.
"""

from django.db import migrations

from apps.statusmcp.rechte import RECHTE_SQL


class Migration(migrations.Migration):
    dependencies = [
        ("statusmcp", "0002_sichten"),
    ]

    operations = [
        migrations.RunSQL(sql=RECHTE_SQL, reverse_sql=migrations.RunSQL.noop),
    ]
