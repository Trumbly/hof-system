# Bauplan Hof-System

Nov 17, 2026 · @Maximilian Noelle-Wying

## Kurzfassung

Das Hof-System entsteht in zwölf Schritten mit rund 57 Arbeitstagen. Bei drei Tagen pro Woche zieht die erste Vertragsart zum 01.05.2027 um.

- **Was gebaut wird:** eine Django-Anwendung mit Postgres auf einem eigenen Hetzner-Server. Zwölf Dienste auf einem Datenmodell mit rund 40 Tabellen tragen die 57 Abläufe der Stufe v1.

- **Reihenfolge:** Grundgerüst, Bank, Kassenbuch, Dokumente, Stammdaten, Sollstellung, Abgleich, Mahnwesen, Zähler, Fristen und Cockpit, Anbindungen, Status-MCP. Jeder Schritt hat Abnahmekriterien zum Abhaken, zusammen 51.

- **Termine:** Start am 23.11., v1 gebaut am 23.04.2027, Umzüge zum 01.05. und 01.06. Das Ziel 01.04. aus dem Setup-Review hält nur mit vier Tagen pro Woche bis Anfang Februar.

- **Abweichung:** Django statt des angekündigten FastAPI, weil Anmeldung, Verwaltungsseiten und Historie dort fertig sind.

- **KI-Grenze:** Gebaut wird mit einem erfundenen Testhof. Echtdaten bleiben auf dem Server; Berichte nennen nur Zähler und Einheit-IDs.

- **Was du abnimmst:** sechs Punkte im Abschnitt Abnahme. Dazu kommen sechs Antworten von dir, Dreses und Herrn Bucks, jede vor ihrem Bauschritt.

## Rahmen

Deine Entscheidungen vom 16. und 17.11. legen fest, was das Hof-System tut. Für den Bau zählen neun davon.

| Vorgabe                                                         | Quelle | Folge für den Bau                                                                                        |
|-----------------------------------------------------------------|--------|----------------------------------------------------------------------------------------------------------|
| Wohnraum liegt ganz in Verwaltio, samt Mahnung und Mieterhöhung | E1, P1 | Kein Mietkonto für Wohnraum. Das Hof-System liest von dort nur Kennzahlen.                               |
| Alles andere führt das Hof-System                               | E1, E5 | Verträge, Sollstellung, Abgleich und Mahnwesen für Garagen, Stellplätze, Lager, Hallen und Außenflächen. |
| Die KI sieht nur Einheit-IDs                                    | E3     | Entwicklung mit Testdaten, kein Zugang zur Produktion, Vorlagen mit Platzhaltern.                        |
| Das alte Setup läuft, bis das neue reif ist                     | E4     | Jeder Dienst läuft zwei Monate parallel ohne Abweichung, bevor der alte Lauf endet.                      |
| Ein System, eine Oberfläche, ein Datenmodell                    | E6     | Eine Anwendung mit Modulen und einer Datenbank, keine verteilten Dienste.                                |
| Eigener Server; das VR-Konto endet im ersten Quartal 2027       | E7     | Vivid läuft auf dem Server ein. Der VR-Abruf bleibt bis zum Ende auf dem hof-mac.                        |
| Nur du arbeitest damit, auch vor Ort                            | E9, P4 | Eine Rolle, schlichte Seiten, die am Handy funktionieren.                                                |
| Versand nur über einen eigenen Senden-Knopf                     | P2     | Jede Freigabe zeigt das fertige Schreiben. Ohne Klick geht nichts hinaus.                                |
| NKA 2025 und Korrekturpaket gehen vor                           | E10    | Der Bau tritt bis zum 31.12. hinter beide Aufgaben zurück.                                               |

Nicht in v1: Wohnraum-Mietkonto, Umlage nach Schlüssel, Mieterportal, Texterkennung, Zutritt, Lastschrift, Stripe und Pacht. Pacht, Verbrauchsabrechnung und Flurstücke folgen in v2.

## Technik und Betrieb

Das Hof-System wird eine Django-Anwendung mit Postgres auf einem eigenen Hetzner-Server, erreichbar nur über Tailscale.

