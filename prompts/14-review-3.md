# Review 3 – Feinschliff nach dem Umbau (19.11.2026)

Lies `CLAUDE.md`, `prompts/12-review-1.md` und `prompts/13-review-2-ux.md`. Der Umbau ist gelungen; die Punkte aus Review 2 Teil A sind bis auf die unten genannten erledigt. Dieser Durchgang (Desktop, Testhof) hat nur noch wenige echte Fehler gefunden, dafür zwei Querschnittsthemen. Bau den Testhof vor dem Start neu auf, ich habe beim Testen Daten verändert.

Leg mir zuerst einen kurzen Plan vor (Reihenfolge, PRs) und warte auf mein OK. Punkt 1 und 2 gehen vor.

## Muss

1. **Zwei Uhren.** Der Testhof steht auf Stichtag 01.11.2026, viele Stellen rechnen aber mit dem echten Datum (19.11.2026). Folgen:
   - Das Cockpit heißt „Heute · Donnerstag, 19. November 2026“, das Band oben sagt „Stichtag 01.11.2026“.
   - Neue Einträge sind mit dem echten Datum vorbelegt: Zugang der Kündigung, Datum der Übergabe.
   - Arbeitsposten sind „fällig 19.11.2026“. Die Bankfreigabe gilt als am 11.11. abgelaufen, obwohl der Stichtag davor liegt.
   - Rückstand und Offene Posten widersprechen sich wieder. Rückstand: „139,05 € · 10.143,14 € nicht prüfbar“. Offene Posten: „45 überfällig, 10.282,19 € überfällig“. Die Einheitenliste zeigt bei fast jedem Vertrag eine volle Monatsmiete als „offen“.

   Lösung:
   - Es gibt genau eine Funktion `kern.zeit.heute()`. Im Testhof liefert sie den Stichtag, in prod das echte Datum. Alles nutzt sie: Vorbelegungen, Fälligkeit, Fristen, Freigaben, Läufe, Cockpit-Kopf.
   - Ein Test sucht im Code nach `date.today()`, `timezone.now().date()` und Ähnlichem außerhalb von `kern.zeit` und schlägt fehl, wenn er etwas findet.
   - „Offen“, „überfällig“ und „nicht prüfbar“ sind überall dieselben drei Begriffe, mit Erklärung per Tooltip. „Nicht prüfbar“ heißt: fällig nach dem letzten Bankabruf. Die Offene-Posten-Kachel im Cockpit und die Rückstand-Kachel zeigen dieselbe Aufteilung.
   - Die Spalte „offen“ in der Einheitenliste zeigt nur Überfälliges, nicht die laufende Monatsmiete.
2. **Radio-Gruppen überlappen.** Bei Optionen mit Radio-Buttons schieben sich Text, Fehlermeldung und das nächste Feld übereinander. Betroffen:
   - Kündigung „Wunschtermin vor dem Fristende“, im Vertragsdialog und in der Kündigungsanfrage
   - Serienmitteilung „Wer keine E-Mail-Adresse hat“
   - Den Baustein reparieren: Optionen untereinander, Label neben dem Radio, Fehlermeldung unter der Gruppe.
   - Der Playwright-Smoke-Test bekommt eine Prüfung auf überlappende Elemente in Formularen und öffnet die Dialoge mit Fehlerzustand mindestens einmal.
3. **Regel doppelt angelegt.** Ablauf: „Übernehmen“ mit „Als Regel merken“ legt Regel #301 an. Danach steht die Gruppe weiter als „24 Umsätze von Karz Dussen van GmbH ohne Regel“ da. „Für alle 24 anwenden“ mit gesetztem Haken will eine zweite, gleiche Regel anlegen. Die Meldung zeigt dann Interna (Priorität 10, Überschneidung mit #301).
   - Gibt es schon eine gleiche Regel, wird sie verwendet, nicht neu angelegt.
   - Nach dem Anlegen einer Regel heißt die Gruppe „23 Umsätze passen auf Regel #301“ und hat den Knopf „Regel anwenden“.
   - Der Link „jetzt anwenden →“ im Toast hat bei mir nichts bewirkt. Prüfen. Der Toast bleibt stehen, bis man ihn schließt.
   - Die Zahl im Listenkopf (50) und die Zahl in der linken Spalte (49) laufen nach einer Aktion auseinander. Beide werden zusammen aktualisiert.
