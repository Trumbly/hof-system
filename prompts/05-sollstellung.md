# Schritt 5 – Sollstellung

Lies `CLAUDE.md`, im Bauplan „Schritt 5“, in der Prozessliste H3.4. Leg mir zuerst deinen Plan vor und warte auf mein OK.

## Aufgaben

1. App `soll`: `Sollposten` (Vertrag, Zeitraum von/bis, Art Miete/Pauschale/Kaution/Sonstiges, Betrag, Steuer, Fälligkeit, Status offen/teilweise/bezahlt/storniert).
2. Service `sollstellung_erzeugen(vertrag, bis_datum)`: idempotent, erzeugt fehlende Posten aus den Konditionen; monatlich, vierteljährlich, jährlich; anteilig bei Beginn oder Ende im Monat (taggenau nach Kalendertagen, Rundung auf Cent, Rest im letzten Teilposten); Staffeln über die Gültigkeit der Kondition.
3. Lauf `soll_monat` am Monatsersten und Signal bei jeder Vertragsänderung; eine Änderung rückwirkend storniert betroffene offene Posten und erzeugt neue.
4. Fälligkeit nach Vertrag, Standard dritter Werktag; Feiertage NRW.
5. Kündigung beendet das Soll zum Fristende (Test).
6. Vergleich mit dem Soll-Export aus HelloHousing: Befehl `soll_vergleich --export <datei>`, der je Vertrag Abweichungen zählt. Format beschreibe ich dir; du schreibst ihn gegen eine erfundene Beispieldatei.
7. Seite „Offene Posten“ je Vertrag.
8. Testhof-Modul: Sonderfälle „Vertragsbeginn und -ende mitten im Monat“, „Staffelmiete mit Stufe im Testzeitraum“, „Jahreszahler“.

## Fertig, wenn (Abnahme Schritt 5)

- Monatsmitte, Staffel und Jahreszahler ergeben im Testhof die erwarteten Posten (Tests mit festen Erwartungswerten).
- Kündigung beendet das Soll zum Fristende (Test).
- Der Vergleich läuft gegen die Beispieldatei; den Vergleich Juli bis Dezember 2026 mit Echtdaten mache ich.

Am Ende: Branch `schritt-5-sollstellung`, Pull Request, kurzer Bericht.