| Baustein           | Wahl                                                                                                                                   | Grund                                                                                                                  |
|--------------------|----------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------|
| Sprache und Rahmen | Python mit Django                                                                                                                      | Anmeldung, Verwaltungsseiten, Formulare und Migrationen sind fertig. hof-fints zieht als Modul ein.                    |
| Datenbank          | Postgres, in v2 mit PostGIS                                                                                                            | Ein Datenmodell für alle Dienste (E6). Flurstücke kommen später ohne Umbau dazu.                                       |
| Oberfläche         | Seiten vom Server mit HTMX                                                                                                             | Kein eigenes Frontend-Projekt. Die Seiten laufen am Handy.                                                             |
| Läufe              | Befehle der Anwendung, gestartet nach Zeitplan auf dem Server                                                                          | Jeder Lauf ist einzeln testbar und schreibt sein Ergebnis ins Laufprotokoll.                                           |
| Historie           | Trigger in der Datenbank (django-pghistory)                                                                                            | Jede Änderung wird festgehalten, auch aus Läufen. Daraus entsteht die Rücknahme (H11.6).                               |
| Anmeldung          | Passwort und zweiter Faktor (django-allauth), Zugang nur über Tailscale                                                                | Zwei Hürden, kein Login im offenen Netz.                                                                               |
| Dokumente          | Dateien auf dem Server, PDF aus HTML-Vorlagen (WeasyPrint)                                                                             | Eine Ablage (E1). Vorlagen bleiben Text mit Platzhaltern.                                                              |
| Versand            | Mails über SMTP des Postfachs kontakt@, Kopie in „Gesendet“; Briefe über die API von LetterXpress, auf Wunsch als Einwurf-Einschreiben | Absender und Postfach bleiben wie heute. Briefe druckt, kuvertiert und frankiert LetterXpress (entschieden am 17.11.). |
| Bank               | Vivid über Enable Banking auf dem Server, VR über FinTS auf dem hof-mac                                                                | Die FinTS-PIN kommt nicht auf den Server. Der hof-mac schiebt die Umsätze hoch, bis das Konto endet.                   |
| Server             | Hetzner Cloud mit Docker Compose: Anwendung, Postgres, Caddy                                                                           | Getrennt von der Website (E7). Ein Befehl rollt aus.                                                                   |
| Sicherung          | täglich Datenbank und Dokumente, verschlüsselt mit restic in eine Hetzner Storage Box                                                  | Zweiter Ort (E7). Die Rückspielprobe läuft, bevor Echtdaten einziehen (H11.2).                                         |
| Code               | privates Repository bei GitHub, Tests mit Testdaten bei jeder Änderung                                                                 | Hier arbeitet Claude Code, sonst nirgends (C1).                                                                        |

**Abweichung von meiner Ankündigung:** Ich hatte FastAPI genannt. Beim Ausarbeiten spricht mehr für Django: Anmeldung, Verwaltungsseiten für Stammdaten und Änderungshistorie müssten wir mit FastAPI selbst bauen und pflegen. Datenmodell und Bauschritte bleiben bei beiden gleich; mit FastAPI dauern Schritt 0 und Schritt 4 länger.

**Drei Umgebungen:** dein Rechner mit Claude Code, die Pipeline und die Produktion. Echtdaten gibt es nur in der Produktion. Die beiden anderen arbeiten mit dem Testhof.

**KI-Grenze im Bau:** Claude Code sieht Code und Testdaten. Den Server erreichst nur du, und nur du rollst aus. Fehlermeldungen und Importberichte nennen IDs und Zähler, keine Namen, Beträge oder Verwendungszwecke.

## Datenmodell

Das Modell hat rund 40 Tabellen in elf Bereichen. Sein Kern ist eine Kette: Vertrag, Sollposten, Zuordnung, Buchung, Bankumsatz.

Sechs Regeln gelten für alle Tabellen:

- **Eine ID je Einheit.** Die NW-ID entsteht im Hof-System und steht als Fremdschlüssel in Website und Verwaltio (H1.6).

- **Zeit statt Überschreiben.** Was sich ändert, bekommt Gültig-ab und Gültig-bis: Miete, Zahlweise, Zielmiete, Status. Alte Stände bleiben lesbar.

- **Stornieren statt löschen.** Gebuchtes wird nie überschrieben. Jede Änderung steht im Protokoll und lässt sich zurücknehmen (H11.6).

- **Mandant an jeder Buchung.** GbR, Nachlass, KG-Linie oder privat (H6.5). Objekte und Verträge tragen ihn ebenfalls.

- **Ein Weg für alle.** Läufe schreiben über dieselben Funktionen wie die Oberfläche. Unsicheres wird ein Arbeitsposten, keine Buchung.

- **Personenfelder sind markiert.** Der Status-MCP und alle Berichte für die Entwicklung lesen nur Sichten ohne diese Felder.

| Bereich      | Tabellen                                                                                                    | Kern                                                                                                                                                                                                  | Ab Schritt |
|--------------|-------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|------------|
| Grundgerüst  | benutzer, protokoll, lauf, arbeitsposten, mandant, objekt                                                   | Wer hat was geändert, was tat jeder Lauf, was wartet auf dich                                                                                                                                         | 0          |
| Bank         | bankkonto, bankfreigabe, bankabruf, bankumsatz                                                              | Umsatz mit Betrag, Datum, Gegenpartei, Zweck, Rohdaten und Fingerabdruck gegen Doppelte                                                                                                               | 1          |
| Kassenbuch   | kategorie, buchung, zuordnungsregel, monatsabschluss                                                        | Ein Umsatz wird zu einer oder mehreren Buchungen mit Kategorie, Objekt, Leistungsjahr und umlagefähigem Anteil                                                                                        | 2          |
| Dokumente    | dokument, dokumentbezug, beleg, eingangsrechnung, vorlage, schreiben                                        | Datei mit Art, Datum und Löschfrist; Bezug zu Einheit, Partei, Vertrag oder Buchung; Eingangsrechnung mit Betrag, Fälligkeit und Status; Schreiben mit Vorlagenversion, Versandweg und Sendungsnummer | 3          |
| Stammdaten   | einheit, partei, vertrag, vertragspartei, kondition, kaution, schluessel, checkliste, checkpunkt, uebergabe | Vertrag mit Art, Beginn, Ende, Kündigungsfrist und Zahlweise; Kondition ist Miete, Pauschale und Steuersatz mit Gültig-ab, auch für Staffeln                                                          | 4          |
| Sollstellung | sollposten                                                                                                  | Forderung je Vertrag und Zeitraum mit Betrag, Fälligkeit, Art und Status                                                                                                                              | 5          |
| Abgleich     | zuordnung                                                                                                   | Verbindet Buchung und Sollposten, ganz oder in Teilen; ein Rest bleibt als Guthaben stehen                                                                                                            | 6          |
| Mahnwesen    | mahnstufe, mahnfall, mahnsperre                                                                             | Stufenfolge je Vertragsart; Fall je Vertrag mit belegter Stufe, Frist und Schreiben                                                                                                                   | 7          |
| Zähler       | zaehler, zaehlerstand                                                                                       | Zähler an Einheit oder Objekt; Stand mit Datum und Foto                                                                                                                                               | 8          |
| Fristen      | frist                                                                                                       | Datum, Art, Bezug und Quelle: Vertrag, Kaution, Bankfreigabe, Eingangsrechnung                                                                                                                        | 9          |
| Anbindungen  | buchungsanfrage, kennzahl, abweichung                                                                       | Buchung der Website an der Einheit; Kennzahlen aus Verwaltio je Tag; Abweichungen bei den IDs                                                                                                         | 10         |

