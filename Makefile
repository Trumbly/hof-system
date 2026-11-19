COMPOSE := docker compose -f docker-compose.dev.yml
SERVICES := $(wildcard apps/*/services.py)

# WeasyPrint (PDF) findet unter macOS die Bibliotheken aus Homebrew (brew install pango)
# nur über diesen Pfad.
ifeq ($(shell uname -s),Darwin)
export DYLD_FALLBACK_LIBRARY_PATH ?= $(shell brew --prefix 2>/dev/null || echo /opt/homebrew)/lib
endif

.PHONY: dev dienste test e2e klickmessung lint fmt migrate stopp

## Postgres und Mailpit starten, migrieren, Django-Server starten (Mails an Mailpit)
dev: dienste migrate
	EMAIL_URL=$${EMAIL_URL:-smtp://localhost:1025} uv run python manage.py runserver

## Postgres und Mailpit im Hintergrund starten und auf Bereitschaft warten
dienste:
	$(COMPOSE) up -d --wait

## Tests gegen Postgres
test:
	$(COMPOSE) up -d --wait db
	uv run pytest

## Oberflächentests (Playwright): Smoke-Test der 15 wichtigsten Seiten, alle erreichbaren Seiten
## und die Suche, bei 1440 und 390 px.
## Eigene Datenbank (HOF_E2E_DATABASE_URL, Standard hof_e2e) mit frischem Testhof;
## Screenshots unter tests/e2e/artefakte/. Einmal vorher: uv run playwright install chromium
e2e:
	$(COMPOSE) up -d --wait db
	uv run pytest -m "e2e and not klickmessung" tests/e2e

## Klickmessung der Abläufe aus Review 2, B (Ergebnis: tests/e2e/artefakte/klickmessung.md)
klickmessung:
	$(COMPOSE) up -d --wait db
	uv run pytest -m klickmessung tests/e2e

## ruff (Lint und Format) und mypy für die services-Module
lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy $(SERVICES)

## Code formatieren und einfache Lint-Fehler beheben
fmt:
	uv run ruff format .
	uv run ruff check --fix .

## Migrationen anwenden
migrate:
	$(COMPOSE) up -d --wait db
	uv run python manage.py migrate

## Postgres und Mailpit anhalten
stopp:
	$(COMPOSE) down
