# Klickmessung (Review 2, B)

Gemessen, nicht geschätzt: Playwright spielt jeden Ablauf so kurz wie möglich mit der
Oberfläche durch und zählt mit. Die Skripte liegen unter `tests/e2e/klickmessung/` (ein Modul
je Ablauf, die Selektoren je Ablauf an einer Stelle in `SELEKTOREN`) und laufen mit
`make klickmessung` gegen einen frischen Testhof (Seed 42, danach Tageslauf zum 02.11.2026).
Das Ergebnis steht in `tests/e2e/artefakte/klickmessung.md` (mit jedem Schritt in
`klickmessung.json`); von dort kommen die Zahlen in diese Tabelle.

## Ergebnis

„vorher“ gemessen am 19.11.2026 auf `44acd75` (main nach Schritt 7; der Messlauf fügt nur
Testcode hinzu). „nachher“ gemessen am 19.11.2026 auf `8b83838` (main nach Review 2,
R2-0 bis R2-9), mit denselben Skripten.

| Ablauf | Klicks vorher | Klicks nachher | Seitenwechsel vorher | Seitenwechsel nachher | Tippfelder vorher | Tippfelder nachher | Stand (Commit) vorher | Stand (Commit) nachher | Ziel (Review 2) |
|---|---:|---:|---:|---:|---:|---:|---|---|---|
| Umsatz einordnen (je Umsatz) | 4 | **1** | 2 | **0** | 0 | 0 | `44acd75` | `8b83838` | 1 Klick, mehrere zugleich |
| Klärfall zuordnen | 3 | **1** | 2 | **0** | 0 | 0 | `44acd75` | `8b83838` | 1 Klick |
| Buchungsanfrage → Vertrag | 18 | **3** | 14 | **2** | 6 | **0** | `44acd75` | `8b83838` | 3 Klicks |
| Beleg erfassen (aus dem Rechnungspostfach) | 2 | 2 | 2 | 2 | 3 | **0** | `44acd75` | `8b83838` | prüfen + 1 Klick |
| Beleg erfassen (hochladen, neuer Lieferant) | 5 | **4** | 2 | 2 | 5 | **0** | `44acd75` | `8b83838` | prüfen + 1 Klick |
| Website-Kündigung übernehmen | nicht möglich | **2** | – | 2 | – | 0 | `44acd75` | `8b83838` | 2 Klicks |
| ↳ Umweg von Hand, ohne Verknüpfung | 6 | entfällt | 6 | – | 2 | – | `44acd75` | `8b83838` | entfällt |
| Übergabe ohne Kaution (im Assistenten) | gescheitert | **11** | – | 8 | – | 1 | `44acd75` | `8b83838` | im Assistenten |
| ↳ Umweg: Kaution 0 € auf der Vertragsseite | 10 | 13 (nicht mehr nötig) | 8 | 8 | 1 | 2 | `44acd75` | `8b83838` | entfällt |

### Anmerkungen zu „nachher“

- **Beleg aus dem Postfach:** Es bleibt bei 2 Klicks: „Zuordnen“ und „Bestätigen und verknüpfen“. Tippen muss man nichts mehr.
  - Datum, Betrag, Rechnungsnummer und Lieferant kommen aus der Textschicht des PDFs.
  - Kostenart und Objekt übernimmt das System vom letzten Beleg desselben Lieferanten.
  - Die passende Abbuchung ist vorgewählt.
- **Beleg hochladen:** Es bleiben 4 Klicks: Datei wählen, Objekt, Kostenart, Bestätigen. Tippen muss man nichts.
- **Website-Kündigung:** Ein Umweg von Hand ist nicht mehr nötig. Der direkte Weg erledigt die Anfrage im selben Lauf und verknüpft sie mit der Kündigung.
- **Übergabe ohne Kaution:** Sie läuft jetzt ganz im Assistenten durch, 8 Schritte mit 11 Klicks. Das eine Tippfeld ist die Begründung „ohne Zähler weiter“, weil die Einheit keinen Zähler hat. Der Umweg über die Vertragsseite wird nicht mehr gebraucht.

## Nach Review 3