Die Zuordnungsregel dient zweimal: Ab Schritt 2 ordnet sie Umsätze nach Gegenpartei in Kategorie, Objekt und Mandant ein. Ab Schritt 6 nennt sie zusätzlich den Vertrag, für den jemand zahlt (H1.4).

## Dienste und Oberfläche

Zwölf Bausteine teilen sich eine Oberfläche. Jeder Dienst besteht aus Tabellen, höchstens zwei Läufen, einer Arbeitsliste und wenigen Seiten.

| Dienst       | Läufe                                                        | Arbeitsliste                                         | Seiten                                                                          |
|--------------|--------------------------------------------------------------|------------------------------------------------------|---------------------------------------------------------------------------------|
| Bank         | Abruf täglich; Prüfung der Freigaben                         | Abruf gescheitert, Freigabe läuft ab                 | Konten, Umsätze, Import                                                         |
| Kassenbuch   | Einordnen nach jedem Abruf; Monatsabschluss am Monatsanfang  | Umsätze einordnen                                    | Kassenbuch, Regeln, Monatsbericht                                               |
| Dokumente    | Belegeingang aus dem Rechnungspostfach; Fälligkeiten täglich | Belege zuordnen, Rechnungen zahlen, Schreiben senden | Ablage, Vorlagen, Belegmappe, Beleg-Upload am Handy                             |
| Stammdaten   | keiner                                                       | offene Checklisten, fehlende Pflichtfelder           | Einheiten, Parteien, Verträge, Übergabe am Handy, Datenstand                    |
| Sollstellung | am Monatsersten und bei jeder Vertragsänderung               | keine                                                | offene Posten je Vertrag                                                        |
| Abgleich     | nach jedem Abruf                                             | Zahlungen klären                                     | Mietkonto je Vertrag, Rückstände                                                |
| Mahnwesen    | montags nach dem Abgleich                                    | Mahnungen senden                                     | Mahnfälle, Stufen, Sperren                                                      |
| Zähler       | Erinnerung zum Stichtag                                      | Ablesung fehlt                                       | Zähler, Ablesung am Handy                                                       |
| Fristen      | täglich                                                      | Fristen fällig                                       | Fristenliste                                                                    |
| Anbindungen  | Website bei jeder Buchung; Verwaltio nachts                  | Abweichungen bei den IDs                             | Buchungsanfragen, Kennzahlen Wohnraum                                           |
| Hof-Cockpit  | Tageslauf nach allen anderen                                 | zeigt alle Listen                                    | Startseite mit Freigaben, Fristen, Rückständen, Leerstand und Zustand der Läufe |
| Status-MCP   | keiner                                                       | keine                                                | keine; liefert Zähler, Summen, Fristen und Einheit-IDs                          |

Jeder Lauf sortiert in drei Körbe: gebucht, Arbeitsposten, Fehler. Ein Arbeitsposten hat feste Aktionen, etwa zuordnen, Regel anlegen, zurückstellen oder senden. Was nach außen geht, zeigt vorher das fertige Schreiben.

Der Tageslauf hält diese Reihenfolge ein:

1.  Bank abrufen

2.  Umsätze einordnen

3.  Sollstellung prüfen

4.  Zahlungen abgleichen

5.  Mahnfälle fortschreiben, montags

6.  Fristen prüfen

7.  Kennzahlen aus Verwaltio holen

8.  Hof-Cockpit aktualisieren

9.  Sichern

## Bauschritte

v1 entsteht in zwölf Schritten mit zusammen rund 57 Arbeitstagen. Ein Schritt ist fertig, wenn alle seine Kästchen angehakt sind.

Die Tage sind deine Zeit für Prüfen, Testen und Entscheiden; den Code schreibt Claude Code. Reihenfolge und Abläufe stammen aus der Prozessliste.

### Schritt 0: Grundgerüst

H11.2 bis H11.4, H11.6, C1 · 4 Tage · löst nichts ab

Baut: Repository mit Pipeline und Testhof, Server mit Datenbank, Anmeldung, Protokoll mit Rücknahme, Laufprotokoll, Arbeitslisten, Sicherung, Notfallmappe.

- [ ] Du meldest dich über Tailscale mit zweitem Faktor an, am Mac und am Handy.

- [ ] Eine Testbuchung steht im Protokoll und lässt sich zurücknehmen.

