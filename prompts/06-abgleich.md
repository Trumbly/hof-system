# Schritt 6 – Abgleich

Lies `CLAUDE.md`, im Bauplan „Schritt 6“ und „Umzug und Parallelbetrieb“, in der Prozessliste H1.4, H3.5 bis H3.7, H3.11 und Standardablauf 2. Leg mir zuerst deinen Plan vor und warte auf mein OK.

Der Abgleich ist der kritische Pfad. Lieber ein Klärfall zu viel als eine falsche Buchung.

## Aufgaben

1. App `abgleich`: `Zuordnung` (Buchung, Sollposten, Betrag; mehrere je Buchung und je Posten), `Guthaben` (Vertrag, Betrag, aus Buchung, verrechnet mit).
2. Zahlerregeln: Erweiterung der `Zuordnungsregel` aus Schritt 2 um Vertrag oder Vertragsliste (Sammelzahler) und erwartete Beträge.
3. Lauf `abgleich` nach jedem Bankabruf, in dieser Reihenfolge:
   1. Regel mit Vertrag und Betrag passt genau auf älteste offene Posten → buchen.
   2. Sammelzahler: Summe passt auf die offenen Posten der Vertragsliste → aufteilen und buchen.
   3. Jahreszahler: Betrag passt auf die Posten eines Jahres → buchen.
   4. Alles andere → Arbeitsposten „Zahlungen klären“ mit Vorschlag und Konfidenz, nie automatisch gebucht.
4. Überzahlung wird Guthaben und mit dem nächsten Posten verrechnet; Teilzahlung lässt den Rest offen.
5. Klärfälle (H3.6) mit festen Aktionen: zuordnen, aufteilen, Regel anlegen, zurückstellen, als nicht vertragsbezogen markieren.
6. Handbuchungen (H3.7): Barzahlung, Zahlung auf fremdes Konto, Erstattung; Grund und Beleg Pflicht.
7. Wohnraum: Umsätze, die eine Regel als Wohnraum markiert, gehen nie in den Abgleich.
8. Ansicht „Rückstände“ (H3.11) je Vertrag und Summe, im Hof-Cockpit verlinkt.
9. Import der Mietregeln aus hof-fints: Befehl mit `--probelauf`. Das Format von `regeln.yaml` beschreibe ich dir; du liest die echte Datei nicht.
10. Rückrechnung: Befehl `abgleich_rueckrechnung --von 2026-07 --bis 2026-12`, der in einer Kopie rechnet und je Vertrag den Stand (bezahlt/offen/in Klärung) ausgibt, ohne Namen und ohne Beträge je Vertrag, nur Zähler und NW-IDs mit Abweichung.
11. Testhof-Modul: Sonderfälle Sammelzahler, fremder Zahler, falscher Zweck, Überzahlung, Barzahlung, Teilzahlung, Dauerauftrag mit alter Miete, Rückbuchung.
12. Durchgehender Test für Standardablauf 2.

## Fertig, wenn (Abnahme Schritt 6)

- Jeder Sonderfall wird richtig zugeordnet oder landet in „Zahlungen klären“ (je ein Test).
- Unsichere Treffer werden nie von selbst gebucht (Test mit absichtlich mehrdeutigem Fall).
- Die Rückstände sind jeden Morgen aktuell.
- Regelimport und Rückrechnung laufen im Probelauf; den Vergleich mit dem alten Mietabgleich mache ich.

Am Ende: Branch `schritt-6-abgleich`, Pull Request, kurzer Bericht.
