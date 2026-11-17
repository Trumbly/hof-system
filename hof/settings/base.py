"""Gemeinsame Einstellungen für alle Umgebungen."""

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

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Setzt EMAIL_BACKEND, EMAIL_HOST, EMAIL_PORT usw. aus einer URL (Muster aus django-environ).
EMAIL_CONFIG = env.email("EMAIL_URL")
vars().update(EMAIL_CONFIG)
DEFAULT_FROM_EMAIL = env.str("DJANGO_DEFAULT_FROM_EMAIL", default="hof-system@localhost")

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
