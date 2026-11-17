# Hof-System – Regeln für Claude Code

Dieses Repository enthält das Hof-System des Hofs Noelle-Wying: eine Django-Anwendung, die Garagen, Stellplätze, Lager, Hallen, Außenflächen, Bank, Kassenbuch, Dokumente und Fristen führt. Wohnraum führt Verwaltio, nicht dieses System.

Grundlage sind zwei Dokumente im Ordner `docs/`:

- `docs/bauplan.md`: Technik, Datenmodell, Dienste, zwölf Bauschritte mit Abnahmekriterien, Testdaten, Umzug
- `docs/prozessliste.md`: alle Abläufe mit Nummern (H1.1, H3.5 …) und die zehn Standardabläufe

Lies vor jeder Aufgabe den Abschnitt des Bauplans, um den es geht. Wenn Bauplan und Aufgabe sich widersprechen, frag nach, statt zu raten.

## 1. KI-Grenze (nicht verhandelbar)

Das System arbeitet mit Daten von Mietern, Pächtern und Zahlern. Claude arbeitet nur mit Code und erfundenen Testdaten.

- **Keine Echtdaten lesen.** Lies keine Exporte, Datenbank-Dumps, Bankumsätze, Regeldateien mit Zahlernamen, `.env`-Dateien, Caches oder Sicherungen, auch nicht, wenn sie im Dateisystem liegen. Wenn du für eine Aufgabe das Format einer echten Datei brauchst, bitte um eine Beschreibung der Spalten oder um eine erfundene Beispieldatei.
- **Keine Echtdaten ins Repository.** Testdaten entstehen nur über den Testhof-Generator (`hof testhof`). Echte Namen, IBANs, Adressen, Beträge je Person oder Verwendungszwecke haben in Code, Tests, Fixtures, Kommentaren, Commits und Issues nichts zu suchen.
- **Kein Zugang zur Produktion.** Du verbindest dich nicht mit dem Server, nicht per SSH, nicht per Datenbank und nicht über die API. Ausrollen tut Max selbst mit `scripts/deploy.sh`.
- **Importe schreibst du gegen Testdaten.** Jeder Import hat `--probelauf` (Standard) und `--ausfuehren`. Sein Bericht nennt nur Zähler, Abweichungsarten und Einheit-IDs, nie Namen, Beträge je Person oder Verwendungszwecke.
- **Fehlermeldungen und Logs** nennen IDs, keine Personendaten. Personenfelder sind im Modell markiert (siehe 4.); Logging und Berichte filtern sie.
- **Texte an Mieter** sind Vorlagen mit Platzhaltern. Namen, Adressen und Beträge setzt das System beim Erzeugen ein.

Wenn Max dir trotzdem Echtdaten zeigt oder in eine Aufgabe kopiert, weise ihn darauf hin und arbeite ohne sie weiter.

## 2. Stack

- Python 3.12, Django 5.x, Postgres 16 (PostGIS kommt in v2), Abhängigkeiten mit `uv`
- Oberfläche: Django-Templates mit HTMX, ohne eigenes Frontend-Projekt; jede Seite muss am Handy bedienbar sein
- Historie: `django-pghistory` (Trigger in der Datenbank)
- Anmeldung: `django-allauth` mit zweitem Faktor (TOTP); eine Rolle, ein Benutzer
- PDF: WeasyPrint aus HTML-Vorlagen
- Versand: Mails über SMTP (lokal Mailpit), Briefe über die REST-API von LetterXpress (lokal und in Tests ein Fake, gegen die Testumgebung nur mit ausdrücklicher Freigabe)
- Läufe: Django-Management-Befehle, gestartet per systemd-Timer auf dem Server
- Tests: `pytest`, `pytest-django`, `factory_boy`, `Faker` mit `de_DE`
- Qualität: `ruff` (Lint und Format), `mypy` mit `django-stubs` für `services`-Module
- Betrieb: Docker Compose (App, Postgres, Caddy), Zugang nur über Tailscale, Sicherung mit `restic`

Prüfe Paketversionen beim Einbauen auf den aktuellen Stand, statt sie aus dem Gedächtnis zu setzen.

## 3. Aufbau