Gemessen am 20.11.2026 auf `a7a868a` (main nach Review 3, R3-1 bis R3-5, #35 bis #39) mit
`make klickmessung`, dieselben Skripte; angepasst sind nur die Selektoren der neuen
Pflichtwahlen aus Review 3, Punkt 4 und 9 (in R3-4). Alle neun Messungen laufen durch.

| Ablauf | Klicks nachher (R2) | Klicks nach R3 | Seitenwechsel nachher (R2) | Seitenwechsel nach R3 | Tippfelder nachher (R2) | Tippfelder nach R3 | Stand (Commit) nach R3 |
|---|---:|---:|---:|---:|---:|---:|---|
| Umsatz einordnen (je Umsatz) | 1 | 1 | 0 | 0 | 0 | 0 | `a7a868a` |
| Klärfall zuordnen | 1 | 1 | 0 | 0 | 0 | 0 | `a7a868a` |
| Buchungsanfrage → Vertrag | 3 | **4** | 2 | 2 | 0 | 0 | `a7a868a` |
| Beleg erfassen (aus dem Rechnungspostfach) | 2 | 2 | 2 | 2 | 0 | 0 | `a7a868a` |
| Beleg erfassen (hochladen, neuer Lieferant) | 4 | 4 | 2 | 2 | 0 | 0 | `a7a868a` |
| Website-Kündigung übernehmen | 2 | **3** | 2 | 2 | 0 | 0 | `a7a868a` |
| ↳ Umweg von Hand, ohne Verknüpfung | entfällt | entfällt | – | – | – | – | `a7a868a` |
| Übergabe ohne Kaution (im Assistenten) | 11 | 11 | 8 | 8 | 1 | 1 | `a7a868a` |
| ↳ Umweg: Kaution 0 € auf der Vertragsseite | 13 | 13 | 8 | 8 | 2 | 2 | `a7a868a` |

### Abweichungen gegenüber „nachher“

- **Buchungsanfrage → Vertrag, +1 Klick** (jetzt 4, Ziel aus Review 2 waren 3): Seit
  Review 3, Punkt 4 braucht eine neue Partei einen Namen und entweder die Anschrift oder den
  Haken „Anschrift folgt“. Die Anfrage der Website bringt keine Anschrift mit; der Haken ist
  der zusätzliche Klick. Name und E-Mail sind aus der Anfrage vorbelegt. Wer die Anschrift
  gleich eintippt, spart den Klick, hat aber Tippfelder mehr.
- **Website-Kündigung übernehmen, +1 Klick** (jetzt 3, Ziel 2): Im Testhof weicht der
  Absender der Anfrage vom Mieter ab. Seit Review 3, Punkt 9 bleibt „Kündigung anlegen“
  gesperrt, bis „Vollmacht liegt vor“ oder „Absender ist Vertragspartei“ gewählt ist. Diese
  Wahl ist der zusätzliche Klick. Stimmt der Absender mit dem Mieter überein, bleibt es bei 2.
- Alle übrigen Abläufe messen gleich. Kein Ablauf ist neu dazugekommen oder weggefallen; der
  Umweg von Hand bei der Website-Kündigung bleibt „entfällt“, weil der direkte Weg die
  Anfrage schon erledigt.

## Nach dem Apple-Rollout

Gemessen am 20.11.2026 nach PR G (Apple-Design als einziges Design, auf `781662d`) mit
`make klickmessung`, dieselben Skripte ohne Änderung: Apple-Rollout, unverändert. Alle neun
Messungen laufen durch, Klicks, Seitenwechsel und Tippfelder wie nach Review 3.

## Wie gezählt wird

- **Klick**: jedes `click`, `check` oder `select_option`. Eine Datei wählen zählt als ein
  Klick (der Knopf des Dateifelds). Tippen zählt nicht als Klick.
- **Seitenwechsel**: jede neue Seite im Hauptfenster nach der Startseite des Ablaufs, auch
  das Neuladen nach dem Absenden eines Formulars. HTMX-Antworten, die nur einen Teil der
  Seite ersetzen, zählen nicht.
- **Tippfelder**: Felder, in die man von Hand tippen muss.
- **Vorbelegung zählt nicht**: Steht in einer Auswahl schon der richtige Eintrag oder in
  einem Feld schon der richtige Wert (bei Beginn und Miete reicht ein Vorschlag), entfällt
  der Klick bzw. das Tippen. Dieselben Skripte messen deshalb nach dem Umbau ohne Änderung
  weniger; anpassen muss man nur die Selektoren.
- Navigiert wird nur mit Klicks, ab der Startseite des Ablaufs (meist die Arbeitslisten).
  Was man nur liest (Kontakt der Anfrage, Werte auf dem PDF), zählt nicht.
- Die Übergabe läuft bei 390 px, alles andere bei 1440 px.

## Die Abläufe heute

- **Umsatz einordnen** (`ablauf_umsatz_einordnen.py`): Umsatz „Material Baumarkt“ ohne Regel.
  Arbeitslisten → „Zuordnen“ → Kategorie „Material“ und Objekt „HOF“ wählen → „Buchen“. Die
  Kategorienliste zeigt alle 35 Kategorien, auch Einnahmen; nichts ist vorgeschlagen.
- **Klärfall zuordnen** (`ablauf_klaerfall_zuordnen.py`): Zweck nennt NW-S-0006, die
  Zahlerregel V-0010. Arbeitslisten → „Zuordnen“ → in der Liste mit 45 Verträgen (ohne Namen)
  V-0011 wählen → „Zuordnen“. Vorausgewählt ist der Vorschlag der Regel (V-0010).
- **Buchungsanfrage → Vertrag** (`ablauf_buchungsanfrage_vertrag.py`): Anfrage öffnen,
  Kontakt ablesen → Stammdaten → Parteien → Neue Partei (Vor- und Nachname tippen) → zwei
  Kontaktwege einzeln hinzufügen (E-Mail, Telefon) → zurück zur Anfrage → „Neuer Vertrag für
  NW-G-0004“ (Einheit vorbelegt; Mieter aus „Partei #…“ ohne Namen wählen, Beginn und
  Zielmiete tippen) → zurück zur Anfrage → Vertrag wählen → „Vertrag angelegt“.
- **Beleg erfassen** (`ablauf_beleg_erfassen.py`): Beleg aus dem Rechnungspostfach.
  Arbeitslisten → „Zuordnen“ → Lieferant, Objekt und Kostenart sind vorbelegt; Belegdatum,
  Betrag und Rechnungsnummer vom PDF abtippen → „Bestätigen“. Zusätzlich das Hochladen eines
  Belegs eines neuen Lieferanten (Datei, Lieferant, Datum, Betrag, Objekt, Kostenart,
  Leistungsjahr, Rechnungsnummer).
- **Website-Kündigung übernehmen** (`ablauf_website_kuendigung.py`): An der Anfrage gibt es
  keinen Weg, die Kündigung anzulegen; „Kündigung erfasst“ setzt nur die Anfrage auf
  erledigt (Review 2, A3). Der Umweg von Hand: Vertrag V-0010 aus der Anfrage öffnen, Zugang
  und Wunschtermin tippen, „Kündigung erfassen“, zurück zur Anfrage, „Kündigung erfasst“.
  Anfrage und Kündigung bleiben unverknüpft.
- **Übergabe ohne Kaution** (`ablauf_uebergabe_ohne_kaution.py`): Vertrag (Entwurf, Beginn
  heute) für eine freie Garage. Im Assistenten allein scheitert der Abschluss mit „Die
  Kaution ist nicht erfasst.“ (nach 9 Klicks und 7 Seitenwechseln): Der Assistent hat keinen
  Kautionsschritt und keine Wahl „keine Kaution vereinbart“ (Review 2, A10), und er verlinkt
  den Vertrag nicht. Kürzester Weg heute: vorher auf der Vertragsseite Kaution 0 € speichern.
  Der Abschluss verlangt außerdem ein Foto der Unterschrift.

## Beobachtungen beim Messen

- Review 2, A1 (Absturz bei Schritt „Fotos“ ohne Foto) tritt mit Chromium (Playwright) nicht
  auf: Der Schritt geht ohne Foto weiter. Der Absturz hängt vermutlich am Browser von Max
  (anderer Aufbau des leeren Mehrfach-Dateifelds).
- Der Assistent schlägt als Datum der Übergabe heute vor und lehnt den Abschluss ab, wenn
  der Vertrag später beginnt („Vertrag V-… beginnt erst später.“). Die Messung legt den
  Vertrag deshalb mit Beginn heute an.
- „Speichern und weiter“ in Schritt 1 braucht in Playwright einen Klick; die zwei Klicks aus
  A10 ließen sich hier nicht nachstellen. Die Warnung „Der Vertrag ist noch nicht
  unterschrieben.“ bleibt nach dem Anhaken stehen, bis die Seite neu lädt.
