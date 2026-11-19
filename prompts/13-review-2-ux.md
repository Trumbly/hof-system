# Review 2 – Fehler, Abläufe mit weniger Klicks, Gestaltung (19.11.2026)

Lies `CLAUDE.md` und `prompts/12-review-1.md`. Review 1 gilt weiter. Punkte, die dort schon stehen (Navigation gruppieren, deutsches Zahlenformat, Stichtag-Band, eine Wahrheit für „offen“), wiederhole ich hier nicht, setze sie aber mit um, wo sie dieselben Seiten betreffen.

Grundlage ist ein kompletter Durchgang durch die laufende Anwendung mit dem Testhof, am Desktop (1440 px) und am Handy (400 px). Bau den Testhof vor dem Start neu auf, ich habe beim Testen Daten verändert.

**Vorgehen:**
1. Leg mir zuerst einen Plan vor: Reihenfolge, Pull Requests und was du je PR testest. Warte auf mein OK.
2. Teil A geht vor allem anderen.
3. Für Teil C (Gestaltung) baust du zuerst nur eine Seite `/stil/`: alle Bausteine auf einer Seite, dazu Cockpit und Vertragsseite im neuen Stil. Schick mir Screenshots (1440 px und 390 px). Erst nach meinem OK stellst du die übrigen Seiten um.

**Klarstellung zur KI-Grenze:** Die Grenze gilt für Claude, den Status-MCP, Notion, Läufe-Meldungen und Logs. Sie gilt nicht für meine eigene Oberfläche. In der UI zeigt das System Namen, Kontakt und Adresse überall dort, wo ich sie zum Arbeiten brauche: in Auswahllisten, Klärfällen, Arbeitslisten und Vertragsköpfen. Ergänze das in `CLAUDE.md`. Hinweistexte wie „kurz und ohne Namen“ bleiben nur an Feldern, deren Inhalt nach Notion oder in den MCP geht.

---

## A. Fehler (zuerst)

1. **Übergabe Schritt 3 (Fotos) ohne Foto bringt den Server zum Absturz.** Ein POST mit leerem Mehrfach-Datei-Feld beendet den Dev-Server. Schritt 5 mit leerem Einzel-Datei-Feld geht. Leeres Feld = kein Foto, weiter zum nächsten Schritt. Test dazu.
2. **Zahlungserinnerung nennt zwei verschiedene Beträge.** Schreiben #1 (V-0005): Text „ein Betrag von 14,60 € offen“, Tabelle darunter „10/2026 76,73 €“. Die Tabelle zeigt das Soll statt des offenen Rests. Die Tabelle zeigt je Posten Soll, bezahlt und offen, und die Summe der offenen Beträge entspricht dem Betrag im Text. Test für Teilzahlungen. Das Schreiben geht an Mieter, deshalb ist das der wichtigste Punkt.
3. **„Kündigung erfasst“ bei einer Website-Kündigung legt keine Kündigung an.** Buchungsanfrage #6 (NW-S-0001, V-0010): Der Knopf setzt nur die Anfrage auf erledigt. V-0010 bleibt aktiv, es gibt weder Kündigung noch Bestätigung oder Fristen. So geht eine Kündigung verloren.
   - Der Knopf heißt „Kündigung anlegen“. Er öffnet den Kündigungsdialog des Vertrags, vorausgefüllt mit Zugang = Meldedatum, „gekündigt von: Mieter“, Wunschtermin und Zugangsweg „Website-Formular“.
   - Erst nach dem Speichern ist die Anfrage erledigt und mit der Kündigung verknüpft.
   - Weichen Absender (Name, E-Mail) und Vertragspartei ab, steht ein deutlicher Hinweis daneben.
   - ⚖️ Für den Anwalt: Reicht eine Kündigung über das Website-Formular (Textform) bei Garagen-, Stellplatz- und Gartenverträgen, wenn der Vertrag Schriftform verlangt? Bis das geklärt ist, steht in der Kündigungsbestätigung bei Zugangsweg „Website“ kein Satz, der die Form ausdrücklich anerkennt. Schreib das als offene Frage in deinen Bericht.
