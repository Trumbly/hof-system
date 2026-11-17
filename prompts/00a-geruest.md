# Schritt 0a – Repository und Gerüst

Lies `CLAUDE.md` und im Bauplan die Abschnitte „Technik und Betrieb“ und „Schritt 0: Grundgerüst“. Leg mir zuerst deinen Plan vor und warte auf mein OK.

## Ziel

Ein lauffähiges, leeres Django-Projekt mit Entwicklungsumgebung, Tests und Pipeline. Noch keine Fachlogik.

## Aufgaben

1. Projekt mit `uv` anlegen: Python 3.12, Django 5.x, Struktur wie in `CLAUDE.md` Abschnitt 3. Lege nur `hof/` und `apps/kern/` an.
2. Einstellungen aus Umgebungsvariablen (`django-environ` oder gleichwertig), getrennt nach `dev`, `test`, `prod`. Eine `.env.example` mit allen Schlüsseln und Platzhaltern, ohne echte Werte.
3. `docker-compose.dev.yml` mit Postgres 16 und Mailpit. `make dev` startet beides und den Django-Server.
4. `pytest` mit `pytest-django` gegen Postgres; ein erster Test, der die Startseite mit Status 302 (Weiterleitung zur Anmeldung) prüft.
5. `ruff` für Lint und Format, `mypy` mit `django-stubs` für `apps/*/services.py`. `pre-commit` mit ruff, `gitleaks` und einer Prüfung, die Dateien unter `data/`, `import/`, `backups/` und `*.csv` außerhalb von `tests/fixtures/` und `testhof/` ablehnt.
6. GitHub Actions: bei jedem Push `ruff`, `mypy`, `pytest` mit einem Postgres-Service-Container.
7. Basis-Layout für die Oberfläche: ein Template mit Navigation, HTMX eingebunden, schlichtes CSS (kein Framework nötig), lesbar am Handy ab 360 px Breite.
8. `Makefile` mit `dev`, `test`, `lint`, `fmt`, `migrate`.
9. `README.md` mit den Befehlen für den Start.

## Fertig, wenn

- `make test` und `make lint` laufen lokal grün, die Pipeline auf GitHub ebenso.
- `gitleaks` und die Datenprüfung schlagen bei einem absichtlich angelegten `data/test.csv` an (Test, danach wieder löschen).
- Im Repository liegen keine Geheimnisse und keine Daten.

## Nicht in dieser Sitzung

Anmeldung, Modelle, Server, Sicherung. Das kommt in 0b bis 0d.

Am Ende: Branch `schritt-0a-geruest`, Pull Request, kurzer Bericht.