- [ ] Die Sicherung wurde einmal vollständig zurückgespielt.

- [ ] Die Pipeline prüft jede Änderung mit dem Testhof; im Repository liegen keine Echtdaten.

- [ ] Notfallmappe und zwei Seiten Betriebshandbuch liegen vor.

### Schritt 1: Bank

H3.1 bis H3.3 · 4 Tage · löst den Abruf über hof-fints ab; hof-fints liefert danach nur noch das VR-Konto zu

Baut: Konten, täglicher Abruf von Vivid, Zulieferung des VR-Kontos vom hof-mac, Import alter Umsätze mit Vorschau, Fristen der Bankfreigaben.

- [ ] Beide Konten laufen 14 Tage ohne Lücke und ohne Doppelte ein.

- [ ] Der Saldo je Konto stimmt täglich mit der Bank.

- [ ] Die Umsätze ab dem 01.01.2026 sind importiert.

- [ ] 14 Tage vor Ablauf einer Bankfreigabe erscheint ein Arbeitsposten.

### Schritt 2: Kassenbuch

H6.3, H6.5, H6.6, H10.2, B9 · 6 Tage · löst das Finanz-Dashboard als Schnappschuss ab

Baut: Kategorien, Objekte, Mandanten, Regeln je Gegenpartei, Aufteilen eines Umsatzes, Arbeitsliste „Umsätze einordnen“, Monatsabschluss mit Sperre, Monatsbericht je Objekt.

- [ ] Jeder Umsatz aus 2026 hat Kategorie, Objekt und Mandant.

- [ ] Die Monate Januar bis Dezember 2026 sind abgeschlossen; jeder Saldo stimmt mit der Bank (Standardablauf 7).

- [ ] Entnahmen stehen in einer eigenen Kategorie je Mandant.

- [ ] Der Monatsbericht je Objekt entsteht nach jedem Abschluss ohne Handarbeit.

- [ ] Abweichungen zum Finanz-Dashboard vom 08.11. sind erklärt.

### Schritt 3: Dokumente und Belege

H6.1, H6.2, H6.4, H6.8, H6.9, H8.1 bis H8.4, C2 · 8 Tage · löst Belegliste und dreifache Belegpflege ab

Baut: Ablage mit Bezügen, Belegeingang aus dem Rechnungspostfach, Upload am Handy, Fälligkeiten der Eingangsrechnungen mit Aufgabe und Erinnerung, Vorlagen mit Platzhaltern und Version, PDF-Erzeugung, Versand mit Senden-Knopf als Mail oder als Brief über LetterXpress, Zustellnachweis, Serienmitteilung, Belegmappe, Werte für die NKA.

- [ ] Eine Rechnung aus dem Rechnungspostfach liegt am selben Tag in der Ablage und hängt nach der Zahlung an ihrer Buchung (Standardablauf 6).

- [ ] Ein Foto vom Handy wird zum Beleg mit Objekt, Kostenart, Leistungsjahr und umlagefähigem Anteil.

- [ ] Aus einer freigegebenen Vorlage entsteht ein PDF. Es geht erst nach deinem Klick auf Senden hinaus, mit Kopie im Postfach.

- [ ] Eine einseitige Erklärung lässt sich ohne Zustellnachweis nicht abschließen.

- [ ] Die Belegmappe für ein Objekt und ein Jahr entsteht auf Knopfdruck; ihre Summen stimmen mit dem Kassenbuch.

- [ ] Vorlagen von Claude enthalten nur Platzhalter; Namen, Adressen und Beträge setzt das System ein.

- [ ] Eine Eingangsrechnung erscheint mit Lieferant und Fälligkeit als Aufgabe im Hof-Backlog. Drei Tage vor Fälligkeit kommt eine Erinnerung; nach der Abbuchung schließt das System die Aufgabe und hängt den Beleg an die Buchung im Kassenbuch.

- [ ] Ein Brief geht über die Testumgebung von LetterXpress als Einwurf-Einschreiben hinaus; Sendungsnummer und Einlieferungsdatum hängen am Schreiben.

### Schritt 4: Stammdaten

H1.1 bis H1.3, H1.5, H2.1, H2.3, H2.4, H2.8 bis H2.11, O1 · 7 Tage · löst Einheiten-Verzeichnis und Mieterverzeichnis in Notion ab

Baut: Einheiten, Parteien, Verträge mit Konditionen, Kaution, Schlüssel, Checklisten je Phase, Übergabe und Rückgabe am Handy, Kündigung mit Fristende, Leerstand.

- [ ] Alle Einheiten außerhalb von Wohnraum und alle laufenden Verträge sind importiert. Du hast jeden Vertrag einmal gegen das PDF geprüft.

- [ ] Ein neuer Vertrag entsteht aus dem Vertragsstamm als PDF; die Übergabe am Handy setzt ihn aktiv (Standardablauf 1).

- [ ] Eine Kündigung erzeugt Fristende, Bestätigung und Rückgabetermin. Die Rückgabe gibt die Einheit frei und stößt die Kautionsabrechnung an (Standardablauf 4).

- [ ] Jede Änderung einer Miete gilt ab einem Datum; der alte Stand bleibt sichtbar.

- [ ] Eine Ansicht zeigt Leerstand und entgangenen Ertrag je Einheit.

