# syntax=docker/dockerfile:1
#
# Produktions-Image des Hof-Systems.
#
# Zwei Stufen: „bau“ installiert die Abhängigkeiten mit uv und sammelt die statischen
# Dateien ein, „laufzeit“ übernimmt nur das Ergebnis. Im fertigen Image gibt es weder uv
# noch Entwicklungswerkzeuge (pytest, ruff, mypy, Faker); die App läuft als Benutzer „hof“
# (UID 10001), nicht als root.
#
# Bauen (macht scripts/deploy.sh):  docker build --build-arg HOF_VERSION=v0.1.0 -t hof-system:v0.1.0 .

ARG PYTHON_IMAGE=python:3.12.16-slim-trixie

FROM ghcr.io/astral-sh/uv:0.13.9 AS uv

# --- Stufe 1: Abhängigkeiten und statische Dateien ----------------------------------
FROM ${PYTHON_IMAGE} AS bau

COPY --from=uv /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.12 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Erst nur die Abhängigkeiten (gut zu cachen), ohne die Entwicklungsgruppe.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project

COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# Statische Dateien einsammeln (WhiteNoise, mit Hash im Namen und vorkomprimiert).
# Die Werte hier sind nur Platzhalter für den Import der Einstellungen; collectstatic
# braucht weder Datenbank noch Mail. Die echten Werte kommen zur Laufzeit aus der .env.
RUN DJANGO_SETTINGS_MODULE=hof.settings.prod \
    DJANGO_SECRET_KEY=nur-fuer-collectstatic-im-build \
    DATABASE_URL=postgres://bau@localhost:5432/bau \
    EMAIL_URL=consolemail:// \
    /app/.venv/bin/python manage.py collectstatic --no-input


# --- Stufe 2: Laufzeit ----------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS laufzeit

# Systembibliotheken für WeasyPrint (PDF ab Schritt 3): Pango, HarfBuzz, Schriften.
# Ohne Empfehlungen, damit das Image schlank bleibt.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        libharfbuzz-subset0 \
        fontconfig \
        fonts-dejavu-core \
        fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Eigener Benutzer mit fester UID: Der Medienordner auf dem Server gehört 10001:10001.
RUN groupadd --system --gid 10001 hof \
    && useradd --system --uid 10001 --gid hof --home-dir /app --no-create-home \
        --shell /usr/sbin/nologin hof

# Code und venv gehören root und sind für die App nur lesbar. Schreiben darf sie nur in
# den Medienordner (auf dem Server ein Volume).
COPY --from=bau /app /app
RUN mkdir -p /app/medien && chown hof:hof /app/medien

ARG HOF_VERSION=unbekannt
LABEL org.opencontainers.image.title="hof-system" \
      org.opencontainers.image.version="${HOF_VERSION}"

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=hof.settings.prod \
    DJANGO_MEDIA_ROOT=/app/medien \
    HOF_VERSION=${HOF_VERSION} \
    GUNICORN_CMD_ARGS="--bind=0.0.0.0:8000 --workers=3 --timeout=60 --access-logfile=- --forwarded-allow-ips=*"

WORKDIR /app
USER hof
EXPOSE 8000

CMD ["gunicorn", "hof.wsgi:application"]
