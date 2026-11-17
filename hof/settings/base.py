"""Gemeinsame Einstellungen für alle Umgebungen."""

from datetime import date
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
# Eine lokale .env wird gelesen, wenn vorhanden. Gesetzte Umgebungsvariablen haben Vorrang.
environ.Env.read_env(BASE_DIR / ".env", overwrite=False)

SECRET_KEY = env.str("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS: list[str] = env.list("DJANGO_ALLOWED_HOSTS", default=[])
CSRF_TRUSTED_ORIGINS: list[str] = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "allauth",
    "allauth.account",
    "allauth.mfa",
    "pgtrigger",
    "pghistory",
    "apps.kern",
    "apps.bank",
    "apps.fristen",
    "apps.benachrichtigung",
    "apps.dokumente",
    "apps.kassenbuch",
    "apps.stammdaten",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    # Ohne zweiten Faktor kommt niemand über die Anmeldung hinaus.
    "apps.kern.middleware.ZweiterFaktorPflichtMiddleware",
    # Jede Änderung aus einer Anfrage trägt den Benutzer als Kontext im Protokoll.
    "pghistory.middleware.HistoryMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "hof.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "hof.wsgi.application"

DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["ATOMIC_REQUESTS"] = True

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Anmeldung: django-allauth mit E-Mail und Passwort, zweiter Faktor (TOTP) ist Pflicht.
# Eine Rolle, ein Benutzer; er entsteht mit `manage.py benutzer_anlegen`.
AUTH_USER_MODEL = "kern.Benutzer"
AUTHENTICATION_BACKENDS = [
    "allauth.account.auth_backends.AuthenticationBackend",
]
LOGIN_URL = "account_login"
LOGIN_REDIRECT_URL = "kern:startseite"
ACCOUNT_ADAPTER = "apps.kern.anmeldung.KontoAdapter"
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_EMAIL_VERIFICATION = "none"
ACCOUNT_LOGOUT_REDIRECT_URL = "account_login"
# Keine Frage „Angemeldet bleiben?“: Jede Sitzung gilt genau SESSION_COOKIE_AGE lang.
ACCOUNT_SESSION_REMEMBER = True
MFA_SUPPORTED_TYPES = ["totp", "recovery_codes"]
MFA_TOTP_ISSUER = "Hof-System"

# Sitzungen laufen 12 Stunden nach der Anmeldung ab, unabhängig von der Nutzung.
SESSION_COOKIE_AGE = 12 * 60 * 60
SESSION_SAVE_EVERY_REQUEST = False

# Protokoll (django-pghistory): Ereignisse sind unveränderlich, die Zeit ist die
# tatsächliche Uhrzeit, nicht der Beginn der Transaktion.
PGHISTORY_APPEND_ONLY = True
PGHISTORY_CREATED_AT_FUNCTION = "clock_timestamp()"

LANGUAGE_CODE = "de-de"
TIME_ZONE = "Europe/Berlin"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# Hochgeladene Dokumente. Auf dem Server ein eigenes Volume (siehe docker-compose.prod.yml),
# ausgeliefert nur über Django mit Anmeldung, nie direkt über Caddy.
MEDIA_URL = "medien/"
MEDIA_ROOT = env.path("DJANGO_MEDIA_ROOT", default=BASE_DIR / "media")

# Ausgerollte Version (Git-Tag), gesetzt von scripts/deploy.sh. Zeigt der Endpunkt /gesund.
HOF_VERSION = env.str("HOF_VERSION", default="unbekannt")

# Bank: Enable Banking für Vivid (PSD2, nur lesend). Der Client ist austauschbar; in
# Entwicklung und Tests steht hier der Fake (hof/settings/dev.py, test.py).
HOF_BANK_CLIENT = env.str("HOF_BANK_CLIENT", default="apps.bank.enablebanking.EnableBankingClient")
ENABLE_BANKING_APP_ID = env.str("ENABLE_BANKING_APP_ID", default="")
# Pfad zum privaten Schlüssel der App (PEM), auf dem Server außerhalb des Images.
ENABLE_BANKING_SCHLUESSEL = env.str("ENABLE_BANKING_SCHLUESSEL", default="")
ENABLE_BANKING_URL = env.str("ENABLE_BANKING_URL", default="https://api.enablebanking.com")
# Rückkehr-Adresse nach der Freigabe; leer: aus der Anfrage (…/bank/freigabe/rueckkehr/).
# Muss genau so in der App bei Enable Banking eingetragen sein.
ENABLE_BANKING_REDIRECT_URL = env.str("ENABLE_BANKING_REDIRECT_URL", default="")
ENABLE_BANKING_LAND = env.str("ENABLE_BANKING_LAND", default="DE")
ENABLE_BANKING_FREIGABE_TAGE = env.int("ENABLE_BANKING_FREIGABE_TAGE", default=180)

# Kassenbuch: erster Tag, ab dem der Lauf kassenbuch_einordnen Umsätze einordnet und der
# Monatsabschluss beginnt (leer: ab dem ersten Umsatz). Auf dem Server: 2026-01-01 (H6.3).
_kassenbuch_beginn = env.str("HOF_KASSENBUCH_BEGINN", default="")
KASSENBUCH_BEGINN = date.fromisoformat(_kassenbuch_beginn) if _kassenbuch_beginn else None

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Setzt EMAIL_BACKEND, EMAIL_HOST, EMAIL_PORT usw. aus einer URL (Muster aus django-environ).
EMAIL_CONFIG = env.email("EMAIL_URL")
vars().update(EMAIL_CONFIG)
DEFAULT_FROM_EMAIL = env.str("DJANGO_DEFAULT_FROM_EMAIL", default="hof-system@localhost")

# Benachrichtigung (H11.1): Nur wenn ein Lauf gescheitert ist oder Freigaben warten, an
# Max per Mail und per Push über ntfy. Inhalt nur Zähler und Link. Ein Kanal ohne
# Einstellung bleibt aus. Die Klassen der Kanäle sind austauschbar (Tests: Fakes).
HOF_BASIS_URL = env.str("HOF_BASIS_URL", default="")  # z. B. https://hof-system.<tailnet>.ts.net
HOF_BENACHRICHTIGUNG_MAIL = env.str("HOF_BENACHRICHTIGUNG_MAIL", default="")
HOF_NTFY_URL = env.str("HOF_NTFY_URL", default="")  # z. B. https://ntfy.sh
HOF_NTFY_THEMA = env.str("HOF_NTFY_THEMA", default="")
HOF_NTFY_TOKEN = env.str("HOF_NTFY_TOKEN", default="")
# Arten offener Arbeitsposten, die als wartende Freigabe gemeldet werden.
HOF_BENACHRICHTIGUNG_ARTEN: list[str] = env.list(
    "HOF_BENACHRICHTIGUNG_ARTEN",
    default=[
        "Frist fällig",
        # Bank (apps.bank.services, ART_*)
        "Bankfreigabe läuft ab",
        "Bankfreigabe fehlt",
        "Bankabruf gescheitert",
        "Bankumsätze fehlen",
        "Saldo weicht ab",
        # Kassenbuch (apps.kassenbuch.services, ART_*)
        "Umsätze einordnen",
        "Monatsabschluss offen",
        # Dokumente (apps.dokumente.services, ART_*)
        "Kopie in „Gesendet“ fehlt",
        "Brief storniert",
        # Stammdaten (apps.stammdaten.services/schreiben, ART_*); die Kautionsabrechnung
        # meldet sich über ihre Frist („Frist fällig“).
        "Schreiben erstellen",
        "Schlüssel fehlt",
        # spätere Schritte
        "Mahnungen senden",
        "Zahlungen klären",
        "Rechnung zahlen",
        "Schreiben senden",
    ],
)
# Wie weit zurück ein gescheiterter Lauf zählt.
HOF_BENACHRICHTIGUNG_FENSTER_STUNDEN = env.int("HOF_BENACHRICHTIGUNG_FENSTER_STUNDEN", default=24)
HOF_BENACHRICHTIGUNG_MAIL_KANAL = "apps.benachrichtigung.kanaele.MailKanal"
HOF_BENACHRICHTIGUNG_NTFY_KANAL = "apps.benachrichtigung.kanaele.NtfyKanal"

# Vorlagen und Versand (Schritt 3c). Die Clients sind austauschbar; Standard sind die Fakes
# (nichts geht ins Netz). prod.py nimmt die echten Clients, ohne Zugangsdaten melden sie
# „nicht eingerichtet“. Die Fakes verweigern sich bei DJANGO_ENV=prod.
DOKUMENTE = {
    "MAIL_ABSENDER": env.str("HOF_MAIL_ABSENDER", default="kontakt@hof.invalid"),
    "MAIL_CLIENT": "apps.dokumente.versand.DjangoMailClient",
    "IMAP_CLIENT": env.str("HOF_IMAP_CLIENT", default="apps.dokumente.versand.FakeImapClient"),
    # imaps://benutzer:passwort@host:993 (Sonderzeichen im Passwort URL-kodiert)
    "IMAP_URL": env.str("HOF_IMAP_URL", default=""),
    "IMAP_ORDNER_GESENDET": env.str("HOF_IMAP_ORDNER_GESENDET", default="Sent"),
    "BRIEF_CLIENT": env.str(
        "HOF_BRIEF_CLIENT", default="apps.dokumente.letterxpress.FakeLetterXpressClient"
    ),
    "LETTERXPRESS_BENUTZER": env.str("LETTERXPRESS_BENUTZER", default=""),
    "LETTERXPRESS_APIKEY": env.str("LETTERXPRESS_APIKEY", default=""),
    # „test“ legt Aufträge nur in die Postbox; „live“ erst nach Freigabe durch Max.
    "LETTERXPRESS_MODUS": env.str("LETTERXPRESS_MODUS", default="test"),
    # Absender im Briefkopf (Platzhalter absender.*). Zeilen der Anschrift mit „;“ trennen.
    "ABSENDER": {
        "name": env.str("HOF_ABSENDER_NAME", default="Hof (Absender nicht eingerichtet)"),
        "anschrift": env.str("HOF_ABSENDER_ANSCHRIFT", default="").replace(";", "\n"),
        "email": env.str("HOF_MAIL_ABSENDER", default="kontakt@hof.invalid"),
        "telefon": env.str("HOF_ABSENDER_TELEFON", default=""),
        "bank": env.str("HOF_ABSENDER_BANK", default=""),
        "steuernummer": env.str("HOF_ABSENDER_STEUERNUMMER", default=""),
    },
}

# Logs nennen IDs, keine Personendaten: Der Filter ersetzt Werte registrierter
# Personenfelder (kern.personenfelder) durch ‹…›.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "personenfelder": {"()": "apps.kern.personenfelder.PersonenfelderFilter"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "filters": ["personenfelder"],
        },
    },
    "root": {"handlers": ["console"], "level": "INFO"},
}