- [ ] Die Seite Datenstand zeigt je Einheit, Partei, Vertrag, Kaution und Schlüssel die fehlenden Pflichtfelder und führt mit einem Klick zur Eingabe.

### Schritt 5: Sollstellung

H3.4 · 3 Tage · löst den Soll-Export aus HelloHousing ab

Baut: Sollposten aus den Verträgen, monatlich, jährlich und anteilig, auch für Staffeln.

- [ ] Die Sollstellung für Juli bis Dezember 2026 stimmt je Vertrag mit dem Soll-Export aus HelloHousing überein; jede Abweichung ist erklärt.

- [ ] Vertragsbeginn zur Monatsmitte, Staffel und Jahreszahler ergeben im Testhof die erwarteten Posten.

- [ ] Eine Kündigung beendet das Soll zum Fristende.

### Schritt 6: Abgleich

H1.4, H3.5 bis H3.7, H3.11 · 6 Tage · löst den Zeitplan Mietabgleich ab

Baut: Zahlerregeln, Zuordnung von Buchung und Sollposten, Arbeitsliste „Zahlungen klären“, Handbuchungen, Guthaben, Ansicht der Rückstände.

- [ ] Die Rückrechnung für Juli bis Dezember 2026 kommt je Vertrag auf denselben Stand wie der alte Mietabgleich; jede Abweichung ist erklärt.

- [ ] Sammelzahler, Jahreszahler, Teilzahlung, Überzahlung und fremder Zahler werden im Testhof richtig zugeordnet.

- [ ] Unsichere Treffer landen in „Zahlungen klären“ und werden nie von selbst gebucht.

- [ ] Die Mietregeln aus hof-fints sind übernommen und liegen nur noch in der Datenbank.

- [ ] Die Ansicht der Rückstände ist jeden Morgen aktuell (Standardablauf 2).

### Schritt 7: Mahnwesen

H4.1 bis H4.3, H4.6 · 5 Tage · löst den Zeitplan Mahnlauf ab

Baut: Stufenfolge mit Fristen und Sperren, Mahnfälle, Schreiben je Stufe, Kündigung durch den Vermieter mit Fristrechnung.

- [ ] Der Lauf schlägt vier Wochen lang dieselben Fälle und Stufen vor wie der alte Mahnlauf (Standardablauf 3).

- [ ] Erinnerung nach 5 Tagen, Mahnung nach 14 Tagen mit zehn Tagen Frist. Keine Stufe folgt, bevor die vorige belegt ist.

- [ ] Ein Rechtsfall oder ein offener Klärfall sperrt die Mahnung.

- [ ] Abmahnung und Kündigung entstehen als Brief. Jede Kündigung trägt den Hinweis, dass sie vor dem Versand zum Anwalt geht.

- [ ] Scharf wird eine Stufe erst mit einer Vorlage, die der Anwalt freigegeben hat.

### Schritt 8: Zähler

H7.1, H7.2, O2 · 3 Tage · löst Nachrichten und Schätzungen ab

Baut: Zähler je Einheit, Wechsel mit beiden Ständen, Ablesung am Handy mit Foto, Kennzeichen für Schätzwerte.

- [ ] Jeder Zähler außerhalb der Wohnhäuser ist angelegt.

- [ ] Die Ablesung am Handy speichert Stand, Datum und Foto. Ein kleinerer Stand als der letzte wird abgelehnt.

- [ ] Zum Stichtag zeigt die Arbeitsliste, welche Ablesung fehlt.

- [ ] Der Datenstand zeigt Zähler ohne Stand zum Stichtag, ohne Foto oder ohne Eichfrist, als Liste für deinen nächsten Besuch.

### Schritt 9: Fristen und Hof-Cockpit

H9.1, H10.1, H11.1 · 4 Tage · löst System-Check und Hof-Tab des Cockpits ab

Baut: Fristen aus Verträgen, Kautionen und Bankfreigaben, Startseite, Überwachung der Läufe mit Benachrichtigung.

- [ ] Die Startseite zeigt Freigaben, Fristen, Rückstände, Leerstand und den Zustand der Läufe direkt aus der Datenbank.

- [ ] Ein gescheiterter Lauf oder eine wartende Freigabe meldet sich bei dir mit Zähler und Link. Sonst bleibt es still (Standardablauf 10).

- [ ] Jede Frist nennt ihre Quelle und verschwindet erst mit einem Erledigt-Datum.

### Schritt 10: Anbindung von Website und Verwaltio

H1.6, W1, W10, V12 · 5 Tage · löst den Nachtlauf ab

Baut: Schnittstelle für Katalog und Belegung, Ereignisse Buchung und Kündigung von der Website, nächtliche Kennzahlen aus Verwaltio, Abgleich der Einheit-IDs.

- [ ] Die Website zeigt Belegung und Preise aus dem Hof-System. Eine Rückgabe macht die Einheit ohne Handgriff wieder buchbar.

- [ ] Eine Buchung auf der Website erscheint als Anfrage an der Einheit; den Vertrag legst du in v1 von Hand an.

- [ ] Das Hof-Cockpit zeigt für Wohnraum offene Posten, Mahnfälle, Stand der NKA und Fristen, ohne Namen.

- [ ] Jede Einheit-ID, die in Website oder Verwaltio fehlt oder abweicht, steht in der Arbeitsliste.

### Schritt 11: Status-MCP

H11.5, C3 · 2 Tage · löst den direkten Zugriff von Claude ab

