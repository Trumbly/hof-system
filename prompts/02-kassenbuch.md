# Schritt 2 – Kassenbuch

Lies `CLAUDE.md`, im Bauplan „Schritt 2: Kassenbuch“ und das Datenmodell, in der Prozessliste H6.3, H6.5, H6.6, H10.2, B9 und Standardablauf 7. Leg mir zuerst deinen Plan vor und warte auf mein OK.

## Aufgaben

1. App `kassenbuch`: `Kategorie` (Name, Art Einnahme/Ausgabe/Entnahme/Umbuchung, aktiv), `Buchung` (Umsatz, Betrag, Kategorie, Objekt, Mandant, Leistungsjahr, umlagefähiger Anteil, storniert durch), `Zuordnungsregel` (Bedingungen auf Gegenpartei, IBAN, Zweck, Betragsspanne; Ergebnis Kategorie, Objekt, Mandant; Priorität; ab Schritt 6 optional Vertrag), `Monatsabschluss` (Monat, Mandant, Saldo Bank, Saldo Buch, abgeschlossen am).
2. Ein Umsatz kann in mehrere Buchungen aufgeteilt werden; die Summe muss dem Umsatz entsprechen.
3. Lauf `kassenbuch_einordnen` nach jedem Bankabruf: wendet Regeln an; eindeutige Treffer werden gebucht, mehrdeutige oder fehlende ein Arbeitsposten „Umsätze einordnen“ mit Aktionen zuordnen, aufteilen, Regel anlegen, zurückstellen.
4. Regeln sind Daten in der Datenbank, nicht Code. Eine neue Regel kann rückwirkend auf offene Umsätze angewandt werden (mit Vorschau).
5. Monatsabschluss (H6.6): Lauf am 3. des Folgemonats; prüft, dass alle Umsätze gebucht sind und der Saldo stimmt, sonst Arbeitsposten. Nach dem Abschluss ist der Monat gesperrt; Änderungen nur noch durch Stornobuchung im offenen Monat.
6. Monatsbericht je Objekt (H10.2): Einnahmen, Kosten, Saldo, Vergleich zum Vorjahresmonat, als Seite und als PDF.
7. Entnahmen (B9) als eigene Kategorie je Mandant.
8. Import der Kategorien: Befehl `kassenbuch_kategorien_import` für eine Liste aus Name und Art. Das echte Format beschreibe ich dir; die Datei selbst liest du nicht.
9. Testhof-Modul: 35 Kategorien, 60 Regeln, Sonderfälle „Sammelrechnung für zwei Objekte“ und „Wohnraummiete auf demselben Konto“ (wird im Kassenbuch gebucht, aber nie im Abgleich).
10. Durchgehender Test für Standardablauf 7 unter `tests/ablaeufe/`.

## Fertig, wenn (Abnahme Schritt 2)

- Im Testhof hat nach dem Lauf jeder Umsatz Kategorie, Objekt und Mandant oder steht als Arbeitsposten.
- Ein abgeschlossener Monat lässt sich nicht mehr ändern; der Test zeigt die Stornobuchung als Weg.
- Der Monatsbericht entsteht nach dem Abschluss ohne Handarbeit.
- Entnahmen stehen getrennt je Mandant.
- Abschluss Januar bis Dezember 2026 und der Vergleich mit dem Finanz-Dashboard laufen danach bei mir.

Am Ende: Branch `schritt-2-kassenbuch`, Pull Request, kurzer Bericht.
