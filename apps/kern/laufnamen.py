"""Anzeigenamen der Läufe (Design-Review nachher, Befund C).

Die Oberfläche (Kachel „Läufe“ im Cockpit, Seite „Läufe“) zeigt diese Namen statt der
Befehlsnamen. Der technische Name (``Lauf.name``, Name des Management-Befehls) bleibt in der
Lauf-Meldung, in den Logs, im Status-MCP und in Notion; dort ändert sich nichts.

Ein neuer Lauf (``LaufBefehl``) bekommt hier seinen Namen; ein Test prüft, dass keiner fehlt.
"""

from __future__ import annotations

#: Technischer Name des Laufs → Anzeigename. Kurz, damit er in einer Zeile der Kachel steht.
ANZEIGENAMEN: dict[str, str] = {
    "abgleich": "Zahlungsabgleich",
    "abgleich_regeln_import": "Zahlerregeln importieren",
    "arbeitsposten_pflege": "Arbeitsposten pflegen",
    "aufgaben_abgleich": "Hof-Backlog abgleichen",
    "bank_abruf": "Bankabruf",
    "bank_import": "Bankumsätze importieren",
    "belege_postfach": "Belege aus dem Postfach",
    "benachrichtigen": "Benachrichtigung",
    "fristen": "Fristen prüfen",
    "id_abgleich": "ID-Abgleich",
    "import_einheiten": "Einheiten importieren",
    "import_hellohousing": "HelloHousing importieren",
    "import_zaehler": "Zähler importieren",
    "kassenbuch_einordnen": "Kassenbuch einordnen",
    "kassenbuch_kategorien_import": "Kategorien importieren",
    "kassenbuch_monatsabschluss": "Monatsabschluss",
    "kern_selbsttest": "Selbsttest",
    "mahnlauf": "Mahnlauf",
    "mahnwesen_vergleich": "Mahnwesen vergleichen",
    "sicherung_pruefen": "Sicherung prüfen",
    "soll_monat": "Sollstellung",
    "soll_vergleich": "Soll vergleichen",
    "statusmcp_aktualisieren": "Status-MCP aktualisieren",
    "tageslauf": "Tageslauf",
    "versandstand_abrufen": "Versandstand abrufen",
    "verwaltio_kennzahlen": "Verwaltio-Kennzahlen",
    "vorlagen_import": "Vorlagen importieren",
    "zaehler_stichtag": "Zähler-Stichtag",
}


def anzeigename(name: str) -> str:
    """Anzeigename eines Laufs; ein unbekannter Name (etwa aus einem Test) bleibt, wie er
    ist."""
    return ANZEIGENAMEN.get(name, name)