Baut: MCP-Dienst hinter dem Gateway, der nur Sichten ohne Personenfelder liest.

- [ ] Claude bekommt Zähler, Summen, Fristen und Einheit-IDs, sonst nichts.

- [ ] Ein Test durchsucht jede Antwort nach Namen, Beträgen je Person und Verwendungszwecken und schlägt bei einem Fund fehl.

- [ ] HelloHousing-MCP, hof-fints-MCP und die Zeitpläne mit Mieterdaten sind abgeschaltet, sobald ihr Dienst umgezogen ist.

## Testdaten

Entwickelt und getestet wird mit einem erfundenen Hof, dem Testhof. Echtdaten verlassen den Server nie, auch nicht anonymisiert.

Der Testhof hat die Größenordnung des echten: 3 Objekte, 60 Einheiten, 45 Verträge, 50 Parteien mit erfundenen Namen, 2 Bankkonten, 24 Monate mit rund 1.800 Umsätzen, 35 Kategorien, 60 Regeln, 20 Zähler. Ein Generator im Repository erzeugt ihn bei jedem Testlauf neu, mit festem Startwert.

Diese Sonderfälle sind fest eingebaut:

| Fall                                                            | Prüft Schritt |
|-----------------------------------------------------------------|---------------|
| Doppelter Umsatz nach erneutem Abruf                            | 1             |
| Rückbuchung einer Zahlung                                       | 2, 6          |
| Sammelrechnung für zwei Objekte                                 | 2, 3          |
| Wohnraummiete auf demselben Konto: Kassenbuch ja, Abgleich nein | 2, 6          |
| Vertragsbeginn und Vertragsende mitten im Monat                 | 5             |
| Staffelmiete mit einer Stufe im Testzeitraum                    | 5             |
| Jahreszahler: zwölf Monate in einer Zahlung                     | 5, 6          |
| Sammelzahler: eine Überweisung für drei Verträge                | 6             |
| Fremder Zahler: Partner oder Firma überweist                    | 6             |
| Verwendungszweck nennt die falsche Einheit                      | 6             |
| Überzahlung mit Guthaben für den Folgemonat                     | 6             |
| Barzahlung ohne Bankumsatz                                      | 6             |
| Teilzahlung mit späterer Nachzahlung                            | 6, 7          |
| Dauerauftrag mit alter Miete nach einer Erhöhung                | 6, 7          |
| Rechtsfall mit Mahnsperre                                       | 7             |
| Kündigung, Rückgabe und Kautionsabrechnung                      | 4             |

Sieben Standardabläufe der Prozessliste laufen als durchgehende Tests: 1, 2, 3, 4, 6, 7 und 10. Jeder Test spielt den Ablauf mit dem Testhof durch und prüft das Ergebnis, das die Prozessliste nennt. Die Abläufe 5 und 9 gehören zu v2, Ablauf 8 zu Verwaltio.

Was nur Echtdaten zeigen, prüfst du auf dem Server. Jeder Import hat einen Probelauf; sein Bericht nennt nur Zähler und Abweichungen je Einheit-ID. Diesen Bericht kannst du mit Claude teilen.

## Umzug und Parallelbetrieb

Jeder Dienst zieht in vier Stufen um. Der alte Lauf endet erst nach zwei Monaten ohne Abweichung (E4).

1.  **Import** mit Probelauf: Der Bericht nennt Zähler und Abweichungen je Einheit-ID.

2.  **Rückrechnung:** Der neue Dienst rechnet Juli bis Dezember 2026 nach. Das Ergebnis muss zum alten Stand passen.

3.  **Parallelbetrieb:** Zwei Monate laufen alt und neu nebeneinander. Geführt wird im alten Setup; jede Abweichung wird erklärt.

4.  **Stichtag** am Monatsersten: Ab dann führt das Hof-System. Offene Posten und Guthaben gehen als Vortrag mit; in HelloHousing endet das Soll dieser Verträge.

| Dienst                    | Vergleich im Parallelbetrieb                             | Alter Lauf endet                                                                                   |
|---------------------------|----------------------------------------------------------|----------------------------------------------------------------------------------------------------|
| Bank                      | Anzahl und Saldo je Konto und Tag                        | Abruf über hof-fints Ende Februar. Der Abruf für HelloHousing bleibt, solange es Mieten führt.     |
| Kassenbuch                | Summen je Kategorie und Monat gegen das Finanz-Dashboard | Dashboard-Datei mit dem Abschluss für Dezember                                                     |
| Sollstellung und Abgleich | Stand je Vertrag: bezahlt, offen, in Klärung             | Mietabgleich für Garagen und Stellplätze zum 01.05., für Lager, Hallen und Außenflächen zum 01.06. |
| Mahnwesen                 | Fälle und Stufen je Woche                                | Mahnlauf wie der Abgleich, zusätzlich erst mit geprüften Vorlagen                                  |
| Stammdaten                | Jede Einheit und jeder Vertrag einmal von dir geprüft    | Verzeichnisse in Notion gehen nach dem letzten Umzug ins Archiv                                    |
| Hof-Cockpit               | Zähler auf beiden Seiten                                 | System-Check und Hof-Tab zwei Monate nach Schritt 9                                                |
| Website                   | Belegung je Einheit                                      | Nachtlauf mit dem letzten Umzug                                                                    |

