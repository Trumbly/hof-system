# Schritt 4 – Stammdaten und Datenstand

Lies `CLAUDE.md`, im Bauplan „Schritt 4“, „Was automatisch übernommen wird“ und „Seite Datenstand“, in der Prozessliste H1.1 bis H1.3, H1.5, H2.1, H2.3, H2.4, H2.8 bis H2.11, O1 sowie die Standardabläufe 1 und 4. Leg mir zuerst deinen Plan vor und warte auf mein OK.

## Aufgaben

1. App `stammdaten`: `Einheit` (NW-ID, Typ, Objekt, Fläche, Status mit Gültig-ab, Zielmiete mit Datum und Begründung, Vermarktung), `Partei` (Person oder Firma, Anschrift, Kontaktwege; Personenfelder), `Vertrag` (Vertragsart G/L/H/GA/A u. a., Einheiten, Beginn, Ende, Kündigungsfrist, Zahlweise, Mandant, Status Entwurf/aktiv/gekündigt/beendet), `Vertragspartei` (Rolle Mieter/Zahler/Bürge), `Kondition` (Miete, Pauschale, Steuersatz, gültig ab/bis; Staffeln als mehrere Zeilen), `Kaution` (Betrag, Eingang, Konto, Abrechnung), `Schluessel` (Art, Anzahl, bei wem), `Checkliste` und `Checkpunkt` je Phase der SOP, `Uebergabe` (Datum, Zählerstände, Fotos, Schlüssel, Unterschrift als Foto).
2. Wohnraum gehört nicht hierher. Einheiten vom Typ Wohnung existieren nur als Einheit mit NW-ID und Verweis „geführt in Verwaltio“, ohne Vertrag.
3. Vertragsdokument aus dem Vertragsstamm über eine Vorlage aus Schritt 3 (H2.3).
4. Übergabe und Rückgabe am Handy (O1): eine Seite in Schritten, Fotos direkt aus der Kamera; die Übergabe setzt den Vertrag aktiv, die Rückgabe gibt die Einheit frei und legt die Kautionsabrechnung als Arbeitsposten an.
5. Kündigung (H2.8): Eingangsdatum erfassen, Fristende rechnen nach Vertragsart, Bestätigung aus Vorlage, Rückgabetermin als Frist.
6. Leerstand (H2.11): Ansicht je Einheit mit Tagen frei und entgangenem Ertrag nach Zielmiete.
7. **Seite „Datenstand“**: Pflichtfelder je Art als Konfiguration (nicht im Code verstreut), Zählung der Lücken je Bereich, Liste je Lücke mit Link zur Eingabe. Bereiche und Regeln wie in der Tabelle im Bauplan. Die Zähler-Bereiche kommen in Schritt 8 dazu.
8. Importe mit `--probelauf`: Einheiten aus dem Einheiten-Verzeichnis (Notion-Export als CSV), Parteien und Verträge aus dem HelloHousing-Export. Die Formate beschreibe ich dir; du schreibst den Import gegen erfundene Beispieldateien in `tests/fixtures/`. Der Bericht nennt nur Zähler und NW-IDs.
9. Testhof-Modul: 60 Einheiten, 45 Verträge, 50 Parteien, Sonderfälle „Vertragsbeginn mitten im Monat“, „Staffelmiete“, „Kündigung, Rückgabe und Kautionsabrechnung“.
10. Durchgehende Tests für Standardablauf 1 und 4.

## Fertig, wenn (Abnahme Schritt 4)

- Neuer Vertrag entsteht als PDF; die Übergabe am Handy setzt ihn aktiv (Test Standardablauf 1).
- Kündigung erzeugt Fristende, Bestätigung und Rückgabetermin; Rückgabe gibt die Einheit frei und stößt die Kautionsabrechnung an (Test Standardablauf 4).
- Mietänderung gilt ab Datum, der alte Stand bleibt sichtbar (Test).
- Leerstand-Ansicht zeigt entgangenen Ertrag je Einheit.
- Datenstand zeigt fehlende Pflichtfelder und führt mit einem Klick zur Eingabe.
- Import im Probelauf ohne Änderung; den echten Import und die Prüfung jedes Vertrags gegen das PDF mache ich.

Am Ende: Branch `schritt-4-stammdaten`, Pull Request, kurzer Bericht.
