# Schritt 9 – Fristen, Hof-Cockpit, Überwachung, Aufgaben im Hof-Backlog

Lies `CLAUDE.md`, im Bauplan „Schritt 9“ und „Dienste und Oberfläche“ (Tageslauf), in der Prozessliste H9.1, H10.1, H11.1 und Standardablauf 10. Leg mir zuerst deinen Plan vor und warte auf mein OK.

## Aufgaben

1. App `fristen`: `Frist` (Datum, Art, Bezug generisch, Quelle Vertrag/Kaution/Bankfreigabe/Eingangsrechnung/Zähler/Hand, erledigt am). Bestehende Dienste legen ihre Fristen über einen Service an; eine Frist verschwindet erst mit Erledigt-Datum.
2. Lauf `fristen` täglich: fällige und überfällige Fristen werden Arbeitsposten.
3. Tageslauf `tageslauf` in der Reihenfolge aus dem Bauplan (Bank, Einordnen, Soll, Abgleich, Mahnwesen montags, Fristen, Verwaltio, Cockpit, Sicherung prüfen); bricht ein Teil ab, laufen die übrigen weiter und der Fehler steht im Lauf.
4. App `cockpit`: Startseite mit Kacheln für offene Freigaben je Art, Fristen der nächsten 14 Tage, Rückstände, Leerstand, Datenstand-Lücken, Zustand der Läufe und der letzten Sicherung. Liest direkt aus der Datenbank, keine Kopien.
5. Benachrichtigung (H11.1): Nur wenn ein Lauf gescheitert ist oder eine Freigabe wartet. Kanal Mail an mich und Push über ntfy (selbst gehostet oder ntfy.sh mit privatem Thema). Inhalt nur Zähler und Link, nie Namen oder Beträge.
6. Aufgaben im Hof-Backlog: `aufgaben.melden()` aus Schritt 3 jetzt umsetzen. Für jeden neuen Arbeitsposten der Arten „Mahnungen senden“, „Zahlungen klären“, „Rechnung zahlen“, „Frist fällig“ und „Ablesung fehlt“ legt das System eine Aufgabe in der Notion-Datenbank „Hof-Backlog“ an, mit Titel aus Art und Zähler (z. B. „3 Mahnungen senden“), NW-IDs, Fälligkeit und Link ins Hof-System; ohne Namen und Beträge. Erledigt sich der Posten, wird die Aufgabe auf Erledigt gesetzt. Notion-Token nur auf dem Server; im Test ein Fake. Die Datenbank-ID und die Feldnamen gebe ich dir.
7. Durchgehender Test für Standardablauf 10.

## Fertig, wenn (Abnahme Schritt 9)

- Die Startseite zeigt alles direkt aus der Datenbank.
- Gescheiterter Lauf oder wartende Freigabe meldet sich mit Zähler und Link, sonst bleibt es still (Test).
- Jede Frist nennt ihre Quelle und verschwindet erst mit Erledigt-Datum (Test).
- Aufgaben im Hof-Backlog entstehen und schließen sich; ein Test prüft, dass keine Personenfelder in Titel oder Text landen.

Am Ende: Branch `schritt-9-fristen-cockpit`, Pull Request, kurzer Bericht.
