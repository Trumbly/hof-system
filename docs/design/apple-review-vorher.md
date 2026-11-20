# Design-Review „vorher“: Startseite und Rahmen (Experiment Apple-Design)

Stand: `main` bei `bb14280`, bisherige Gestaltung (`werte.css`, `bausteine.css`).
Geprüft mit dem Skill `apple-design` (Apples Human Interface Guidelines als Markdown, lokal
unter `~/.claude/skills/apple-design/`, nicht im Repository). Die Befunde geben die
Richtlinien in eigenen Worten wieder; Quellen als `Datei › Abschnitt`.

Screenshots: `screens/vorher-start-{1440,390}-{hell,dunkel}.png`.

## Zusammenhang

- **Plattform:** Web-App (Django-Templates, HTMX, eigenes CSS) für Desktop-Browser und Handy.
  Keine iOS- oder macOS-App. Nach den Regeln des Skills gelten für eine Web-App die
  Grundsätze und die Grundlagen (Barrierefreiheit, Farbe, Schrift, Layout, Texte), die
  Plattform-Konventionen nur, wo sie im Browser Sinn ergeben.
- **Nutzer:** eine Person (Max), täglich, Desktop und Handy, Verwaltung mit Geld und Fristen.
- **Aufgabe der Startseite:** zeigen, was heute wartet, und mit einem Klick dorthin führen.
- **Geladene Seiten (13):** die Pflichtseiten `accessibility.md`, `color.md`,
  `typography.md`, `layout.md`, `designing-for-macos.md` und `cross-platform.md`, dazu für
  das, was zu sehen ist: `sidebars.md`, `search-fields.md`, `lists-and-tables.md`,
  `dark-mode.md`, `writing.md`, `branding.md`, `tab-bars.md`. `materials.md` kam für den
  Vorschlag in Befund 12 dazu.
- **Grenzen:** Kontrast aus den Hex-Werten in `werte.css` gerechnet (WCAG 2.1), Größen aus
  dem CSS und dem gerenderten DOM (Playwright). Nicht geprüft: Screenreader-Reihenfolge,
  Tastaturwege jenseits der Suche.

## Zusammenfassung

Bewertung: **Needs work.** Die Startseite ist sauber, ruhig und erfüllt den Kontrast nach
WCAG AA überall (Text 4,6:1 bis 16,7:1). Schwächen liegen in der Gewichtung: Das Hof-Grün
trägt fünf Bedeutungen, die Kacheln stehen alle gleich laut nebeneinander, und Schriftgrößen
in festen Pixeln skalieren nicht mit. Die Gestaltung ist ein gut gemachtes Werkzeug ohne
eigenes Gesicht; das einzige Markenelement ist das grüne „H“.

## Befunde, nach Wirkung

### 1. Das Grün bedeutet fünf Dinge zugleich (High)