```
hof/                 Django-Projekt (settings, urls)
apps/
  kern/              Benutzer, Mandant, Objekt, Lauf, Arbeitsposten, Protokoll, Personenfelder
  bank/              Konten, Abruf, Umsätze, Bankfreigaben
  kassenbuch/        Kategorien, Buchungen, Regeln, Monatsabschluss
  dokumente/         Ablage, Belege, Eingangsrechnungen, Vorlagen, Schreiben, Versand
  stammdaten/        Einheiten, Parteien, Verträge, Konditionen, Kaution, Schlüssel, Checklisten, Datenstand
  soll/              Sollposten
  abgleich/          Zuordnung, Zahlerregeln, Klärfälle
  mahnwesen/         Stufen, Fälle, Sperren
  zaehler/           Zähler, Stände
  fristen/           Fristen
  anbindungen/       Website, Verwaltio, LetterXpress
  cockpit/           Startseite
  statusmcp/         MCP-Dienst ohne Personendaten
testhof/             Generator für den erfundenen Testhof
scripts/             deploy.sh, backup.sh, restore-probe.sh, server-setup.sh
docs/                Bauplan, Prozessliste, Betriebshandbuch
```

Eine App wird erst angelegt, wenn ihr Bauschritt dran ist.

## 4. Regeln für den Code

1. **Ein Schreibweg.** Jede Änderung an Daten läuft über eine Funktion in `apps/<app>/services.py`. Views, Läufe und Importe rufen diese Funktionen auf, nie `Model.objects.create()` direkt.
2. **Geld** als `Decimal` mit zwei Stellen (`DecimalField(max_digits=12, decimal_places=2)`), nie als `float`. Beträge sind positiv; die Richtung steht in einem eigenen Feld.
3. **Zeit statt Überschreiben.** Was sich ändert (Miete, Zahlweise, Zielmiete, Status), bekommt `gueltig_ab` und `gueltig_bis`. Alte Stände bleiben lesbar.
4. **Stornieren statt löschen.** Gebuchtes wird nie gelöscht oder überschrieben, sondern storniert. Jede Änderung steht im Protokoll und lässt sich über `kern.services.zuruecknehmen()` zurücknehmen.
5. **Mandant** (GbR, Nachlass, KG-Linie, privat) steht an jeder Buchung, jedem Objekt und jedem Vertrag.
6. **Personenfelder markieren.** Jedes Feld mit Personendaten (Name, Anschrift, Kontakt, IBAN, Verwendungszweck, Freitext über Personen) wird in `kern.personenfelder` registriert. Berichte, Logs und der Status-MCP lesen nur Sichten ohne diese Felder. Ein Test prüft, dass jedes neue Textfeld entweder registriert oder ausdrücklich als unbedenklich markiert ist.
7. **Läufe** sind Management-Befehle. Jeder Lauf schreibt einen Eintrag in `kern.Lauf` (Start, Ende, Ergebnis, Zähler) und sortiert in drei Körbe: gebucht, Arbeitsposten, Fehler. Unsicheres wird ein Arbeitsposten, nie eine Buchung.
8. **Nichts geht ohne Klick hinaus.** Mail oder Brief entstehen als Schreiben im Status „bereit“. Versendet wird nur über die Aktion „Senden“ eines angemeldeten Benutzers, nach einer Vorschau des fertigen PDFs.
9. **Deutsch in der Fachsprache.** Modelle, Felder und Services tragen die deutschen Begriffe aus dem Bauplan (`Sollposten`, `Kassenbuch`, `gueltig_ab`). Umlaute in Bezeichnern als ae, oe, ue, ss. Kommentare und Oberfläche auf Deutsch.
10. **Kleine Schritte.** Eine Sitzung, ein Thema, ein Branch. Migrationen sind Teil des Commits, der sie braucht.

## 5. Tests

- Jede Service-Funktion hat Tests. Jedes Abnahmekriterium eines Bauschritts hat mindestens einen Test, der seinen Namen im Docstring nennt.
- Die Standardabläufe 1, 2, 3, 4, 6, 7 und 10 aus der Prozessliste laufen als durchgehende Tests unter `tests/ablaeufe/`, sobald ihre Schritte gebaut sind.
- Tests laufen gegen Postgres (Docker), nicht gegen SQLite.
- `pytest` muss vor jedem Commit grün sein; `ruff check` und `ruff format --check` ebenso.

## 6. Arbeitsweise

- Zu Beginn einer Sitzung: Bauplan-Abschnitt lesen, Plan in fünf bis zehn Punkten vorlegen, auf Max' OK warten.
- Arbeite auf einem Branch `schritt-<nr>-<thema>`. Am Ende: Commit, Push, Pull Request mit Beschreibung, welche Abnahmekriterien erfüllt sind und welche noch offen.
- Am Ende jeder Sitzung einen kurzen Bericht: was gebaut ist, welche Tests es abdecken, was Max prüfen oder entscheiden muss.
- Neue Abhängigkeiten, Änderungen an diesen Regeln und alles an `scripts/` nur nach Rückfrage.
