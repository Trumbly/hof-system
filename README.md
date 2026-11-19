# Hof-System – Startpaket für Claude Code

Dieses Paket ist der Anfang des Repositorys `hof-system`. Es enthält die Regeln für Claude Code, den Bauplan, die Prozessliste und je Bauschritt einen fertigen Auftrag.

## Entwicklung starten

Voraussetzungen: Docker Desktop läuft, [`uv`](https://docs.astral.sh/uv/) ist installiert. Python 3.12 holt `uv` selbst.

```sh
uv sync                        # Abhängigkeiten in .venv
uv run pre-commit install      # Prüfungen vor jedem Commit (ruff, gitleaks, Datenprüfung)
make dev                       # Postgres + Mailpit starten, migrieren, Server auf http://localhost:8000
```

| Befehl | Was er tut |
| --- | --- |
| `make dev` | Postgres 16 und Mailpit starten, migrieren, Django-Server starten |
| `make test` | Postgres starten, `pytest` laufen lassen |
| `make lint` | `ruff check`, `ruff format --check`, `mypy` für `apps/*/services.py` |
| `make fmt` | formatieren und einfache Lint-Fehler beheben |
| `make migrate` | Migrationen anwenden |
| `make stopp` | Postgres und Mailpit anhalten |

- Testhof: `uv run python manage.py testhof --seed 42` legt den erfundenen Hof an (`--leeren` baut ihn neu auf); Anmeldung mit `test@testhof.invalid`, Passwort und TOTP-Geheimnis nennt der Befehl.
- `DJANGO_ENV` nennt die Umgebung. `hof/settings/prod.py` setzt sie auf `prod`, wenn sie nicht gesetzt ist; dann verweigert sich der Testhof-Generator. Am eigenen Rechner und in Tests bleibt sie leer.
- Mailpit zeigt lokal versendete Mails unter http://localhost:8025.
- Ein Benutzer für die Anmeldung: `uv run python manage.py benutzer_anlegen --email <adresse>` (fragt das Passwort ab). Bei der ersten Anmeldung richtet er den zweiten Faktor (TOTP) mit einer Authenticator-App ein; ohne ihn ist keine Seite erreichbar.
- Selbsttest der Läufe: `uv run python manage.py kern_selbsttest` legt einen Lauf und einen Arbeitsposten an (Seiten „Läufe“ und „Arbeitslisten“).
- Fristen (seit Schritt 9a): Seite „Fristen“; der Lauf `uv run python manage.py fristen [--stichtag JJJJ-MM-TT]` macht fällige Fristen zu Arbeitsposten. `uv run python manage.py benachrichtigen` meldet gescheiterte Läufe und wartende Freigaben per Mail (lokal in Mailpit, wenn `HOF_BENACHRICHTIGUNG_MAIL` gesetzt ist) und ntfy (siehe `docs/betrieb.md`).
- Seit Schritt 0b gibt es ein eigenes Benutzermodell (`kern.Benutzer`). Eine Entwicklungsdatenbank aus Schritt 0a einmal neu aufsetzen: `docker compose -f docker-compose.dev.yml down -v`, dann `make dev`.
- Am eigenen Rechner braucht es keine `.env`; die Standardwerte stehen in `hof/settings/dev.py`. Für den Server zeigt `.env.example` alle Schlüssel.
- Die Pipeline (`.github/workflows/pruefen.yml`) prüft jeden Push mit ruff, mypy und pytest gegen Postgres, dazu Datenprüfung und gitleaks über den ganzen Verlauf.
- Vorlagen und Versand (Schritt 3c): Seiten „Schreiben“ und „Vorlagen“ unter `/dokumente/`. Lokal gehen Mails an Mailpit, die Kopie in „Gesendet“ und Briefe an Fakes im Speicher (nichts ins Netz). PDFs erzeugt WeasyPrint; unter macOS vorher `brew install pango` (das Makefile setzt `DYLD_FALLBACK_LIBRARY_PATH`). Vorlagen aus Notion: `uv run python manage.py vorlagen_import tests/fixtures/vorlagen` (Probelauf, mit `--ausfuehren` als Entwurf). Schnittstelle für andere Schritte: `apps.dokumente.services.schreiben_erzeugen` (Docstring des Moduls).
- Stammdaten (Schritt 4): Seiten unter `/stammdaten/` (Einheiten, Parteien, Verträge, Übergabe und Rückgabe am Handy, Leerstand, Datenstand). Importe mit Probelauf als Standard: `uv run python manage.py import_einheiten tests/fixtures/stammdaten/notion_einheiten.csv` und `uv run python manage.py import_hellohousing tests/fixtures/stammdaten/hellohousing_mieter.csv --mietverlauf tests/fixtures/stammdaten/hellohousing_mietverlauf.csv` (mit `--ausfuehren` übernehmen). Regeln in `apps/stammdaten/konfiguration.toml`, Spalten in `apps/stammdaten/import_spalten.toml`.
- Oberflächentests und Klickmessung (Review 2): einmal `uv run playwright install chromium`, dann `make e2e` (Smoke-Test: 15 wichtige Seiten bei 1440 und 390 px, ohne Serverfehler, ohne waagrechte Scrollleiste, ohne englische Standardtexte; Screenshots unter `tests/e2e/artefakte/smoke/`) und `make klickmessung` (zählt Klicks, Seitenwechsel und Tippfelder der Abläufe aus Review 2, B; Ergebnis in `tests/e2e/artefakte/klickmessung.md`, Tabelle in `docs/klickmessung.md`). Beide bauen eine eigene Datenbank (`HOF_E2E_DATABASE_URL`, Standard `hof_e2e`) mit frischem Testhof und Tageslauf auf und starten einen eigenen Server auf einem freien Port (`HOF_E2E_PORT` setzt ihn fest). `make test` überspringt sie. In der Pipeline läuft der Smoke-Test als Job `e2e` und lädt die Screenshots als Artefakt hoch.
- Betrieb auf dem Server (seit Schritt 0d): `docs/betrieb.md`. Produktions-Image aus `Dockerfile`, Dienste in `docker-compose.prod.yml` (App, Postgres, Caddy, nur über Tailscale), Skripte unter `scripts/` (`server-setup.sh`, `deploy.sh`, `backup.sh`, `restore-probe.sh`), systemd-Einheiten unter `deploy/systemd/`. Ausgeführt werden sie nur von Max auf dem Server.

## Inhalt

| Datei | Wofür |
| --- | --- |
| `CLAUDE.md` | Regeln, die Claude Code in jeder Sitzung liest: KI-Grenze, Stack, Aufbau, Code-Regeln, Tests |
| `.claude/settings.json` | sperrt für Claude Code das Lesen von `.env`, Datenordnern und den Regeldateien von hof-fints; verbietet SSH und Datenbankzugriff |
| `.gitignore` | hält Daten, Sicherungen und Geheimnisse aus dem Repository |
| `docs/bauplan.md` | Bauplan vom 17.11.2026 |
| `docs/prozessliste.md` | Prozessliste vom 16./17.11.2026 |
| `docs/betrieb.md` | Betriebshandbuch: Ausrollen, Sicherung, Wiederherstellen, Ausfall |
| `docs/notfallmappe-vorlage.md` | Vorlage der Notfallmappe: welche Zugänge es gibt und wo sie liegen |
| `Dockerfile`, `docker-compose.prod.yml`, `deploy/` | Produktion: Image, Dienste, Caddy, systemd-Timer |
| `scripts/` | Einrichtung, Ausrollen, Sicherung, Rückspielprobe, Läufe |
| `prompts/` | ein Auftrag je Sitzung, in dieser Reihenfolge |

## Einmal vorab

1. Leeres privates Repository `hof-system` bei GitHub anlegen und klonen.
2. Den Inhalt dieses Pakets hineinkopieren, committen, pushen.
3. Docker Desktop und Claude Code auf dem Mac, `gh` angemeldet.

## So läuft eine Sitzung

1. Im Repository-Ordner `claude` starten.
2. Mit Shift+Tab in den Plan-Modus wechseln.
3. Eingeben: `Arbeite prompts/00a-geruest.md ab.`
4. Den Plan lesen, Fragen beantworten, freigeben.
5. Am Ende den Pull Request prüfen und mergen.
6. Im Bauplan die erfüllten Abnahmekriterien abhaken.

Eine Sitzung, ein Auftrag. Für den nächsten Auftrag eine neue Sitzung starten (`/clear`).

## Reihenfolge und was du vorher brauchst

| Auftrag | Vorher von dir |
| --- | --- |
| `00a-geruest` | Repository, Docker |
| `00b-kern` | – |
| `00c-testhof` | – |
| `00d-betrieb` | Hetzner-Cloud-Server (Ubuntu 24.04) im Tailscale-Netz, Hetzner Storage Box |
| `01-bank` | Code von hof-fints unter `../hof-fints`; Zugang zu Enable Banking für Vivid |
| `02-kassenbuch` | Beschreibung der Spalten der Kategorienliste aus dem Finanz-Dashboard |
| `03-dokumente` | eigenes Rechnungspostfach mit IMAP-Zugang, SMTP-Zugang für kontakt@, Konto und API-Schlüssel bei LetterXpress, Vertrag zur Auftragsverarbeitung |
| `04-stammdaten` | Antwort von Dreses zur Umsatzsteuer; Spaltenbeschreibung der Exporte aus Notion und HelloHousing |
| `05-sollstellung` | Spaltenbeschreibung des Soll-Exports aus HelloHousing |
| `06-abgleich` | Beschreibung des Aufbaus von `regeln.yaml` (ohne Inhalte) |
| `07-mahnwesen` | geprüfte Vorlagen von Herrn Bucks, sonst bleibt der Versand im Probebetrieb |
| `08-zaehler` | Spaltenbeschreibung der vorhandenen Zählerdaten |
| `09-fristen-cockpit` | Notion-Integration mit Zugriff auf das Hof-Backlog, Datenbank-ID |
| `10-anbindungen` | Website-Repository unter `../website`; API-Dokumentation von Verwaltio |
| `11-status-mcp` | – |

## KI-Grenze in einem Satz

Claude Code sieht Code und den erfundenen Testhof. Echtdaten, Server und Zugangsdaten bleiben bei dir. Wenn Claude Code das Format einer echten Datei braucht, beschreibst du ihm die Spalten, statt die Datei zu zeigen.