- **Was:** `--primaer`/`--link`/`--ok` (#2b6a4c bzw. #1b6440) färben die Hauptaktion, alle
  Links, die aktive Navigation, den Status „erfolgreich“ und grüne Badges. In der Kachel
  „Läufe“ stehen 14 fette grüne „erfolgreich“; das Auge landet dort, wo nichts zu tun ist.
- **Quelle:** `color.md › Best practices`: dieselbe Farbe nicht für verschiedene Bedeutungen
  verwenden, gerade bei Status und Interaktivität. `branding.md › Best practices` (aus dem
  Handwerks-Blick des Skills): die Akzentfarbe sparsam, für Hauptaktionen und Status.
- **Vorschlag:** Grün nur für „klickbar/ausgewählt“; Erfolg leise (Grau mit grünem Punkt),
  Fehler laut.

### 2. Schriftgrößen in festen Pixeln (High)

- **Was:** Gruppentitel der Seitenleiste, Beschriftungen der Leiste unten, die Taste „⌘K“
  und die Navigationsmarke stehen auf `11px`. Sie wachsen nicht mit, wenn jemand im Browser
  eine größere Grundschrift einstellt; 11 px am Handy ist Apples Untergrenze, nicht der
  Regelfall.
- **Quelle:** `accessibility.md › Vision`: größere Textgrößen unterstützen, idealerweise bis
  200 %. `typography.md › Ensuring legibility`: Standard am Handy 17 pt, Minimum 11 pt.
  `cross-platform.md › Vocabulary`: Dynamic Type entspricht im Web Zoom und Schriftgröße.
- **Vorschlag:** alle Größen in `rem`, kleinste Stufe 0,75 rem (12 px).

### 3. Fließtext am Handy kleiner als der Plattform-Standard (High)

- **Was:** `body` hat am Handy 15 px (`--text-15`); iOS-Apps lesen sich mit 17 pt.
- **Quelle:** `typography.md › Ensuring legibility` (Tabelle Standard/Minimum) und
  `typography.md › Specifications › Large (default)`: Body 17 pt.
- **Vorschlag:** am Handy 1,0625 rem (17 px), am Desktop 0,9375 rem (15 px).

### 4. Alle Kacheln gleich laut, keine Rangfolge (High)

- **Was:** Vier Kennzahlen, dann zwölf Kacheln in gleicher Form; die längste (Läufe, 14
  Zeilen) ist die unwichtigste. Kacheltitel (15 px fett) und Fließtext (15 px) unterscheiden
  sich nur im Gewicht; zwischen Seitentitel (30 px) und Kacheltitel fehlt eine Stufe.
- **Quelle:** `layout.md › Visual hierarchy`: Inhalt nach Wichtigkeit ordnen, Verwandtes
  gruppieren. `typography.md › Conveying hierarchy`: Gewicht, Größe und Farbe für die
  Rangfolge nutzen.
- **Vorschlag:** klare Stufen (großer Titel 34, Kartentitel 17 halbfett, Zeilen 15,
  Fußnoten 13); Kennzahlen als „Zusammenfassung“ mit eigenem Gewicht; Erfolgsmeldungen leise.

### 5. Klickflächen am Desktop unter 44 px; Marke im Kopf am Handy 32 px (Medium)

- **Was:** Am Handy hebt `bausteine.css` Knöpfe auf 44 px; aber das „H“ im Kopf ist ein
  Link von 32 × 32 px. Am Desktop: Einträge der Seitenleiste 34 px, „Abmelden“ 28 px,
  Suchfeld 36 px.
- **Quelle:** `accessibility.md › Mobility`: Standard 44 × 44 pt auf iOS, 28 × 28 pt auf
  macOS; Abstand zwischen Zielen zählt wie die Größe. Nach Apple genügt der Desktop also;
  die Vorgabe von Max für dieses Experiment ist 44 px überall.
- **Vorschlag:** alle Ziele in Rahmen und Startseite mindestens 44 px.

### 6. Weiterführende Zeilen ohne Hinweis (Medium)

- **Was:** Zeilen wie „Umsätze einordnen · 50“ und alle Kacheln sind Links. Ob etwas
  weiterführt, sieht man nur am Hover, den es am Handy nicht gibt.
- **Quelle:** `lists-and-tables.md › Platform considerations › Mobile`: zum Hineingehen in
  eine Zeile den Disclosure Indicator nutzen. `lists-and-tables.md › Best practices`:
  Rückmeldung bei der Auswahl einer Zeile.
- **Vorschlag:** Pfeil „›“ am rechten Rand jeder weiterführenden Zeile und Kachel.

### 7. Dunkles Erscheinungsbild ohne Tiefe (Medium)

- **Was:** Karten (#191e1b) heben sich vom Grund (#121614) nur mit 1,08:1 ab; die Trennung
  leisten allein die Rahmenlinien.
- **Quelle:** `dark-mode.md › Platform considerations › Mobile`: zwei Ebenen, Grund dunkler,
  erhöhte Flächen heller. `dark-mode.md › Dark Mode colors`: dunkle Farben sind keine
  bloße Umkehrung der hellen.
- **Vorschlag:** Grund dunkler, Karten heller (mindestens 1,1:1 Abstand), Rahmen weg.

### 8. Rahmen um jede Fläche statt feiner Trennlinien (Medium)

- **Was:** Karten, Kennzahlen, Kacheln, Suchfeld und Navigation tragen je einen 1-px-Rahmen,
  dazu Linien unter jedem Kartenkopf. Die Seite wirkt dadurch kleinteilig.
- **Quelle:** `layout.md › Visual hierarchy`: Gruppen über Weißraum, Flächenform oder
  Trennlinien zeigen (eines davon genügt). `lists-and-tables.md › Style`: der gruppierte
  Stil trennt über Kopfzeilen und Abstand.
- **Vorschlag:** weiße Karten auf leicht grauem Grund ohne Rahmen; Haarlinien nur zwischen
  Zeilen, eingerückt.

### 9. Seitenleiste: letzter Eintrag abgeschnitten, kein Hinweis aufs Scrollen (Medium)

- **Was:** Bei 1440 × 900 endet die sichtbare Navigation mitten in „Läufe“; nichts zeigt, dass
  sie scrollt.
- **Quelle:** `layout.md › Visual hierarchy`: Bedienelemente mit einem Kanteneffekt beim
  Scrollen vom Inhalt abheben. `sidebars.md › Best practices`: Gruppen mit kurzen Titeln,
  höchstens zwei Ebenen (hier eingehalten).
- **Vorschlag:** unten weich ausblenden, Platz zum Ausrollen lassen.

### 10. Erhöhter Kontrast und reduzierte Bewegung ohne Antwort (Medium)

- **Was:** Es gibt Übergänge (0,12 s) und keine Regel für `prefers-reduced-motion`,
  `prefers-contrast` oder `prefers-reduced-transparency`.
- **Quelle:** `accessibility.md › Cognitive`: auf „Bewegung reduzieren“ reagieren.
  `color.md › Best practices`: Farben müssen auch bei erhöhtem Kontrast funktionieren.
  `dark-mode.md › Best practices`: mit erhöhtem Kontrast und reduzierter Transparenz testen.
- **Vorschlag:** alle drei Medienabfragen bedienen.

### 11. Suchfeld sagt nicht, wonach man suchen kann (Medium)

- **Was:** Der Platzhalter lautet „Suchen …“; was gesucht wird (NW-ID, Vertrag, Name, Betrag)
  steht nur im unsichtbaren Label.
- **Quelle:** `search-fields.md › Best practices`: Platzhalter nutzen, um zu zeigen, was
  sich suchen lässt. Positiv: Die Suche startet beim Tippen, wie empfohlen.
- **Vorschlag:** Platzhalter „NW-ID, Vertrag …“.

### 12. Kopf und Leiste am Handy ohne Ebene (Low)

- **Was:** Kopfzeile und Leiste unten sind deckend weiß mit Linie; der Inhalt läuft hart
  darunter. Bedienung und Inhalt sind nur durch die Linie getrennt.
- **Quelle:** `materials.md › Liquid Glass`: Bedienelemente bilden eine eigene Ebene über
  dem Inhalt, Material nur dort, nie im Inhalt.
- **Vorschlag:** halb deckendes Material mit Unschärfe für Seitenleiste, Kopf und Leiste
  unten; deckend bei reduzierter Transparenz.

### 13. Uneinheitliche Kacheltitel (Low)

- **Was:** „Belege zuordnen: 1“ und „Mahnfälle: 0“ (Zahl im Titel), „Offene Freigaben“ mit
  Zähler-Kapsel, „Rückstände“ mit Zahl in der nächsten Zeile.
- **Quelle:** `writing.md › Best practices`: Sprachmuster aufbauen und durchhalten.
- **Vorschlag:** ein Muster (Titel ohne Zahl, Zahl als Kapsel oder groß darunter). Die Titel
  stehen in den Vorlagen der Apps; das gehört in die nächste Runde.

### 14. Testhof-Band in der Farbe der Warnungen (Low)

- **Was:** Das Band nutzt dasselbe Bernstein wie die Warn-Badges. Es ist ein Hinweis zur
  Umgebung, keine Warnung zu Daten.
- **Quelle:** `color.md › Best practices`: eine Farbe, eine Bedeutung.
- **Vorschlag:** Farbe behalten (sie fällt auf, und Testhof soll auffallen), aber als ruhiges,
  schmales Band mit Punkt; in Worten steht ohnehin „Testhof“.

### 15. Kein Gesicht (Craft-Hinweis, Low)

- **Was:** Ruhig und dicht, aber austauschbar; man erinnert sich an nichts außer dem „H“.
- **Quelle:** `SKILL.md › Lens 3` (Handwerks-Blick, Urteil des Skills, keine HIG-Seite):
  ein Element mit Gewicht, alles andere ruhig.
- **Vorschlag:** Die Zusammenfassung oben (vier Kennzahlen) bekommt das Gewicht, das Grün
  bleibt der Hauptaktion und der Auswahl vorbehalten.

## Was gut ist (bleibt)

- Kontrast nach WCAG AA durchgehend, gerechnet und im CSS dokumentiert.
- Seitenleiste mit fünf Gruppen und zwei Ebenen (`sidebars.md › Best practices`).
- Suche oben in der Seitenleiste, mit ⌘K und Treffern beim Tippen
  (`search-fields.md › Platform considerations › Tablet and desktop`).
- Am Handy eine Leiste unten mit Beschriftungen, am Desktop die Seitenleiste: dieselben
  Funktionen bei jeder Breite (`layout.md › Size classes`).
- Ziffern in gleicher Breite (`tabular-nums`) in allen Zahlenspalten.

## Nicht übernommen (mit Begründung)

| Vorgabe aus dem Skill | Quelle | Warum nicht |
| --- | --- | --- |
| Jeder Befehl in der Menüleiste | `designing-for-macos.md › Best practices` | Eine Web-App hat keine Menüleiste; ⌘K und die Navigation übernehmen die Rolle. |
| Keine eigene Einstellung für das Erscheinungsbild | `dark-mode.md › Best practices` | Eingehalten: hell/dunkel folgt dem System. Der Designschalter des Experiments wählt keine Helligkeit, sondern eine Gestaltung, und es gibt ihn nur in dev. |
| Tab-Leiste ohne „Mehr“ | `tab-bars.md › Best practices` | 18 Bereiche passen nicht in fünf Tabs; „Mehr“ öffnet alle Gruppen. |
| Gefüllte SF Symbols in der Tab-Leiste | `tab-bars.md › Best practices`, `sidebars.md › Best practices` | Apples Lizenz für SF Symbols erlaubt sie nur in Apps für Apple-Plattformen (eigenes Wissen, steht nicht im Skill); das System bleibt bei seinen eigenen Strich-Symbolen. Die aktive Seite bekommt eine stärkere Linie. |
| Schrift SF Pro einbinden | `typography.md › Using system fonts` | Keine Schriftdateien (Vorgabe von Max). Der System-Schriftstapel nimmt auf Apple-Geräten SF, sonst Segoe UI oder Roboto. |
| Systemfarben (Systemblau) | `color.md › System colors` | Akzent bleibt das Hof-Grün; Apple rät ohnehin, Systemfarbwerte nicht fest einzutragen. |
| Seitenleiste ein- und ausblendbar, anpassbar | `sidebars.md › Best practices` | Braucht Logik und Speicher je Benutzer; nicht Teil eines reinen Gestaltungsexperiments. |
| Gruppen der Seitenleiste als Aufklapper | `sidebars.md › Best practices` | Kostet Klicks (Klickmessung aus Review 2). |
| Suche am Handy unten | `search-fields.md › Platform considerations › Phone` | Unten liegt die Tab-Leiste; die Suche bleibt oben im Kopf, wo sie heute schon gefunden wird. |
| Desktop-Ziele 28 × 28 pt | `accessibility.md › Mobility` | Max verlangt 44 px; das ist strenger als Apple und wird umgesetzt. |
| Titelschreibung (Title Case) | `lists-and-tables.md › Content`, `writing.md › Best practices` | Gilt fürs Englische; im Deutschen gilt die normale Rechtschreibung. |