Mietabgleich und Mahnlauf schrumpfen mit jedem Umzug auf den Wohnraum. Ganz enden sie erst mit dem Wechsel zu Verwaltio; so lange sieht Claude über diese beiden Läufe weiter Wohnraumdaten. Der Termin dieses Wechsels hängt am Test und steht nicht in diesem Plan.

### Was automatisch übernommen wird

Alles, was heute schon in einem System steht, kommt per Import. Von Hand bleibt, was nur im PDF oder vor Ort steht.

| Daten                                                 | Quelle heute                                         | Weg                                 | Dein Teil                                                                                               |
|-------------------------------------------------------|------------------------------------------------------|-------------------------------------|---------------------------------------------------------------------------------------------------------|
| Bankumsätze ab 01.01.2026                             | Vivid, VR-Konto                                      | automatisch über den Abruf          | keiner                                                                                                  |
| Zuordnungsregeln                                      | hof-fints: 57 Miet- und 9 Pachtregeln                | Import                              | Abweichungen zwischen den zwei Rechnern klären                                                          |
| Kategorien                                            | Finanz-Dashboard, 35 Kategorien                      | Import                              | einmal durchsehen                                                                                       |
| Einheiten mit NW-ID, Typ und Status                   | Einheiten-Verzeichnis in Notion, Katalog der Website | Import mit Probelauf                | fehlende Flächen nachtragen; in HelloHousing haben 16 Einheiten 0 m²                                    |
| Parteien und laufende Verträge außerhalb von Wohnraum | HelloHousing: Mieter, Miete, Mietverlauf             | Import mit Probelauf                | Kündigungsfrist, Kaution, Zahlweise und Sonderabreden aus dem PDF ergänzen; jeden Vertrag einmal prüfen |
| Zähler und Stände                                     | HelloHousing, Notizen                                | Import, soweit vorhanden            | fehlende Zähler anlegen und beim nächsten Besuch ablesen                                                |
| Vorlagen                                              | Vorlagen-Bibliothek in Notion, 32 Einträge           | Übernahme als Text mit Platzhaltern | Prüfung durch die Kanzlei                                                                               |
| Verträge als PDF                                      | iCloud-Archiv                                        | je Vertragsart beim Umzug           | nichts; der Rest nach Bedarf                                                                            |

Die Importe schreibt Claude Code gegen den Testhof. Ausführen tust du sie auf dem Server; der Bericht nennt nur Zähler und Einheit-IDs.

### Seite „Datenstand“

Eine eigene Seite zeigt, welche Stammdaten fehlen. Für jede Art ist festgelegt, welche Felder Pflicht sind; die Seite zählt die Lücken je Bereich und führt mit einem Klick zur Eingabe.

| Bereich   | Gilt als vollständig, wenn                                                                       |
|-----------|--------------------------------------------------------------------------------------------------|
| Einheit   | Typ, Fläche, Objekt, Status und Zielmiete stehen; Zähler zugeordnet, wo einer vorgesehen ist     |
| Partei    | Anschrift und ein Kontaktweg stehen                                                              |
| Vertrag   | Beginn, Miete, Zahlweise, Kündigungsfrist, Kaution und der unterschriebene Scan liegen vor       |
| Zähler    | Nummer, Art, Einbaudatum und Eichfrist stehen; ein Stand zum letzten Stichtag mit Foto liegt vor |
| Kaution   | Betrag, Eingangsdatum und Konto stehen                                                           |
| Schlüssel | Anzahl je Einheit und wer sie hat                                                                |

Der Bereich Zähler zeigt dazu eine Liste für deinen nächsten Besuch: alle Zähler ohne aktuellen Stand, sortiert nach Objekt. Das Hof-Cockpit zeigt die Zahl der Lücken, der Status-MCP auch, nur mit Einheit-IDs.

## Meilensteine

| Was | Termin |
|---|---|
| 0 Grundgerüst | 23.11.–04.12.2026 |
| 1 Bank | 07.12.–18.12.2026 |
| 2 Kassenbuch | 21.12.2026–08.01.2027 |
| 3 Dokumente und Belege | 11.01.–29.01.2027 |
| 4 Stammdaten | 01.02.–12.02.2027 |
| 5 Sollstellung | 15.02.–19.02.2027 |
| 6 Abgleich | 22.02.–05.03.2027 |
| Abgleich parallel zum alten Lauf | 08.03.–30.04.2027 |
| 7 Mahnwesen | 08.03.–19.03.2027 |
| 8 Zähler | 22.03.–26.03.2027 |
| 9 Fristen und Cockpit | 29.03.–02.04.2027 |
| 10 Anbindungen | 05.04.–16.04.2027 |
| 11 Status-MCP | 19.04.–23.04.2027 |
| Erster Umzug | 01.05.2027 |
| Letzter Umzug | 01.06.2027 |

Der kritische Pfad läuft über den Abgleich. Er steht am 05.03., läuft im März und April neben dem alten Mietabgleich und trägt ab dem 01.05. die Garagen und Stellplätze. Lager, Hallen und Außenflächen folgen zum 01.06.

Gerechnet ist mit drei Arbeitstagen pro Woche, im Dezember mit zwei und einer freien Woche zum Jahreswechsel.

Das Ziel 01.04. aus dem Setup-Review ist mit drei Arbeitstagen pro Woche nicht zu halten. Dafür müsste der Abgleich Anfang Februar stehen. Das sind 38 Arbeitstage in zehn Wochen, also vier Tage pro Woche neben der NKA 2025.

