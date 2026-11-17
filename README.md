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

- Mailpit zeigt lokal versendete Mails unter http://localhost:8025.
- Ein Benutzer für die Anmeldung: `uv run python manage.py createsuperuser`.
- Am eigenen Rechner braucht es keine `.env`; die Standardwerte stehen in `hof/settings/dev.py`. Für den Server zeigt `.env.example` alle Schlüssel.
- Die Pipeline (`.github/workflows/pruefen.yml`) prüft jeden Push mit ruff, mypy und pytest gegen Postgres, dazu Datenprüfung und gitleaks über den ganzen Verlauf.

## Inhalt

| Datei | Wofür |
| --- | --- |
| `CLAUDE.md` | Regeln, die Claude Code in jeder Sitzung liest: KI-Grenze, Stack, Aufbau, Code-Regeln, Tests |
| `.claude/settings.json` | sperrt für Claude Code das Lesen von `.env`, Datenordnern und den Regeldateien von hof-fints; verbietet SSH und Datenbankzugriff |
| `.gitignore` | hält Daten, Sicherungen und Geheimnisse aus dem Repository |
| `docs/bauplan.md` | Bauplan vom 17.11.2026 |
| `docs/prozessliste.md` | Prozessliste vom 16./17.11.2026 |
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
