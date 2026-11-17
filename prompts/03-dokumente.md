# Schritt 3 – Dokumente, Belege, Eingangsrechnungen, Versand

Lies `CLAUDE.md`, im Bauplan „Schritt 3“, in der Prozessliste H6.1, H6.2, H6.4, H6.8, H6.9, H8.1 bis H8.4, C2 und Standardablauf 6. Leg mir zuerst deinen Plan vor und warte auf mein OK. Diese Sitzung ist groß; teile sie in drei Pull Requests: (a) Ablage und Belege, (b) Eingangsrechnungen, (c) Vorlagen und Versand.

## (a) Ablage und Belege

1. App `dokumente`: `Dokument` (Datei, Art, Datum, Löschfrist), `Dokumentbezug` (zu Einheit, Partei, Vertrag, Buchung, Vorgang; generisch), `Beleg` (Dokument, Lieferant, Betrag, Objekt, Kostenart, Leistungsjahr, umlagefähiger Anteil, Buchung).
2. Dateien liegen im Medienordner auf dem Server, Name aus Art, Datum und ID; nie aus Personennamen.
3. Belegeingang: Lauf `belege_postfach` liest per IMAP ein eigenes Rechnungspostfach, legt jede PDF-Anlage als Dokument ab und prüft auf Dubletten (Hash).
4. Upload am Handy: Foto oder PDF, Felder von Hand (keine Texterkennung). Vorschlag für Objekt, Kostenart und Leistungsjahr aus früheren Belegen desselben Lieferanten.
5. Belegmappe je Objekt und Jahr als PDF mit Inhaltsverzeichnis; Summen müssen zum Kassenbuch passen (H6.9).
6. Mahnungen von Lieferanten und Ähnliches als eigene Dokumentart ohne Buchung (H6.8).

## (b) Eingangsrechnungen

1. `Eingangsrechnung` (Beleg, Betrag, Rechnungsnummer, Fälligkeit, Status offen/bezahlt/strittig, bezahlt durch Buchung).
2. Beim Bestätigen eines Belegs mit Fälligkeit entsteht die Eingangsrechnung, ein Arbeitsposten „Rechnung zahlen“ und eine Frist.
3. Drei Tage vor Fälligkeit eine Erinnerung; nach Fälligkeit wird der Posten als überfällig markiert.
4. Der Kassenbuch-Lauf erkennt die Abbuchung (Betrag, Gegen-IBAN oder Rechnungsnummer im Zweck), verbindet Buchung und Rechnung, schließt Posten und Frist und hängt den Beleg an die Buchung.
5. Anbindung an das Hof-Backlog in Notion kommt in Schritt 9; sieh hier eine Schnittstelle `aufgaben.melden(arbeitsposten)` vor, die vorerst nichts tut.

## (c) Vorlagen und Versand

1. `Vorlage` (Name, Art, Version, Text als HTML mit Platzhaltern `{{ partei.name }}` usw., Status Entwurf/freigegeben, freigegeben von, am). Nur freigegebene Vorlagen lassen sich scharf verwenden.
2. `Schreiben` (Vorlage und Version, Bezug, PDF, Versandweg Mail/Brief/Einwurf-Einschreiben/selbst, Status bereit/gesendet/zugestellt, Sendungsnummer, Einlieferungsdatum, Zustellnachweis).
3. PDF mit WeasyPrint, Briefkopf als eigene Vorlage, DIN-5008-Fensterposition für die Anschrift.
4. Versand Mail über SMTP von kontakt@ mit Kopie in „Gesendet“ (IMAP-Append).
5. Versand Brief über die REST-API von LetterXpress: Client als Klasse mit Fake für Tests; Einwurf-Einschreiben als Option; Sendungsnummer und Status später per Abfrage holen. Gegen die Testumgebung oder echte API nur, wenn ich es ausdrücklich freigebe.
6. Senden nur über die Aktion „Senden“ nach einer Vorschau des fertigen PDFs (Regel 8 in `CLAUDE.md`).
7. Bei einseitigen Erklärungen (Art in der Vorlage markiert) ist ein Zustellnachweis Pflicht, bevor das Schreiben als erledigt gilt.
8. Serienmitteilung (H8.4): Auswahl von Verträgen, eine Vorlage, ein Senden-Klick für alle, jedes Schreiben einzeln abgelegt.
9. Import der Vorlagen-Texte aus Notion: Befehl, der Markdown mit Platzhaltern übernimmt, Status Entwurf.

## Fertig, wenn (Abnahme Schritt 3)

- Rechnung aus dem Postfach liegt am selben Tag in der Ablage und hängt nach der Zahlung an ihrer Buchung (durchgehender Test für Standardablauf 6).
- Foto vom Handy wird Beleg mit allen Feldern.
- Kein Schreiben verlässt das System ohne Klick auf Senden (Test).
- Einseitige Erklärung lässt sich ohne Zustellnachweis nicht abschließen (Test).
- Belegmappe entsteht auf Knopfdruck, Summen stimmen.
- Eingangsrechnung erzeugt Arbeitsposten und Erinnerung; nach der Abbuchung schließt sie sich (Test).
- Brief über den LetterXpress-Fake als Einwurf-Einschreiben, Sendungsnummer hängt am Schreiben (Test); ein echter Testbrief folgt nach meiner Freigabe.
- Vorlagen enthalten nur Platzhalter; ein Test schlägt an, wenn eine Vorlage einen Namen aus dem Testhof wörtlich enthält.

Am Ende: drei Pull Requests, je ein kurzer Bericht.
