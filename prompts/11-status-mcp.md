# Schritt 11 – Status-MCP

Lies `CLAUDE.md`, im Bauplan „Schritt 11“ und die Regel zu Personenfeldern, in der Prozessliste H11.5 und C3. Leg mir zuerst deinen Plan vor und warte auf mein OK.

## Ziel

Claude bekommt im Betrieb nur noch Zähler, Summen, Fristen und Einheit-IDs. Dieser Dienst ist die einzige Verbindung zwischen Claude und dem Hof-System.

## Aufgaben

1. App `statusmcp`: ein MCP-Server (offizielles Python-SDK, Streamable HTTP), der hinter dem bestehenden hof-mcp-gateway läuft.
2. Er liest ausschließlich aus eigenen Datenbank-Sichten (`CREATE VIEW`), die keine registrierten Personenfelder enthalten. Ein eigener Datenbank-Benutzer mit Leserecht nur auf diese Sichten.
3. Werkzeuge, alle nur lesend:
   - `ueberblick`: Anzahl offener Arbeitsposten je Art, nächste Fristen (Datum, Art, NW-ID), Zustand der Läufe und der Sicherung
   - `rueckstaende`: Anzahl Verträge im Rückstand und Gesamtsumme, je Objekt; Liste der NW-IDs ohne Beträge je Vertrag
   - `datenstand`: Lücken je Bereich, NW-IDs mit Lücken
   - `leerstand`: freie Einheiten mit NW-ID, Typ, Tagen frei
   - `kassenbuch_monat`: Summen je Kategorie und Objekt für einen Monat
   - `fristen`: Fristen in einem Zeitraum mit Art und NW-ID
4. Ein Test ruft jedes Werkzeug gegen den Testhof auf und durchsucht jede Antwort nach allen Namen, IBANs, Adressen und Verwendungszwecken aus dem Testhof; ein Fund lässt den Test fehlschlagen.
5. Ein zweiter Test prüft, dass der Datenbank-Benutzer des MCP keine Tabelle außer den Sichten lesen kann.
6. Anleitung in `docs/betrieb.md`: wie der Gateway auf diesen Dienst zeigt und die alten MCPs (HelloHousing, hof-fints) nicht mehr.

## Fertig, wenn (Abnahme Schritt 11)

- Claude bekommt Zähler, Summen, Fristen und Einheit-IDs, sonst nichts.
- Der Suchtest schlägt bei einem absichtlich eingebauten Namen an und ist sonst grün.
- Die Abschaltung der alten MCPs und Zeitpläne mache ich, sobald ihr Dienst umgezogen ist.

Am Ende: Branch `schritt-11-status-mcp`, Pull Request, kurzer Bericht.