4. **Neue Partei ohne Name und Anschrift möglich.**
   - Beim „Annehmen und Vertrag anlegen“ sind Vor- und Nachname optional, und Felder für die Anschrift fehlen ganz. Damit entsteht ein Vertrag, an den sich kein Brief schicken lässt.
   - Pflicht ist ein Name (Person oder Firma). Die Anschrift kann man eintragen oder ausdrücklich auf „Anschrift folgt“ setzen. Dann steht die Lücke im Datenstand, und Briefe an diese Partei sind gesperrt, bis sie ergänzt ist.
   - Partei bekommt das Feld Anrede: Frau, Herr, neutral, Firma. Bei „neutral“ beginnen Schreiben mit „Guten Tag Vorname Nachname“. Der Testhof vergibt Anreden passend zum Vornamen. Heute bekommt „Tom Bohnbach“ den Brief mit „Sehr geehrte Frau Bohnbach“.

## Sollte

5. **Vertrag mit Beginn in der Zukunft** (V-0046, Beginn 01.12.2026):
   - Der Kopf sagt „seit 01.12.2026“ und „Miete heute 86,81 €“, die Konditionszeile trägt das Badge „heute“.
   - Richtig wäre „ab 01.12.2026“, „Miete ab Beginn“ und kein Badge „heute“.
6. **Schreiben**
   - Der Hinweis lautet doppelt „Senden gesperrt: Senden ist gesperrt: …“.
   - Ist der Briefkopf nachgetragen, gibt es einen Knopf „Neu erzeugen“ statt „verwerfen und neu erzeugen“.
   - Serienmitteilung: Statt „an der Stelle {{ mitteilung.text }}“ heißt es „erscheint im Schreiben an der Stelle des Mitteilungstexts“.
7. **Beleg und Abbuchung**
   - Weicht der eingetippte Betrag vom gelesenen PDF-Betrag oder von der vorgeschlagenen Abbuchung ab, steht der Hinweis schon vor dem Bestätigen am Feld, nicht erst danach.
   - In der Dokumentenliste stehen bei noch nicht bestätigten Belegen „–“ für Lieferant und Betrag. Dort sollen die aus dem PDF gelesenen Werte erscheinen, grau, mit dem Zusatz „gelesen“.
8. **Arbeitslisten**
   - Titel nicht doppelt: „Belege zuordnen · Belege zuordnen“, „Buchungsanfrage prüfen · Buchungsanfrage prüfen“. Der Titel nennt den Gegenstand, z. B. „Zänker AG · Rechnung 4779“ oder „NW-G-0004 · Jochem Stumpf“.
   - Pfade in Meldungen sind Links (Review 1, Punkt 8), z. B. `/bank/konten/9/freigabe/`.
9. **Kündigung durch jemanden, der nicht Vertragspartei ist.**
   - Kündigungsanfrage #15 kommt von „Martine Mende“, Mieter ist „Giorgio Koch“. Die Warnung ist da, „Kündigung anlegen“ geht aber trotzdem mit einem Klick.
   - Bei abweichendem Absender braucht es einen ausdrücklichen Haken „Vollmacht liegt vor“ oder „Absender ist Vertragspartei (anderer Name/E-Mail)“, sonst bleibt der Knopf gesperrt.
   - ⚖️ Gehört zusammen mit der Frage zur Textform aus Review 2 (A3) in die Liste für den Anwalt.
10. **Kleinigkeiten**
    - Protokoll: Interne Bezeichnungen wie „Checkliste #18 rueckgabe“ gehören in Klartext: „Checkliste Rückgabe“.
    - Toasts nach einer Rücknahme kürzen: „Kündigung V-0002 zurückgenommen · 12 Änderungen → ansehen“.
    - Cockpit: Die rechte Spalte ist oben fast leer (nur „Belege zuordnen: 1“). Die Kacheln so verteilen, dass keine großen Lücken entstehen.
    - Läufe, die in dev nicht laufen können (bank_abruf, sicherung_pruefen, tageslauf), erscheinen als „übersprungen (dev)“ statt „mit Fehlern“, wie schon bei benachrichtigen.

## Offen, bitte selbst prüfen

- **Handy-Ansicht:** Ich konnte das Fenster nicht schmal machen, und die App lässt sich nicht in ein iframe laden (X-Frame-Options, so ist es richtig). Lege Screenshots der Playwright-Smoke-Tests bei 390 px in den PR, mindestens: Cockpit, Arbeitslisten, Vertrag, Übergabe Schritt 1–7, Zähler-Ablesung, Beleg.

Am Ende: PRs, Screenshots (1440 und 390 px), die Klick-Tabelle aus Review 2 aktualisiert, offene Punkte.
