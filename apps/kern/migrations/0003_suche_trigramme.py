"""Trigramm-Indexe für die Suche in der Kopfzeile (Review 2, B1, apps.kern.suche).

Django sucht ``feld__icontains`` in Postgres als ``UPPER("feld"::text) LIKE UPPER('%…%')``.
Ein GIN-Index mit ``gin_trgm_ops`` auf genau diesem Ausdruck macht das ab drei Zeichen
schnell, auch mitten im Wort. Die Indexe liegen hier im Kern (und nicht in den Modellen der
Apps), weil sie nur der Suche dienen; das Modell der Apps bleibt unverändert.

``pg_trgm`` gehört zu Postgres (contrib) und ist eine „trusted extension“: Der Eigentümer
der Datenbank darf sie anlegen.
"""

from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations

#: (Tabelle, Spalte) für die Suche; Namen der Indexe: suche_<tabelle>_<spalte>
SPALTEN = (
    ("stammdaten_partei", "vorname"),
    ("stammdaten_partei", "nachname"),
    ("stammdaten_partei", "firma"),
    ("stammdaten_partei", "ort"),
    ("stammdaten_kontaktweg", "wert"),
    ("bank_bankumsatz", "gegenpartei"),
    ("bank_bankumsatz", "verwendungszweck"),
    ("dokumente_beleg", "rechnungsnummer"),
    ("dokumente_eingangsrechnung", "rechnungsnummer"),
    ("dokumente_lieferant", "name"),
)


def _name(tabelle: str, spalte: str) -> str:
    return f"suche_{tabelle}_{spalte}"[:63]


class Migration(migrations.Migration):
    dependencies = [
        ("kern", "0002_lauf_status_uebersprungen"),
        ("stammdaten", "0004_kaution_art_uebergabe_uebersprungen"),
        ("bank", "0001_initial"),
        ("dokumente", "0006_briefkopf_freigabe"),
    ]

    operations = [
        TrigramExtension(),
        *(
            migrations.RunSQL(
                sql=(
                    f'CREATE INDEX IF NOT EXISTS "{_name(t, s)}" ON "{t}" '
                    f'USING gin ((UPPER("{s}"::text)) gin_trgm_ops)'
                ),
                reverse_sql=f'DROP INDEX IF EXISTS "{_name(t, s)}"',
            )
            for t, s in SPALTEN
        ),
    ]
