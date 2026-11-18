"""Einstellungen für pytest und die Pipeline. Tests laufen gegen Postgres, nie gegen SQLite."""

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "nur-fuer-tests-nicht-geheim")
os.environ.setdefault("DATABASE_URL", "postgres://hof:hof@localhost:5432/hof")
os.environ.setdefault("EMAIL_URL", "smtp://localhost:1025")
# Kein Aufruf ins Netz: Enable Banking als Fake.
os.environ.setdefault("HOF_BANK_CLIENT", "apps.bank.enablebanking.FakeEnableBanking")
# Website und Verwaltio als Fakes (spiegeln die Einheiten des Hof-Systems).
os.environ.setdefault("HOF_WEBSITE_CLIENT", "apps.anbindungen.website.FakeWebsite")
os.environ.setdefault("HOF_VERWALTIO_CLIENT", "apps.anbindungen.verwaltio.FakeVerwaltio")
# Notion (Hof-Backlog) als Fake.
os.environ.setdefault("HOF_NOTION_CLIENT", "apps.cockpit.notion.FakeNotion")

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

# Benachrichtigung: Mail landet in mail.outbox, ntfy im Fake. Nichts geht ins Netz.
HOF_BASIS_URL = "https://hof-system.testhof.invalid"
HOF_BENACHRICHTIGUNG_MAIL = "max@testhof.invalid"
HOF_NTFY_URL = "https://ntfy.testhof.invalid"
HOF_NTFY_THEMA = "hof-testhof"
HOF_NTFY_TOKEN = ""
HOF_BENACHRICHTIGUNG_NTFY_KANAL = "apps.benachrichtigung.kanaele.FakeNtfyKanal"

# Sicherung prüfen: In Tests nie die Datei des Rechners; Tests setzen eine Fake-Statusdatei.
HOF_BACKUP_STATUS = "/nicht-vorhanden/hof-test/backup-status.json"
