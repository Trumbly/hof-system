"""Einstellungen für die Entwicklung am eigenen Rechner (Postgres und Mailpit aus Docker)."""

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "nur-fuer-die-entwicklung-nicht-geheim")
os.environ.setdefault("DATABASE_URL", "postgres://hof:hof@localhost:5432/hof")
os.environ.setdefault("EMAIL_URL", "smtp://localhost:1025")
# Kein Aufruf ins Netz: Enable Banking als Fake.
os.environ.setdefault("HOF_BANK_CLIENT", "apps.bank.enablebanking.FakeEnableBanking")

from .base import *  # noqa: F403

DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]
