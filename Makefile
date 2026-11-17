COMPOSE := docker compose -f docker-compose.dev.yml
SERVICES := $(wildcard apps/*/services.py)

.PHONY: dev dienste test lint fmt migrate stopp

## Postgres und Mailpit starten, migrieren, Django-Server starten
dev: dienste migrate
	uv run python manage.py runserver

## Postgres und Mailpit im Hintergrund starten und auf Bereitschaft warten
dienste:
	$(COMPOSE) up -d --wait

## Tests gegen Postgres
test:
	$(COMPOSE) up -d --wait db
	uv run pytest

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