4. **Rücknahme im Protokoll hinterlässt halbe Zustände.** Ich habe beim Testen „Verträge #47 geändert“ (Kündigung V-0002) zurückgenommen. Danach ist der Vertrag wieder aktiv, aber Kündigung #3, die Kündigungsbestätigung (Schreiben #3, weiter „bereit“ und sendbar) und die Fristen #62/#63 bleiben bestehen.
   - Jede Nutzeraktion bekommt eine Vorgangs-ID (Kontext in pghistory).
   - Das Protokoll zeigt Vorgänge, nicht Einzelereignisse: „Kündigung V-0002 erfasst, 19.11. 08:44, 8 Änderungen“, die Einzelheiten aufklappbar.
   - „Zurücknehmen“ gibt es nur für den ganzen Vorgang. Abhängige bereite Schreiben werden dabei verworfen.
   - Ist ein Schreiben aus dem Vorgang schon versandt, ist die Rücknahme gesperrt, und die Seite sagt warum.
5. **Fristen sind nicht nach Datum sortiert.** Die Liste zeigt 31.12.2029 vor 11.11.2026. Sortierung: überfällig zuerst, dann aufsteigend nach Datum.
6. **Beleg-Formular nach dem Bestätigen leer.** Beleg #23 ist mit 627,34 € gespeichert (in der Belegmappe zu sehen), die Felder Belegdatum und Betrag erscheinen danach aber leer. Außerdem:
   - Der Vorschlag verweist auf sich selbst („Vorschlag aus Beleg #23“).
   - Der Vorschlagstext nennt eine andere Kostenart als die Vorbelegung („Reparaturen und Instandhaltung“ im Text, „Winterdienst“ im Feld).
7. **Beleg und Abbuchung passen nicht zusammen, und niemand merkt es.** Beleg #23: Rechnung 4779 über 627,34 €. Die Abbuchung mit „Rechnung 4779“ im Zweck lautet −527,34 €. Wenn Rechnungsnummer oder Lieferant passen, der Betrag aber abweicht, gibt es einen Arbeitsposten „Beleg und Abbuchung weichen ab“ mit beiden Beträgen. Falls der Testhof das absichtlich so anlegt, umso besser: Dann ist das der Test.
8. **Serienmitteilung bricht ganz ab, wenn einem Empfänger die E-Mail fehlt.** Die Meldung lautet „Für den Versand per Mail fehlt empfaenger.email.“.
   - Vor dem Erzeugen kommt eine Vorschau der Empfänger: Einheit, Name, Versandweg.
   - Wer keine E-Mail hat, bekommt automatisch Brief (umstellbar), oder er wird ausgelassen und namentlich aufgeführt.
   - Fehlermeldungen in der UI sind Sätze auf Deutsch, nie Feldnamen.
9. **Mailversand in dev:** „Nicht gesendet: SMTP: ConnectionRefusedError“. Mailpit kommt in die Dev-Compose-Datei, oder dev nutzt das Datei-Backend. Die Meldung in der UI heißt „Mailserver nicht erreichbar, Schreiben bleibt bereit“. Der Status bleibt richtig auf „bereit“, das so lassen.
10. **Übergabe lässt sich ohne Umweg nicht abschließen.**
    - Sie verlangt eine Kaution, der Assistent hat aber kein Kautionsfeld. Kaution (Betrag, Art, „keine Kaution vereinbart“) kommt als Schritt in den Assistenten.
    - Schritt 2 (Zähler) und Schritt 4 (Schlüssel) sind Sackgassen, wenn nichts da ist. Sie bieten dann „Zähler anlegen“ bzw. „Schlüssel ausgeben“ an oder werden mit Begründung übersprungen.
    - Schritt 1: Die Warnung „Vertrag noch nicht unterschrieben“ verschwindet sofort beim Anhaken. „Speichern und weiter“ braucht heute zwei Klicks.
11. **Kleinere Fehler**
    - Checkliste: „Vertrag aus Vorlage erzeugt“ wird nicht abgehakt, obwohl das PDF erzeugt wurde.
    - Klärfall zugeordnet: Das System bietet nicht an, die Zahlerregel #50 zu korrigieren, die auf den falschen Vertrag zeigt.
    - Vertrag V-0046 aus der Einheitenseite ist nicht mit der angenommenen Buchungsanfrage #5 (NW-GA-0003) verknüpft.
    - P8: Ab 100 € Monatsmiete fehlt der Bonitäts-Hinweis im Vertragsablauf.
    - Stammdaten zählt 43 Verträge, andere Seiten 45 bzw. 44. Eine Zählung für alle.
    - Mahnsperre aus Klärfall: Wird der Klärfall erledigt, hebt das System die Sperre sofort auf, nicht erst beim Montagslauf (Sperre #4 hängt noch).
    - Kündigung: Liegt der Wunschtermin vor dem Fristende (31.12.2026 gewünscht, Fristende 28.02.2027), wird er heute stillschweigend übergangen. Stattdessen kommt ein Hinweis mit der Wahl „Fristende 28.02.2027“ oder „Aufhebung zum Wunschtermin (Vorlage Aufhebungsvertrag)“.
    - Vertragstext: „Garten Garten 3“ (Typ doppelt), „140,00 € je Monat ,“ (Leerzeichen vor dem Komma).
    - Datums- und Monatsfelder auf Englisch („October 2026“, „Choose files“): `<html lang="de">` und eigene Dateiknöpfe (C4).
    - Katalog: Einheiten ohne Fläche (NW-G-0017) gehen auf die Website, und keine Einheit hat Bilder. Fehlende Pflichtangaben für den Katalog stehen im Datenstand. Eine Einheit ohne Fläche ist nicht buchbar.
    - Monatsabschlüsse: Seit 11/2024 ist kein Monat geschlossen. Es fehlt ein Knopf „Monat jetzt abschließen“ für „bereit“-Monate. Statt „Mandant #18“ steht der Name des Mandanten. Die IDs in der Meldung sind Links.

---

## B. Abläufe mit weniger Klicks

Leitlinien für alle Seiten:
- **Eine Seite, eine Hauptaktion.** Formulare sind nicht dauerhaft offen. Seiten zeigen eine Lesesicht, Aktionen öffnen einen Dialog (`<dialog>` mit HTMX). Nach dem Speichern wird der betroffene Teil ersetzt, die Seite springt nicht nach oben.
- **Vorausfüllen statt Abtippen.** Was das System weiß oder vorschlagen kann, steht schon im Feld. Ich bestätige nur.
- **Nach einer Aktion kommt der nächste Posten**, nicht der Anfang der Liste.
- **Rückmeldungen sagen, was passiert ist**, mit Link: „Kündigung erfasst · Ende 28.02.2027 · Bestätigung #3 liegt bereit → ansehen“ statt „Gespeichert.“

Zielwerte für die wichtigsten Abläufe (gemessen im Durchgang, Klicks ohne Tippen):

| Ablauf | heute | Ziel |
|---|---|---|
| Umsatz einordnen (je Umsatz) | ~5 Klicks + Scrollen, eigene Seite | 1 Klick (Vorschlag übernehmen), mehrere zugleich |
| Klärfall zuordnen | ~4 Klicks, Auswahlliste mit 46 Verträgen ohne Namen | 1 Klick auf Vorschlag oder Alternative |
| Buchungsanfrage → Vertrag | ~15 Klicks über 3 Seiten, Kontaktdaten neu tippen | 3 Klicks |
| Beleg erfassen | 8 Felder tippen, PDF klein unten | prüfen + 1 Klick |
| Website-Kündigung übernehmen | geht heute nicht (A3) | 2 Klicks |
| Übergabe ohne Kaution | Assistent verlassen, Vertrag öffnen, Kaution erfassen, zurück | im Assistenten |

1. **Suche in der Kopfzeile (auch Strg+K).** Sie findet NW-ID, V-Nummer, Name, Ort, E-Mail, Rechnungsnummer und Betrag. Treffer sind gruppiert (Einheiten, Verträge, Parteien, Umsätze, Belege), Enter öffnet den Treffer.
2. **Arbeitslisten als Posteingang.**
   - Links die Arten mit Zahl, rechts die Posten. Jeder Posten lässt sich direkt in der Liste bearbeiten (HTMX).
   - Mehrfachauswahl mit „für alle übernehmen“.
   - Gleichartige Posten erscheinen gebündelt (siehe Review 1, Punkt 5).
   - Tastatur: j/k zum Wechseln, Enter übernimmt den Vorschlag, e öffnet die Bearbeitung.
3. **Umsatz einordnen**
   - Der Vorschlag kommt aus Gegenpartei, Zweck und Verlauf, z. B. „Material · HOF“ bei „Material Baumarkt“.
   - Die Kategorienliste ist nach Richtung gefiltert: keine Einnahme-Kategorien bei einer Ausgabe.
   - „Als Regel merken“ ist angehakt, wenn die Gegenpartei schon einmal vorkam.
   - Umsatz, Vorschlag und Knöpfe stehen in einer Zeile, ohne Seitenwechsel.
4. **Klärfall**
   - Links der Umsatz (Betrag, Gegenpartei, Zweck), rechts der Vorschlag und bis zu zwei Alternativen als Karten mit Vertrag, Einheit, Mieter-Name und offenen Posten. Ein Klick ordnet zu.
   - Darunter „anderen Vertrag suchen“ als Suchfeld mit Namen, keine lange Auswahlliste.
   - Weicht die Zahlerregel ab, ist „Zahlerregel auf diesen Vertrag ändern“ angehakt.
5. **Buchungsanfrage**
   - Der Knopf „Annehmen und Vertrag anlegen“ öffnet den neuen Vertrag vorausgefüllt: Einheit, Beginn = Wunschtermin, Miete = Zielmiete, Partei aus den Kontaktdaten.
   - Eine vorhandene Partei mit gleicher E-Mail wird vorgeschlagen, sonst wird eine neue angelegt.
   - Anfrage und Vertrag verweisen aufeinander.
6. **Neuer Vertrag**
   - Die Einheitenliste zeigt nur freie Einheiten (Schalter „alle“), mit Typ und Zielmiete.
   - Die Miete ist aus der Zielmiete vorbelegt.
   - Partei wählen per Suchfeld mit Namen. „Neue Partei“ lässt sich direkt im Dialog anlegen.
   - Kaution mit Option „keine“. Ab 100 € erscheint der Bonitäts-Hinweis (P8).
7. **Vertragsseite und Einheitenseite neu aufbauen**
   - **Kopf:** V-Nummer, Status-Badge, Einheit, Mieter-Name mit Telefon und E-Mail, Miete heute, Saldo (offen/Guthaben), nächste Frist. Rechts die Aktionen als Knöpfe: Miete ändern, Kündigung erfassen, Übergabe/Rückgabe, Schreiben erstellen, mehr ▾.
   - **Reiter:** Übersicht · Mietkonto (Sollposten und Zahlungen mit laufendem Saldo) · Dokumente und Schreiben · Mahnwesen · Verlauf.
   - Die heutigen 5–8 offenen Formulare mit je eigenem Speichern-Knopf werden Dialoge.
   - Einheitenseite entsprechend: Kopf mit Typ, Fläche, Status, aktueller Vertrag und Mieter, Zielmiete, Zähler. Reiter: Übersicht · Verträge · Zähler · Bilder/Katalog · Verlauf.
8. **Übergabe/Rückgabe am Handy**
   - Fortschrittsleiste, Kautionsschritt (A10), Kameraknopf je Schritt (C4).
   - Schritte ohne Inhalt bieten „anlegen“ an oder lassen sich überspringen.
   - Am Ende eine Zusammenfassung mit allem Fehlenden und je einem Link dorthin.
9. **Beleg erfassen**
   - Geteilte Ansicht: PDF links groß (mindestens die halbe Breite, mitlaufend), Formular rechts.
   - Datum, Betrag, Rechnungsnummer und Lieferant kommen aus der Textschicht des PDFs (pypdf/pdfminer, keine KI). Kostenart und Objekt kommen vom letzten Beleg desselben Lieferanten.
   - Gibt es die passende Abbuchung schon, steht sie daneben mit „verknüpfen“.
   - Nach dem Bestätigen öffnet sich der nächste offene Beleg.
10. **Fristen**
    - Kompakte Tabelle statt großer Karten, gruppiert: überfällig · nächste 14 Tage · später.
    - Eichfristen sind zu einer Zeile gebündelt („14 Eichfristen 2027–2033“, aufklappbar).
    - „Erledigt“ und „verschieben“ (+1 Woche, +1 Monat, Datum) direkt in der Zeile.
    - Eine abgelöste Bankfreigabe (#4) schließt ihre Frist (#41) automatisch.
11. **Listen**
    - **Einheiten:** Suche; Filter nach Typ, Objekt und Status (vermietet/frei/gekündigt); Spalten Mieter, Miete, Saldo, frei ab.
    - **Parteien:** Suche; Spalten Rolle und Verträge.
    - **Dokumente:** Spalten Lieferant/Absender, Betrag, Betreff statt nur „#26“. Die doppelten Knöpfe unter den Reitern entfallen.
    - **Kassenbuch:**
      - Filter zugeklappt, nur Suche und Monat sichtbar.
      - Spalten Gegenpartei und Zweck; Kontonamen statt „Bankkonto #3“.
      - 50 Zeilen mit „mehr laden“ statt 8 Seiten à 100.
      - Die Unternavigation reparieren: Sie bricht als 3-Spalten-Raster mit losen „·“ um. Das gilt auch für Bank und Monatsbericht.
    - **Katalog:** standardmäßig nur buchbare Einheiten; „alle 54“ als Schalter.
    - **Belegmappe:**
      - Objekt und aktuelles Jahr sind vorbelegt.
      - Buchungs- und Beleg-IDs sind Links.
      - Die Liste „Buchungen ohne Beleg“ wird eine Tabelle (Datum, Gegenpartei, Betrag) mit „Beleg zuordnen“ je Zeile, statt 90 IDs in einem Satz.
12. **Datenstand:** gruppiert nach fehlendem Feld („44 Verträge ohne Scan“) mit Sammelaktion (Scans hochladen und per V-Nummer im Dateinamen zuordnen). Dichte Tabelle statt Karten. Der Testhof bekommt Scans, damit die Seite nicht nur aus diesem einen Punkt besteht.
13. **Schreiben**
    - Titel lesbar: „Zahlungserinnerung · V-0005 · Tom Bohnbach“ statt „zahlungserinnerung“; Bezug als V-Nummer statt „Vertrag #91“.
    - Neben dem PDF steht der Mailtext.
    - Die PDF-Vorschau ist groß (volle Breite, A4-Verhältnis).
    - Ist Senden gesperrt (Review 1, Punkt 4), stehen die Gründe am Knopf.
    - Serienmitteilung: Empfängervorschau (A8), Auswahl nach Objekt, Typ und Einzel-Einheiten, Freitext-Feld für „Mitteilung an Nutzer“. Vorlagen wie Vertrag oder Übergabeprotokoll stehen dort nicht zur Wahl.
14. **Protokoll:** Vorgänge statt Ereignisse (A4). Feldnamen auf Deutsch („Status: aktiv → gekündigt“ statt `status: aktiv → gekuendigt`). `inhalt_html`, `pdf_sha256` und Event-IDs nur in den aufgeklappten Details. Filter nach Vertrag/Einheit, Person und Zeitraum.
15. **Konto:** zeigt den 2FA-Status (TOTP an/aus, Wiederherstellungscodes) und die E-Mail. „Verwaltung“ (Django-Admin) wandert in die Gruppe System und bleibt optisch erkennbar als Admin.
16. **Läufe:** Knopf „jetzt ausführen“ je Lauf (nur dev oder mit Bestätigung), Meldungen mit Links zu den betroffenen Posten.

---

## C. Gestaltung

Ziel: ruhig, dicht, gut lesbar, wie ein gutes Verwaltungswerkzeug (Richtung Linear, Stripe-Dashboard). Kein neues Framework: eigenes CSS mit Variablen, weiter HTMX und Server-Templates.

1. **Grundgerüst**
   - Desktop ab 1024 px: linke Seitenleiste mit den Gruppen aus Review 1, Punkt 13. Die heutige Kopfnavigation bricht bei 1440 px um („Offene / Posten“) und erzeugt eine waagrechte Scrollleiste. Beides beheben, die Ursache der Scrollleiste finden.
   - Inhaltsbreite: bis 1280 px für Tabellen und Übersichten, 720 px für reine Formulare. Heute ist alles eine schmale Spalte von ~560 px in der Mitte von 1440 px.
   - Handy: Kopfzeile mit Suche, unten eine Leiste mit Heute · Arbeitslisten · Zähler · Übergabe · Mehr.
2. **Gestaltungswerte** in `static/css/werte.css`
   - Farben: Grün als Primärfarbe beibehalten, Grautöne, Statusfarben (ok, Hinweis, Warnung, Fehler, neutral) mit ausreichendem Kontrast (WCAG AA).
   - Schrift: `system-ui`-Stapel; Größen 13/15/17/20/24/30; `font-variant-numeric: tabular-nums` für Zahlen.
   - Abstände in Schritten von 4 px, Radius 8 px, leichte Schatten nur für Dialoge und Menüs.
3. **Bausteine** als Template-Includes mit Einträgen auf `/stil/`:
   - Seitenkopf (Titel, Meta-Zeile, Primäraktion rechts) und Kennzahl-Kacheln.
   - Tabelle: dicht, Zeilen-Hover, Zahlen rechtsbündig, Kopf sticky, Leerzustand.
   - Status-Badge (aktiv, gekündigt, Entwurf, offen, überfällig, gesperrt, bereit, versandt).
   - Dialog, Toast-Meldung, Aufklapper und Reiter.
   - Formularfeld: Label über dem Feld, Hilfetext, Fehler direkt am Feld. Gestaltete Selects und Datumsfelder; die heutigen Selects sind schmal und ungestaltet.
   - Knöpfe: primär (höchstens einer je Ansicht), sekundär, Text, Gefahr.
   - Karte für Übersichten. Zahlenfilter `|euro` und `|zahl` für das deutsche Format (Review 1, Punkt 11).
4. **Handy und Eingabe**
   - Dateifelder werden zu eigenen Knöpfen: „Foto aufnehmen“ (`accept="image/*" capture="environment"`) und „Datei wählen“, beides mit Vorschau der Bilder.
   - Checkboxen und Knöpfe mindestens 44 px hoch.
   - Zahlenfelder mit `inputmode="decimal"`.
5. **Prüfung**
   - Ein Smoke-Test mit Playwright ruft die 15 wichtigsten Seiten bei 1440 und 390 px auf und prüft: keine waagrechte Scrollleiste, keine englischen Standardtexte („Choose file“, „Page not found“ in prod), kein Server-Fehler.
   - Screenshots dieser Seiten gehen als Artefakt in den PR, damit ich sie ansehen kann.

---

Am Ende: Pull Requests, eine Tabelle „Ablauf · Klicks vorher · Klicks nachher“ (gemessen, nicht geschätzt), Screenshots der neuen Hauptseiten, offene Punkte (darunter die Anwaltsfrage aus A3) und was ich selbst prüfen muss.
