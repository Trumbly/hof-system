"""Einstellungen für pytest und die Pipeline. Tests laufen gegen Postgres, nie gegen SQLite."""

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "nur-fuer-tests-nicht-geheim")
os.environ.setdefault("DATABASE_URL", "postgres://hof:hof@localhost:5432/hof")
os.environ.setdefault("EMAIL_URL", "smtp://localhost:1025")
# Kein Aufruf ins Netz: Enable Banking als Fake.
os.environ.setdefault("HOF_BANK_CLIENT", "apps.bank.enablebanking.FakeEnableBanking")

from .base import *  # noqa: F403

DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost"]

# Schnelles Hashing, Mails nur im Speicher: In Tests geht nichts hinaus.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
