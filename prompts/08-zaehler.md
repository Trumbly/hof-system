# Schritt 8 – Zähler

Lies `CLAUDE.md`, im Bauplan „Schritt 8“ und „Seite Datenstand“, in der Prozessliste H7.1, H7.2, O2. Leg mir zuerst deinen Plan vor und warte auf mein OK.

## Aufgaben

1. App `zaehler`: `Zaehler` (Nummer, Art Strom/Wasser/Gas/Wärme, Einheit oder Objekt, Einbaudatum, Eichfrist bis, ausgebaut am, Nachfolger), `Zaehlerstand` (Zähler, Datum, Stand, Foto, Art abgelesen/geschätzt/Einbau/Ausbau, erfasst bei Übergabe ja/nein).
2. Zählerwechsel mit beiden Ständen und Datum in einem Vorgang (H7.1).
3. Ablesung am Handy (O2): Liste der Zähler eines Objekts, je Zähler Stand und Foto; ein kleinerer Stand als der letzte wird abgelehnt (außer nach Wechsel); Schätzwerte tragen ein Kennzeichen.
4. Stichtag 31.12. als Konfiguration; Lauf `zaehler_stichtag` legt ab dem 15.12. einen Arbeitsposten „Ablesung fehlt“ je Zähler ohne Stand an.
5. Übergabe und Rückgabe aus Schritt 4 erfassen Zählerstände über dieselbe Funktion.
6. Datenstand erweitern: Bereich Zähler mit Pflichtfeldern Nummer, Art, Einbaudatum, Eichfrist und Stand zum letzten Stichtag mit Foto; dazu die Liste „für deinen nächsten Besuch“, sortiert nach Objekt, druckbar.
7. Import vorhandener Zähler und Stände mit `--probelauf`; Format beschreibe ich dir.
8. Testhof-Modul: 20 Zähler, Stände über zwei Jahre, ein Wechsel, ein fehlender Stichtagsstand.

## Fertig, wenn (Abnahme Schritt 8)

- Zähler lassen sich anlegen und wechseln.
- Ablesung am Handy speichert Stand, Datum und Foto; kleinerer Stand wird abgelehnt (Test).
- Zum Stichtag zeigt die Arbeitsliste fehlende Ablesungen (Test).
- Datenstand zeigt Zähler ohne Stand, ohne Foto oder ohne Eichfrist als Liste für den Besuch.

Am Ende: Branch `schritt-8-zaehler`, Pull Request, kurzer Bericht.
