"""Einstellungen für die Entwicklung am eigenen Rechner (Postgres und Mailpit aus Docker)."""

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "nur-fuer-die-entwicklung-nicht-geheim")
os.environ.setdefault("DATABASE_URL", "postgres://hof:hof@localhost:5432/hof")
# Mail (Review 2, A9): Ohne EMAIL_URL landen Mails als Dateien in tmp/mails/ (gitignored),
# damit Senden auch ohne Mailpit geht. ``make dev`` startet Mailpit und setzt
# EMAIL_URL=smtp://localhost:1025; die Mails stehen dann unter http://localhost:8025.
os.environ.setdefault("EMAIL_URL", "filemail:///tmp/mails")
# Kein Aufruf ins Netz: Enable Banking als Fake.
os.environ.setdefault("HOF_BANK_CLIENT", "apps.bank.enablebanking.FakeEnableBanking")
# Der Testhof gleicht ab Juli 2026 ab (testhof/abgleich.py); davor führt das alte System.
os.environ.setdefault("HOF_ABGLEICH_BEGINN", "2026-07-01")
# Website und Verwaltio als Fakes (spiegeln die Einheiten des Hof-Systems).
os.environ.setdefault("HOF_WEBSITE_CLIENT", "apps.anbindungen.website.FakeWebsite")
os.environ.setdefault("HOF_VERWALTIO_CLIENT", "apps.anbindungen.verwaltio.FakeVerwaltio")
# Notion (Hof-Backlog) als Fake.
os.environ.setdefault("HOF_NOTION_CLIENT", "apps.cockpit.notion.FakeNotion")
# Stichtag des Testhofs (testhof.aufbau.STICHTAG) als Band oben auf jeder Seite.
os.environ.setdefault("HOF_STICHTAG", "2026-11-01")
# Läufe ohne echte Bank oder Sicherung: „übersprungen (dev)“ statt „mit Fehlern“ (Review 3).
os.environ.setdefault("HOF_DEV_LAEUFE", "1")

from .base import *  # noqa: F403

DEBUG = True

# Experiment „Apple-Design“ (apps.kern.design): in dev voreingestellt; umschalten über den
# Schalter unten in der Seitenleiste oder ?design=standard. HOF_DESIGN=standard schaltet ab.
HOF_DESIGN = os.environ.get("HOF_DESIGN", "apple")

if EMAIL_BACKEND == "django.core.mail.backends.filebased.EmailBackend":  # noqa: F405
    # Relativ zum Projekt, nicht zum Arbeitsverzeichnis (``filemail:///tmp/mails``).
    EMAIL_FILE_PATH = str(BASE_DIR / EMAIL_FILE_PATH)  # noqa: F405
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]
