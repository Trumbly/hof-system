"""Sichten des Status-MCP (Schritt 11): nur Spalten ohne Personendaten.

Der Status-MCP liest ausschließlich diese Sichten (Rolle ``hof_statusmcp``, Migration
``0003_rechte``). ``tests/statusmcp/test_sichten.py`` prüft jede Spalte jeder Basistabelle,
die eine Sicht liest (``information_schema.view_column_usage``), gegen das Register der
Personenfelder.

Live aus den Tabellen (einfache Spalten, keine Logik in Python):
- ``sm_arbeitsposten``: Art, Status, Fälligkeit; keine Hinweise, kein Bezug.
- ``sm_laeufe``: der letzte Lauf je Name mit Status und Zählern; keine Meldung.
- ``sm_fristen``: offene Fristen mit Datum, Art, Quelle; NW-IDs bzw. Kürzel aus der
  Momentaufnahme (``statusmcp_kennzahl``, Bereich „frist“), kein Hinweis.
- ``sm_kassenbuch_monat``: Summe mit Vorzeichen je Monat, Objekt-Kürzel und Kategorie, die
  Kategorie nur als ID und Art. Ihre Bezeichnung ist Personenfeld (eine Kategorie kann einen
  Namen tragen, etwa eine Entnahme). Stornobuchungen haben die Gegenrichtung und heben ihr
  Original in der Summe auf, wie in ``kassenbuch.auswertung``.

Aus der Momentaufnahme des Laufs ``statusmcp_aktualisieren`` (``statusmcp_kennzahl``):
- ``sm_rueckstaende`` und ``sm_rueckstaende_einheiten``
- ``sm_datenstand`` und ``sm_datenstand_kennungen``
- ``sm_leerstand``

Achtung bei späteren Migrationen: Postgres verweigert ``ALTER COLUMN … TYPE`` und
``DROP COLUMN`` an Spalten, die eine Sicht liest. Dann die Sicht in derselben Migration
vorher löschen, danach neu anlegen und ``apps.statusmcp.rechte.RECHTE_SQL`` ausführen.
"""

from django.db import migrations

SICHTEN = {
    "sm_arbeitsposten": """
        SELECT art, status, faellig_am
        FROM kern_arbeitsposten
    """,
    "sm_laeufe": """
        SELECT DISTINCT ON (name)
            name, status, gestartet_am, beendet_am, gebucht, arbeitsposten, fehler
        FROM kern_lauf
        ORDER BY name, gestartet_am DESC, id DESC
    """,
    "sm_fristen": """
        SELECT
            f.id AS frist_id,
            f.datum,
            f.art,
            f.quelle,
            f.datum - f.vorlauf_tage AS faellig_ab,
            coalesce(
                array_agg(k.kennung ORDER BY k.kennung) FILTER (WHERE k.kennung IS NOT NULL),
                ARRAY[]::varchar[]
            ) AS kennungen
        FROM fristen_frist f
        LEFT JOIN statusmcp_kennzahl k ON k.bereich = 'frist' AND k.frist_id = f.id
        WHERE f.erledigt_am IS NULL
        GROUP BY f.id
    """,
    "sm_kassenbuch_monat": """
        SELECT
            date_trunc('month', b.datum)::date AS monat,
            o.kuerzel AS objekt,
            k.id AS kategorie_id,
            k.art AS kategorie_art,
            k.wohnraum,
            count(*) FILTER (WHERE b.storno_von_id IS NULL) AS buchungen,
            sum(CASE WHEN b.richtung = 'eingang' THEN b.betrag ELSE -b.betrag END) AS summe
        FROM kassenbuch_buchung b
        JOIN kern_objekt o ON o.id = b.objekt_id
        JOIN kassenbuch_kategorie k ON k.id = b.kategorie_id
        GROUP BY 1, 2, 3, 4, 5
    """,
    "sm_rueckstaende": """
        SELECT
            objekt, schluessel AS klasse, anzahl AS vertraege, betrag AS summe,
            datum AS datenstand_bank, stichtag, erstellt_am AS stand
        FROM statusmcp_kennzahl
        WHERE bereich = 'rueckstand'
    """,
    "sm_rueckstaende_einheiten": """
        SELECT
            objekt, kennung AS nw_id, tage AS alter_tage, schluessel AS klasse,
            stichtag, erstellt_am AS stand
        FROM statusmcp_kennzahl
        WHERE bereich = 'rueckstand_einheit'
    """,
    "sm_datenstand": """
        SELECT
            schluessel AS bereich, anzahl AS luecken, gesamt AS geprueft,
            stichtag, erstellt_am AS stand
        FROM statusmcp_kennzahl
        WHERE bereich = 'datenstand'
    """,
    "sm_datenstand_kennungen": """
        SELECT
            schluessel AS bereich, kennung, anzahl AS fehlende_felder,
            stichtag, erstellt_am AS stand
        FROM statusmcp_kennzahl
        WHERE bereich = 'datenstand_kennung'
    """,
    "sm_leerstand": """
        SELECT
            objekt, kennung AS nw_id, schluessel AS typ, datum AS frei_seit,
            tage AS tage_frei, stichtag, erstellt_am AS stand
        FROM statusmcp_kennzahl
        WHERE bereich = 'leerstand'
    """,
}


class Migration(migrations.Migration):
    dependencies = [
        ("statusmcp", "0001_initial"),
        ("kern", "0001_initial"),
        ("fristen", "0001_initial"),
        ("kassenbuch", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql=f"CREATE VIEW {name} AS {abfrage.strip()};",
            reverse_sql=f"DROP VIEW IF EXISTS {name};",
        )
        for name, abfrage in SICHTEN.items()
    ]
