# Schritt 7 – Mahnwesen

Lies `CLAUDE.md`, im Bauplan „Schritt 7“, in der Prozessliste H4.1 bis H4.3, H4.6 und Standardablauf 3. Leg mir zuerst deinen Plan vor und warte auf mein OK.

Das Mahnwesen gilt nur für Verträge im Hof-System. Den Wohnraum mahnt Verwaltio.

## Aufgaben

1. App `mahnwesen`: `Mahnstufe` (Vertragsart, Reihenfolge, Auslöser in Tagen nach Fälligkeit oder nach voriger Stufe, Frist, Vorlage, Versandweg), `Mahnfall` (Vertrag, offene Posten, aktuelle Stufe, Stufe belegt am, nächste Stufe frühestens), `Mahnsperre` (Vertrag, Grund Rechtsfall/Klärfall/Ratenplan/Hand, bis).
2. Stufen nach SOP als Startdaten: Erinnerung 5 Tage nach Fälligkeit; Mahnung nach 14 Tagen mit 10 Tagen Frist; Abmahnung mit Kündigungsandrohung bei zweiter offener Monatsmiete; fristlose Kündigung, hilfsweise ordentlich, ab zwei Monatsmieten Rückstand. Als Daten, änderbar in der Oberfläche.
3. Lauf `mahnlauf` montags nach dem Abgleich: schreibt Fälle fort, legt je fälliger Stufe ein Schreiben im Status „bereit“ und einen Arbeitsposten „Mahnungen senden“ an. Keine Stufe, bevor die vorige belegt ist (gesendet, bei einseitigen Erklärungen zugestellt). Kein zweites Schreiben derselben Stufe binnen 14 Tagen.
4. Sperren: Ein offener Klärfall aus dem Abgleich oder ein Rechtsfall sperrt automatisch.
5. Abmahnung und Kündigung entstehen als Brief; jede Kündigung trägt den Hinweis „vor Versand zum Anwalt“, und ihr Senden-Knopf verlangt eine Bestätigung, dass der Anwalt sie gesehen hat.
6. Ordentliche Kündigung durch den Vermieter (H4.6): Fristrechnung je Vertragsart, Schreiben aus Vorlage.
7. Scharf wird eine Stufe erst mit einer freigegebenen Vorlage; sonst bleibt sie im Probebetrieb und erzeugt nur den Arbeitsposten.
8. Vergleich mit dem alten Mahnlauf: Befehl, der je Woche Fälle und Stufen als Zähler und NW-IDs ausgibt.
9. Testhof-Modul: Sonderfall „Rechtsfall mit Mahnsperre“, dazu Fälle für jede Stufe.
10. Durchgehender Test für Standardablauf 3.

## Fertig, wenn (Abnahme Schritt 7)

- Stufenfolge und Fristen stimmen im Testhof (Test je Stufe).
- Keine Stufe folgt ohne belegte Vorstufe (Test).
- Sperren greifen (Test).
- Kündigung hat Anwaltshinweis und Bestätigung vor dem Senden.
- Ohne freigegebene Vorlage bleibt die Stufe im Probebetrieb (Test).
- Den Vergleich über vier Wochen mit dem alten Mahnlauf mache ich.

Am Ende: Branch `schritt-7-mahnwesen`, Pull Request, kurzer Bericht.
