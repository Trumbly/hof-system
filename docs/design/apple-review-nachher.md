# Design-Review „nachher“: Startseite und Rahmen im Apple-Design

Stand: Branch `experiment-apple-design`, fortgeschrieben in PR A (`apple-a-grundlage`) und
nach dem Rollout (PR G). Gestaltung unter `static/css/apple/` über `werte.css` und
`bausteine.css` (`basis.css` mit Werten, Rahmen und Startseite, `bausteine.css` mit allen
Bausteinen, dazu je Bereich eine Datei).
Geprüft mit dem Skill `apple-design` an denselben Stellen wie in
[apple-review-vorher.md](apple-review-vorher.md). Quellen als `Datei › Abschnitt`, Inhalt in
eigenen Worten.

**Seit PR G (Rollout abgeschlossen)** ist das Apple-Design die einzige Gestaltung, auch in
prod. Schalter, `?design=`, Cookie und die Einstellung `HOF_DESIGN_STANDARD` sind weg; Stand
und offene Punkte im Abschnitt [Stand nach dem Rollout](#stand-nach-dem-rollout) am Ende.

**Screenshots** (`screens/`, Playwright, Chromium):

| | hell | dunkel |
| --- | --- | --- |
| Startseite vorher, 1440 px | `vorher-start-1440-hell.png` | `vorher-start-1440-dunkel.png` |
| Startseite vorher, 390 px | `vorher-start-390-hell.png` | `vorher-start-390-dunkel.png` |
| Startseite nachher, 1440 px | `nachher-start-1440-hell.png` | `nachher-start-1440-dunkel.png` |
| Startseite nachher, 390 px | `nachher-start-390-hell.png` | `nachher-start-390-dunkel.png` |
| Arbeitslisten (geerbte Werte), 1440 px | `apple-arbeitslisten-1440-hell.png` | `apple-arbeitslisten-1440-dunkel.png` |
| Arbeitslisten (geerbte Werte), 390 px | `apple-arbeitslisten-390-hell.png` | `apple-arbeitslisten-390-dunkel.png` |

Ganze Seiten; die Arbeitslisten bei 1440 px nur die ersten 2000 px. Bei 390 px steht die
feste Leiste unten in der Bildmitte, weil Playwright die ganze Seite in einem Bild aufnimmt.

**Geladene Seiten (12):** `accessibility.md`, `color.md`, `typography.md`, `layout.md`,
`cross-platform.md`, `materials.md`, `dark-mode.md`, `motion.md`, `sidebars.md`,
`lists-and-tables.md`, `search-fields.md`, `branding.md`.

## Zusammenfassung

Bewertung: **Good.** Kein Befund mehr über „Medium“ in Rahmen und Startseite. Die Seite hat
jetzt eine Rangfolge: großer Titel, eine Zusammenfassung aus vier Kennzahlen mit Gewicht,
darunter ruhige Karten ohne Rahmen. Grün heißt nur noch „klickbar oder ausgewählt“. Das
Gesicht der Seite ist die Zusammenfassung oben; alles darunter tritt zurück.

## Token-System

**Farben** (hell / dunkel). Kontrast nach WCAG 2.1, aus den Hex-Werten gerechnet
(`tmp/kontrast.py`, nicht im Repo; Formel der relativen Leuchtdichte aus WCAG 2.1).

| Rolle | hell | dunkel |
| --- | --- | --- |
| Grund (gruppiert) | `#f2f3f0` | `#0d0f0e` |
| Fläche (Karten) | `#ffffff` | `#1a1d1b` |
| Text | `#1b1e1c` | `#f3f5f4` |
| Text 2 (Nebenzeilen) | `#5a605c` | `#b0b7b3` |
| Text 3 (Hilfetexte) | `#6a706c` | `#959c98` |
| Akzent Hof-Grün (Hauptaktion, Links, Auswahl) | `#217a4f` | `#3fbf7f` |
| Auswahl (Seitenleiste) | `#1a6641` auf `#e1f0e7` | `#8fdcb3` auf `#173524` |
| Material (Bedienebene) | `rgb(248 249 247 / 85 %)` | `rgb(29 32 30 / 85 %)` |
| Testhof-Band | `#7f4600` auf `#fcefd6` | `#f2bc66` auf `#33250e` |
| Statusfarben | aus `werte.css` übernommen | aus `werte.css` übernommen |

**Schrift:** nur der System-Schriftstapel `-apple-system, BlinkMacSystemFont, "SF Pro Text",
"SF Pro Display", system-ui, "Segoe UI", Roboto, sans-serif`, keine Schriftdateien. Stufen in
rem nach den iOS-Textstilen (`typography.md › Specifications`): großer Titel 2,125 rem (34),
Kopfzeile 1,0625 rem (17, halbfett), Fließtext 0,9375 rem am Desktop und 1,0625 rem am Handy,
Fußnote 0,8125 rem (13), kleinste Stufe 0,75 rem (12).

**Formen:** Karten 14 px Radius ohne Rahmen, Felder 10 px, Knöpfe als Kapsel, Seitenleiste
als schwebende Fläche mit 18 px Radius.

**Bewegung:** nur Farbübergänge von 0,15 s beim Hover; bei `prefers-reduced-motion` keine.

## Kontrast nachher (WCAG AA: Text 4,5:1, Bedienelemente und Rahmen 3:1)

| Paar | hell | dunkel |
| --- | --- | --- |
| Text auf Karte | 16,81:1 | 15,52:1 |
| Text auf Grund | 15,09:1 | 17,57:1 |
| Text 2 auf Karte | 6,44:1 | 8,31:1 |
| Text 2 auf Grund | 5,78:1 | 9,41:1 |
| Text 2 im Suchfeld (Platzhalter) | 5,36:1 | 6,81:1 |
| Text 3 auf Karte | 5,06:1 | 6,06:1 |
| Text 3 auf Grund | 4,55:1 | 6,86:1 |
| Text 3 auf Hover-Fläche | 4,63:1 | 5,45:1 |
| Link auf Karte | 5,30:1 | 9,05:1 |
| Link auf Grund | 4,76:1 | 10,24:1 |
| Schrift auf Hauptknopf | 5,30:1 (Weiß auf Grün) | 8,09:1 (Dunkel auf Grün) |
| Auswahl in der Seitenleiste | 5,90:1 | 8,31:1 |
| Testhof-Band | 6,64:1 | 8,61:1 |
| Feldrahmen (`--linie-stark`) auf Karte | 3,41:1 | 3,69:1 |
| Pfeil „›“ (Text 3) auf Karte | 5,06:1 | 6,06:1 |
| Fehlerrot auf Karte (Kennzahl, Status) | 7,19:1 | 8,51:1 |
| Text 2 auf Material, Grund darunter | 6,04:1 | 8,29:1 |
| Link auf Material (Seitenleiste) | 4,98:1 | 9,03:1 |
| Text 2 auf Material, ungünstigster Fall* | 4,50:1 | 5,13:1 |
| aktiver Tab auf Material, ungünstigster Fall* | 4,86:1 | 5,21:1 |

\* Das Material deckt zu 85 %. Gerechnet ist der Fall, dass unter der Leiste eine volle
Fläche in Textfarbe liegt; die Unschärfe mischt in Wirklichkeit hellere Umgebung dazu. Mit
72 % Deckkraft fiel der Wert auf 3,9:1; deshalb das dickere Material
(`materials.md › Standard materials`: dickere Materialien geben feinem Text mehr Kontrast).

Trennung der Ebenen im Dunkeln: Karte zu Grund 1,13:1 (vorher 1,08:1), ohne Rahmenlinie.

## Abgleich mit dem Review vorher

Stand der 15 Befunde von vorher; das sind keine neuen Befunde.

| # | Befund vorher | nachher | Quelle |
| --- | --- | --- | --- |
| 1 | Grün mit fünf Bedeutungen | Erledigt. Grün nur für Hauptaktion, Links, Auswahl. „erfolgreich“ ist grau mit grünem Punkt, „übersprungen“ grau mit leerem Kreis, Fehler rot und fett. Status hat damit Form und Wort, nicht nur Farbe. | `color.md › Best practices`, `color.md › Inclusive color`, `branding.md › Best practices` |
| 2 | Schrift in festen Pixeln | Erledigt für Rahmen und Startseite: alle Größen in rem, kleinste 0,75 rem. | `accessibility.md › Vision` |
| 3 | Fließtext am Handy 15 px | Erledigt: 17 px am Handy, 15 px am Desktop. | `typography.md › Ensuring legibility` |
| 4 | Keine Rangfolge | Erledigt: Stufen 34 / 28 (Kennzahl) / 17 / 15 / 13. Kennzahlen mit farbigem Titel und großer Zahl als Zusammenfassung. Die Reihenfolge der Kacheln kommt aus dem Register der Apps und bleibt. | `typography.md › Conveying hierarchy`, `layout.md › Visual hierarchy` |
| 5 | Ziele unter 44 px | Erledigt: Suchfeld, Abmelden, Marke, Tabs, Listenzeilen, Kartenlinks, Reiter, Felder und Knöpfe mindestens 44 px, bei 390 und 1440 px (E2E-Prüfung). Ausnahme seit PR A: Zeilen der Seitenleiste mit Maus 36 px (Befund D). | `accessibility.md › Mobility` |
| 6 | Weiterführende Zeilen ohne Hinweis | Erledigt: Pfeil „›“ an jeder Link-Zeile und Kachel. | `lists-and-tables.md › Platform considerations › Mobile` |
| 7 | Dunkel ohne Tiefe | Erledigt: Grund dunkler, Karten heller, keine Rahmen. | `dark-mode.md › Platform considerations › Mobile` |
| 8 | Rahmen überall | Erledigt: Karten ohne Rahmen, Haarlinien nur zwischen Zeilen und eingerückt wie in einer gruppierten Liste. | `lists-and-tables.md › Style` |
| 9 | Seitenleiste abgeschnitten | Erledigt: Die Navigation blendet unten weich aus und hat Platz zum Ausrollen. | `layout.md › Visual hierarchy` |
| 10 | Kontrast-, Bewegungs-, Transparenzwunsch | Erledigt: `prefers-reduced-motion` (keine Übergänge), `prefers-reduced-transparency` (deckende Flächen), `prefers-contrast: more` (dunklere Linien, Nebentexte in Textfarbe, kein Material); Browser ohne `backdrop-filter` bekommen deckende Flächen. | `accessibility.md › Cognitive`, `color.md › Best practices`, `dark-mode.md › Best practices` |
| 11 | Platzhalter der Suche | Erledigt: „NW-ID, Vertrag …“; das Label nennt weiter alles. | `search-fields.md › Best practices` |
| 12 | Kopf und Leiste ohne Ebene | Erledigt: Material (85 % deckend, Unschärfe 20 px) in Seitenleiste, Kopf und Tab-Leiste, nie im Inhalt. | `materials.md › Liquid Glass` |
| 13 | Uneinheitliche Kacheltitel | Offen. Die Titel stehen in den Kachel-Vorlagen der Apps; im CSS sind sie nur gleich gesetzt (17 px halbfett). | `writing.md › Best practices` |
| 14 | Testhof-Band in Warnfarbe | Teilweise: Farbe bleibt bewusst (Testhof soll auffallen), jetzt schmaler, mit Punkt, ruhiger. | `color.md › Best practices` |
| 15 | Kein Gesicht | Besser: Die Zusammenfassung oben trägt das Gewicht, das Grün ist knapp. Ein eigenes Markenelement über das „H“ hinaus gibt es weiter nicht; für ein Werkzeug, das täglich benutzt wird, halte ich das für richtig. | `branding.md › Best practices` |

## Befunde nachher, nach Wirkung

Sechs Befunde, keiner über „Medium“. E und F sind die offenen Reste von vorher (13 und 14).

### A. Material in der Seitenleiste ohne Inhalt darunter (Medium)

- **Was:** Die Seitenleiste ist eine eigene Spalte; der Inhalt läuft nicht unter ihr durch.
  Das Material zeigt deshalb nur den Grund, die Unschärfe wirkt nur am Handy (Kopf und
  Tab-Leiste über scrollendem Inhalt).
- **Quelle:** `sidebars.md › Best practices`: reichen Inhalt unter die Seitenleiste ziehen,
  damit die Trennung sichtbar wird. `materials.md › Liquid Glass`: Material sparsam.
- **Einschätzung:** Für eine Verwaltung ohne Bilder ist das richtig so. Die Seitenleiste
  wirkt durch Schatten und Radius schwebend; mehr Glas brächte nichts.

### B. Leere Fläche über „Objekte“ bei 1440 px (Medium)

- **Was:** Das Mauerwerk der Kacheln endet in der Mitte früher als rechts; unter ihm bleibt
  bis zur Karte „Objekte“ eine Lücke. Das war vorher genauso.
- **Quelle:** `layout.md › Visual hierarchy` (Gruppen und Weißraum).
- **Vorschlag:** „Objekte“ in die Kacheln aufnehmen oder ganz nach unten in eine eigene
  Zeile; das ist Logik der Startseite, nicht Gestaltung.

### C. Lange Laufnamen brechen um (Low)

- **Was:** „statusmcp_aktualisieren:“ bricht in der Läufe-Kachel vor dem Doppelpunkt um.
- **Quelle:** `lists-and-tables.md › Content`: Zeilentext kurz halten.
- **Vorschlag:** Anzeigenamen der Läufe statt der Befehlsnamen (Vorlage der Kachel).

### D. Seitenleiste bei 900 px Höhe (erledigt in PR A)

- **Was (Experiment):** Mit 44-px-Zeilen passten bei 1440 × 900 nicht alle Einträge;
  „Schreiben“ und die Gruppe „System“ lagen unter der Blende.
- **Entscheidung von Max (PR A):** Mit Maus (`pointer: fine`) sind die Zeilen der
  Seitenleiste 36 px hoch; mit Finger (`pointer: coarse`) und am Handy bleiben es 44 px.
  Knöpfe und alle übrigen Klickflächen bleiben überall mindestens 44 px.
- **Umsetzung:** Damit bei 1440 × 900 alles sichtbar ist (auch mit Testhof-Band), steht am
  Desktop das „H“ neben der Suche statt einer eigenen Zeile mit dem Schriftzug, „Abmelden“
  steht rechts in der letzten Zeile (bis PR G teilte es sie mit dem Designschalter), das Konto
  erreicht man über „Konto“ in der Navigation, und das Testhof-Band liegt nur über der
  Inhaltsspalte. Gemessen: Navigation 765 px Inhalt bei 765 px Platz, kein Scrollen
  (E2E: `test_seitenleiste_passt_bei_1440_mal_900`).
- **Quelle:** `accessibility.md › Mobility` (Apple nennt am Desktop 28 pt als Standard, also
  liegen 36 px darüber). `branding.md › Best practices`: das Logo nicht auf jeder Seite
  wiederholen. `sidebars.md › Platform considerations › Desktop`: keine kritischen Aktionen
  am unteren Rand (Abmelden und der Schalter sind es nicht).

### E. Uneinheitliche Kacheltitel (Low, offen aus vorher 13)

- **Was:** „Belege zuordnen: 1“ neben „Rückstände“ mit Zahl darunter und „Offene Freigaben“
  mit Kapsel. Das CSS setzt alle gleich groß, die Wörter bleiben verschieden.
- **Quelle:** `writing.md › Best practices`: ein Sprachmuster, durchgehalten.
- **Vorschlag:** beim Ausrollen die Kachel-Vorlagen der Apps auf ein Muster bringen.

### F. Testhof-Band in Bernstein (Low, teilweise aus vorher 14)

- **Was:** Das Band teilt die Farbe mit den Warnungen, ist aber jetzt schmal und mit Punkt.
- **Quelle:** `color.md › Best practices`: eine Farbe, eine Bedeutung.
- **Einschätzung:** bewusst so gelassen; der Testhof soll auffallen, und das Wort steht dabei.

## Was gut ist (bleibt)

- Zusammenfassung oben mit Statusfarbe im Titel und in der Zahl, Wort und Farbe zusammen.
- Gruppierte Listen mit eingerückten Haarlinien und Pfeil, Zeilen 44 px.
- Grün nur dort, wo man klicken kann oder etwas ausgewählt ist.
- Hell und dunkel aus `prefers-color-scheme`, beide gerechnet.

## Plattform-Hinweise

- Web-App, kein natives Apple-Programm: übernommen sind Grundsätze und Grundlagen, von den
  Plattform-Mustern nur gruppierte Listen, Disclosure-Pfeile, Material für die Bedienebene,
  Segmented Control für den Schalter und die Tab-Leiste am Handy (gab es schon).
- `backdrop-filter` braucht in Safari das Präfix `-webkit-`; beides ist gesetzt.
- Die übrigen Seiten erben Farben, Schrift, Radien, Kapselknöpfe und Material, aber nicht die
  44-px-Ziele am Desktop und nicht die gruppierten Listen (siehe Arbeitslisten).

## Nicht übernommen (mit Begründung)

| Vorgabe aus dem Skill | Quelle | Warum nicht |
| --- | --- | --- |
| Jeder Befehl in der Menüleiste | `designing-for-macos.md › Best practices` | Eine Web-App hat keine Menüleiste; ⌘K und die Navigation übernehmen die Rolle. |
| Keine eigene Einstellung für das Erscheinungsbild | `dark-mode.md › Best practices` | Eingehalten: hell/dunkel folgt dem System. Den Designschalter des Experiments gibt es seit PR G nicht mehr. |
| Liquid Glass mit Lichtbrechung, klare Variante | `materials.md › Liquid Glass` | Im Browser gibt es nur Unschärfe und Deckkraft. Die „klare“ Variante ist für Bilder und Video gedacht, die es hier nicht gibt. |
| Inhalt unter die Seitenleiste ziehen | `sidebars.md › Best practices` | Kein bildreicher Inhalt; siehe Befund A. |
| Tab-Leiste ohne „Mehr“ | `tab-bars.md › Best practices` | 18 Bereiche passen nicht in fünf Tabs. |
| Gefüllte SF Symbols | `tab-bars.md › Best practices` | Apples Lizenz für SF Symbols erlaubt sie nur in Apps für Apple-Plattformen (eigenes Wissen, steht nicht im Skill). Eigene Strich-Symbole, aktive Seite mit stärkerer Linie. |
| SF Pro als Schriftdatei | `typography.md › Using system fonts` | Keine Schriftdateien; der Systemstapel nimmt auf Apple-Geräten ohnehin SF. |
| Systemblau als Akzent | `color.md › System colors` | Akzent bleibt das Hof-Grün. |
| Seitenleiste ein- und ausblendbar | `sidebars.md › Best practices` | Braucht Logik und eine Einstellung je Benutzer. |
| Gruppen der Seitenleiste als Aufklapper | `sidebars.md › Best practices` | Kostet Klicks (Klickmessung aus Review 2). |
| Suche am Handy unten | `search-fields.md › Platform considerations › Phone` | Unten liegt die Tab-Leiste; die Suche bleibt oben. |
| Desktop-Ziele 28 pt | `accessibility.md › Mobility` | Vorgabe von Max: 44 px, nur die Seitenleiste mit Maus 36 px. Siehe Befund D. |
| Abwechselnde Zeilenfarben in Tabellen | `lists-and-tables.md › Platform considerations › Desktop` | Auf der Startseite gibt es keine mehrspaltige Tabelle; Thema für die übrigen Seiten. |
| Titelschreibung (Title Case) | `lists-and-tables.md › Content`, `writing.md › Best practices` | Gilt fürs Englische. |

## Stand nach dem Rollout

Stand: PR G (`apple-g-abschluss`) auf `main` nach #43 bis #48.

### Bereiche

Alle Seiten mit `base.html` tragen das Apple-Design, in dev, Tests und prod:

| PR | Bereich | Bereichsdatei |
| --- | --- | --- |
| #43 (A) | Grundlage: Werte, Rahmen, Startseite, alle Bausteine, `/stil/` | `apple/basis.css`, `apple/bausteine.css` |
| #45 (B) | Arbeitslisten und Posteingang | `apple/arbeitslisten.css` |
| #48 (C) | Stammdaten: Übersicht, Vertrag, Einheit, Partei, Leerstand, Anfragen, Übergabe | `apple/stammdaten.css` |
| #47 (D) | Geld: Bank, Kassenbuch, Offene Posten, Abgleich, Mahnwesen | `apple/geld.css` |
| #46 (E) | Dokumente: Ablage, Belege, Rechnungen, Lieferanten, Vorlagen, Schreiben, Serie | `apple/dokumente.css` |
| #44 (F) | System: Zähler, Fristen, Läufe, Protokoll, Anbindungen, Konto, Fehlerseiten | `apple/system.css` |

Fehlerseiten (`_fehlerseite.html`), PDF- und Mail-Vorlagen haben ihr eigenes Layout und laden
keine Stylesheets; sie sind unverändert.

### Was PR G entfernt hat

- **Schalter:** `apps/kern/design.py` (Middleware, Context Processor), die Vorlage
  `bausteine/design_schalter.html`, die Einstellung `HOF_DESIGN_STANDARD` in allen
  Umgebungen, das Cookie `hof_design`, `?design=` und alle Verzweigungen in `base.html`,
  Suche und `/stil/`. `<html>` trägt `data-design="apple"` fest: Jede Regel unter
  `static/css/apple/` beginnt damit und steht so eine Stufe Spezifität über der Grundlage.
- **`hof.css`** (797 Zeilen, Ebene `@layer alt`): Sie galt nur für Seiten ohne Seitenkopf,
  und die gibt es nicht mehr (Abdeckung: 243 Seiten im Testhof durchlaufen, alle mit
  Seitenkopf; statisch geprüft über alle Vorlagen).
- **`werte.css`:** die Werte, die `apple/basis.css` ohnehin setzt (Flächen, Linien, Text,
  Akzent, Fokus, Band, Schrift, Radien, Schleier, Breite der Leiste, `color-scheme`). Es
  bleiben Statusfarben, Gefahr, Schriftstufen der Grundlage, Abstände, Ziele, Schatten der
  Dialoge und Menüs und das Gerüst.
- **`bausteine.css` (Grundlage):** die Klassen, die keine Vorlage, kein Python und kein
  JavaScript mehr nutzt: `.design-schalter`, `.kopf-fakten`, `.raster-3`, `.symbol-klein`.
  Alle übrigen Klassen der Grundlage nutzt mindestens eine Vorlage; sie trägt den Aufbau
  (Raster, Abstände, Verhalten am Handy), über den die Apple-Dateien das Aussehen legen.
- **`apple/basis.css`:** Regeln des Designschalters; die Seitenleiste hat unten nur noch
  „Abmelden“.

### Bausteine aus den Bereichen (jetzt in `apple/bausteine.css`, Beispiele auf `/stil/`)

| Baustein | vorher | jetzt |
| --- | --- | --- |
| Faktenkopf | `.fakten-kacheln` (Stammdaten), `.geld-fakten` (Geld), `#zaehler-kopf .fakten` und `#vorgang-fakten` (System) | `dl.fakten`: eine Karte ohne Rahmen und Trennlinien; am Handy zwei Spalten, am Desktop eine Zeile mit Breite nach Inhalt |
| Pfad über dem Titel | `.kopf-pfad` (Stammdaten) | `.kopf-pfad` |
| Abschnittstitel | `h2.abschnitt-titel` (Geld) | `h2.abschnitt-titel` |
| Reiter unter dem Seitenkopf | `.seitenkopf + .dok-reiter` (Dokumente), Geld und System ohne Regel | `.seitenkopf + .reiter`: 16 px darüber, 24 px darunter, überall |
| Filterleiste | `.filterleiste > .feld` überschrieb `.feld-breit` (Fehler); Raster nur im Protokoll | Fehler behoben in der Grundlage; schmale Felder (9 rem), Suche breit (16 rem, wächst dreifach), Auswahltexte mit „…“; die Sonderlösung im Protokoll ist weg |
| Raster am Handy | `.anfrage-raster` (Stammdaten) | `.raster` mit `minmax(0, 1fr)` und `min-width: 0` an den Kindern, in der Grundlage |
| Wahl-Karten auf einer Karte | Arbeitslisten | `.karte .wahl-karte`: grau statt Schatten, Vorschlag weiß mit Ring |
| Karte ohne Kopf | `form.karte` (Arbeitslisten) | `form.karte > .karte-rumpf:first-child` |
| Liste bis an den Kartenrand | `.dok-liste-buendig` (Dokumente), `.hindernisse.liste` (Stammdaten), `#ruecknahme .liste` (System) | `.liste-buendig` |
| Eingerückte Haarlinie | je eigene Regel in Arbeitslisten (Posten) und Stammdaten (Checkpunkte) | `.zeilen-linien` mit `--linie-einzug` |
| `fieldset` als Karte | `.dok-teil` (Dokumente), `fieldset.teil` mit `.teil-titel` (Geld) | `fieldset.karte` mit `legend.karte-titel` |
| Quelltext | `.dok-quelltext` (Dokumente) | `.quelltext` |
| Zelle mit Nebenzeile | `.frist-zelle` (System) | `.zelle-neben`, am Handy rechtsbündig |
| Gekürzter langer Text | `.lauf-meldung-lang` (System) | `.gekuerzt` mit „ganze Meldung“ |
| Rahmen ohne Seitenleiste | `system.css` | `apple/basis.css` |
| Checkliste | Stammdaten | `.checkliste`, `.checkpunkte`, `button.haken` |
| Badge in Angaben | Stammdaten | `.angaben-neu dd .badge` bricht um |
| Abstände in Karten, Beträge | `geld.css` (wirkten schon überall) | `apple/bausteine.css` |

Dazu: Datums- und Monatsfelder sind so hoch wie die übrigen Felder (Chromium legte innen
2 px dazu; nebeneinander in der Filterleiste standen sie sonst versetzt).

### Bewusst im Bereich geblieben

- **Graue Einlage in der Posten-Zeile** (`.posten-arbeit`, Arbeitslisten): Nur die
  Arbeitslisten haben eine Arbeitsfläche in einer Listenzeile.
- **Navigationsliste am Handy** (Arten der Arbeitslisten): Nur dort wird eine Seitenleiste
  am Handy zur Liste mit Pfeil; andere Bereiche nutzen dafür Reiter.
- Spaltenbreiten einzelner Tabellen (Fristen, Läufe, Konten, Buchungen, Regeln), Kassenbuch-
  und Abgleich-Formulare, Vorlagen-Raster, Anbindungen, Konto-Seiten.

### Kleinigkeiten

- `|euro` zeigt negative Beträge mit Minuszeichen (U+2212): „−36,50 €“
  (`apps.kern.zahlen.euro_anzeige`). Schreiben, Mails und Notion (Platzhalter) behalten den
  Bindestrich; CSV und Exporte nutzen `|euro` nicht.
- Am aktiven Vertrag ist die Hauptaktion „Schreiben erstellen“; „Rückgabe am Handy“ steht
  unter „mehr ▾“. So gelassen, zur Entscheidung bei Max.

### Offen

- Befund B (Lücke über „Objekte“), C (lange Laufnamen in der Kachel), E (uneinheitliche
  Kacheltitel) und F (Testhof-Band) wie oben; das ist Logik der Startseite bzw. bewusst so.
- Die Grundlage (`bausteine.css`) trägt weiter Eigenschaften, die die Apple-Dateien
  überschreiben (Farben, Radien, Schriftgrößen einzelner Bausteine). Nach Klassen ist
  nichts mehr tot; die Grundlage und `apple/bausteine.css` zu einer Datei zu verschmelzen
  wäre der nächste Schritt, aber ein großer Umbau ohne sichtbaren Gewinn.
- Am Handy stehen in der Filterleiste zwei Felder nebeneinander; lange Auswahltexte
  („offen und teilweise bezahlt“) enden dort mit „…“.