Drei Termine liegen bei dir: die NKA 2025 bis zum 31.12., das Ergebnis des Verwaltio-Tests bis Ende Februar und die geprüften Vorlagen bis Ende März.

## Offene Punkte und Risiken

Neun Punkte liegen außerhalb des Codes; sechs davon brauchen eine Antwort von dir oder von Dritten, bevor ihr Schritt beginnt.

| Punkt                                                                                     | Wirkt auf                                                                  | Nächster Schritt                                                                                                                                                         | Wer            |
|-------------------------------------------------------------------------------------------|----------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------|
| Verwaltio-Test: API, Mahnwesen, Mieterhöhung, Bankanbindung, Export, Auftragsverarbeitung | Schritt 10 und den Wechsel des Wohnraums                                   | Testzugang anlegen; Ergebnis bis Ende Februar                                                                                                                            | du             |
| Prüfung der Vorlagen                                                                      | Versand in Schritt 3 und 7 wird erst damit scharf                          | Vorlagen nach Schritt 3 aus dem System an die Kanzlei geben                                                                                                              | du, Herr Bucks |
| Umsatzsteuer bei Garagen, Stellplätzen und Lager                                          | Kondition und Sollstellung; entscheidet, ob das System Rechnungen schreibt | Antwort vor Schritt 4, also bis Ende Januar                                                                                                                              | du, Dreses     |
| Kaution auf dem Sammelkonto                                                               | Kaution in Schritt 4                                                       | Bewertung, ob das Konto als Treuhandkonto gilt                                                                                                                           | Herr Bucks     |
| Aufgabentracker                                                                           | Schritt 9                                                                  | Bestätigen: Freigaben erscheinen im Hof-Backlog mit Einheit-ID und Link                                                                                                  | du             |
| Postfächer                                                                                | Schritt 3                                                                  | SMTP-Zugang für kontakt@, eine Adresse, an die das Rechnungspostfach weiterleitet, und ein Konto bei LetterXpress mit API-Schlüssel und Vertrag zur Auftragsverarbeitung | du             |
| Notfall zwischen zwei Besuchen                                                            | Betrieb nach deinem Umzug                                                  | Regelung für Schlüssel, Ansprechperson und Handwerker (Q4 im Setup-Review)                                                                                               | du             |
| Zeit                                                                                      | alle Termine                                                               | Drei Tage pro Woche sind eine Annahme. Mit zwei Tagen endet der Bau Mitte Juni statt Ende April.                                                                         | du             |
| Eine Person                                                                               | Betrieb                                                                    | Notfallmappe und Rückspielprobe in Schritt 0; mehr sieht v1 nicht vor                                                                                                    | du             |

## Abnahme

Mit deiner Abnahme beginnt Schritt 0. Sechs Punkte gibst du frei:

- [ ] Technik: Django und Postgres auf einem eigenen Hetzner-Server, Zugang nur über Tailscale

- [ ] Datenmodell: elf Bereiche, Kern ist die Kette von Vertrag bis Bankumsatz

- [ ] Zwölf Bauschritte in dieser Reihenfolge, mit ihren Abnahmekriterien

- [ ] Testhof statt Echtdaten; Importberichte nur mit Zählern und Einheit-IDs

- [ ] Termine bei drei Tagen pro Woche: v1 gebaut am 23.04.2027, erster Umzug zum 01.05.2027

- [ ] Aufgabentracker: Freigaben erscheinen im Hof-Backlog mit Einheit-ID und Link

Die Termine gelten, wenn du bis zum 22.11. abnimmst. Jede Woche später verschiebt alles um eine Woche.

Für Schritt 0 brauche ich von dir zwei Dinge: einen Hetzner-Server in deinem Tailscale-Netz und ein leeres privates Repository. Danach schreibe ich die Aufgaben für Claude Code: Aufbau des Repositorys, Regeln der KI-Grenze, Generator für den Testhof, Pipeline.

## Quellen und Grenzen

Der Plan beruht auf drei Quellen mit Stand 17.11.2026:

- [Prozessliste Hof Noelle-Wying](https://claude.ai/artifact/JEsQ8yweYZVLo32FkJLwXe): die 57 Abläufe der Stufe v1, die zehn Standardabläufe und die Reihenfolge der Bauschritte

- [Setup-Review](https://claude.ai/artifact/7CDgSSAg78KHUjkgmeSM7Q): Zielbild, Datenlandkarte und die Entscheidungen E1 bis E10

- deine Antworten vom 16. und 17.11. zu P1 bis P9 und E10

Grenzen:

- Die Repositorys von hof-fints und Website habe ich für diesen Plan nicht neu gelesen. Wie viel Code einzieht, zeigt Schritt 1.

- Die Tage sind Schätzungen ohne Erfahrungswert aus diesem Projekt. Nach Schritt 2 lassen sie sich nachschärfen.

- Verwaltio ist nicht getestet. Schritt 10 beruht auf Angaben des Anbieters.

- Die genannten Pakete (django-pghistory, django-allauth, WeasyPrint, restic) sind Vorschläge; Schritt 0 legt sie fest.

- Rechtliches habe ich nicht geprüft. Mahnstufen und Fristen stammen aus der SOP; Vorlagen, Versandwege und Kaution gehören zu Herrn Bucks.
