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
    "apps.soll",
    "apps.anbindungen",
    "apps.zaehler",
    "apps.abgleich",
    "apps.mahnwesen",
    "apps.cockpit",
    "apps.statusmcp",
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
# Datumsfelder (<input type="date">) brauchen den Wert als JJJJ-MM-TT, sonst zeigt der
# Browser ein leeres Feld (Review 2, A6). Gelesen wird beides (hof/formate/de/formats.py).
FORMAT_MODULE_PATH = ["hof.formate"]

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

# Sollstellung: erster Tag, ab dem das Hof-System das Soll führt. Perioden, die früher
# beginnen, führt das alte System (HelloHousing); leer: ab Vertragsbeginn. Je Vertrag
# abweichend über die Sollregel. Vorschlag für den Server: 2026-07-01 (Vergleich ab Juli).
_soll_beginn = env.str("HOF_SOLL_BEGINN", default="")
SOLL_BEGINN = date.fromisoformat(_soll_beginn) if _soll_beginn else None

# Abgleich: erster Tag, ab dem der Lauf abgleich Zahlungen und Posten ansieht. Zahlungen davor
# und Posten, deren Zeitraum davor endet, führt das alte System (Vortrag). Leer: wie
# HOF_SOLL_BEGINN. Vorschlag für den Server: 2026-07-01 (Rückrechnung ab Juli).
_abgleich_beginn = env.str("HOF_ABGLEICH_BEGINN", default="")
ABGLEICH_BEGINN = date.fromisoformat(_abgleich_beginn) if _abgleich_beginn else None

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
        "Belege zuordnen",
        "Rechnung zahlen",
        "Rechnung zuordnen",
        "Beleg und Abbuchung weichen ab",
        # Stammdaten (apps.stammdaten.services/schreiben, ART_*); die Kautionsabrechnung
        # meldet sich über ihre Frist („Frist fällig“).
        "Schreiben erstellen",
        "Schlüssel fehlt",
        # Sollstellung (apps.soll.services, ART_*)
        "Sollposten prüfen",
        # Anbindungen (apps.anbindungen.services, ART_*)
        "Buchungsanfrage prüfen",
        "ID-Abweichung",
        # Zähler (apps.zaehler.services, ART_*): nur im Ablesefenster um den Stichtag
        "Ablesung fehlt",
        # Cockpit (apps.cockpit.services, ART_*)
        "Sicherung prüfen",
        # Mahnwesen (apps.mahnwesen.services, ART)
        "Mahnungen senden",
        # spätere Schritte
        "Zahlungen klären",
        "Schreiben senden",
    ],
)
# Wie weit zurück ein gescheiterter Lauf zählt.
HOF_BENACHRICHTIGUNG_FENSTER_STUNDEN = env.int("HOF_BENACHRICHTIGUNG_FENSTER_STUNDEN", default=24)
HOF_BENACHRICHTIGUNG_MAIL_KANAL = "apps.benachrichtigung.kanaele.MailKanal"
HOF_BENACHRICHTIGUNG_NTFY_KANAL = "apps.benachrichtigung.kanaele.NtfyKanal"

# Aufgaben im Hof-Backlog (Notion, Schritt 9): Offene Arbeitsposten der Arten aus
# HOF_NOTION_ARTEN erscheinen je Art als eine Aufgabe („3 Mahnungen senden“) mit NW-IDs,
# Fälligkeit und Link, ohne Namen und Beträge (apps.cockpit.services). Der Client ist
# austauschbar; ohne Token und Datenbank-ID meldet er „nicht eingerichtet“. Token nur auf
# dem Server.
HOF_NOTION_CLIENT = env.str("HOF_NOTION_CLIENT", default="apps.cockpit.notion.NotionHttpClient")
HOF_NOTION_TOKEN = env.str("HOF_NOTION_TOKEN", default="")
HOF_NOTION_DATENBANK_ID = env.str("HOF_NOTION_DATENBANK_ID", default="")
# „database_id“ (Datenbank mit einer Datenquelle) oder „data_source_id“ (dann ist
# HOF_NOTION_DATENBANK_ID die ID der Datenquelle).
HOF_NOTION_PARENT = env.str("HOF_NOTION_PARENT", default="database_id")
# Namen der Eigenschaften in der Datenbank „Hof-Backlog“ und die Werte des Status.
# Annahme bis Max die echten Namen gibt; „text“ leer: kein Textfeld.
HOF_NOTION_FELDER = {
    "titel": env.str("HOF_NOTION_FELD_TITEL", default="Name"),
    "status": env.str("HOF_NOTION_FELD_STATUS", default="Status"),
    "status_typ": env.str("HOF_NOTION_STATUS_TYP", default="status"),  # oder „select“
    "faellig": env.str("HOF_NOTION_FELD_FAELLIG", default="Fällig"),
    "link": env.str("HOF_NOTION_FELD_LINK", default="Link"),
    "text": env.str("HOF_NOTION_FELD_TEXT", default="Details"),
    "offen_wert": env.str("HOF_NOTION_WERT_OFFEN", default="Offen"),
    "erledigt_wert": env.str("HOF_NOTION_WERT_ERLEDIGT", default="Erledigt"),
}
# Arten, die ins Hof-Backlog gehen: Art des Arbeitspostens → (Titel bei 1, Titel ab 2).
HOF_NOTION_ARTEN: dict[str, tuple[str, str]] = {
    "Mahnungen senden": ("Mahnung senden", "Mahnungen senden"),
    "Zahlungen klären": ("Zahlung klären", "Zahlungen klären"),
    "Rechnung zahlen": ("Rechnung zahlen", "Rechnungen zahlen"),
    "Beleg und Abbuchung weichen ab": (
        "Beleg und Abbuchung prüfen",
        "Belege und Abbuchungen prüfen",
    ),
    "Frist fällig": ("Frist fällig", "Fristen fällig"),
    "Ablesung fehlt": ("Ablesung fehlt", "Ablesungen fehlen"),
}

# Sicherung prüfen (Tageslauf, Schritt 9): Statusdatei von scripts/backup.sh. Im Container
# ist /var/lib/hof nur lesend eingehängt (docker-compose.prod.yml).
HOF_BACKUP_STATUS = env.str("HOF_BACKUP_STATUS", default="/var/lib/hof/backup-status.json")
HOF_BACKUP_HOECHSTALTER_STUNDEN = env.int("HOF_BACKUP_HOECHSTALTER_STUNDEN", default=26)

# Anbindungen (Schritt 10): Website und Verwaltio. Die Clients sind austauschbar; in
# Entwicklung und Tests stehen hier die Fakes (hof/settings/dev.py, test.py). Ohne Adresse
# und Token melden die echten Clients „nicht eingerichtet“, und die Läufe überspringen sie.
HOF_WEBSITE_CLIENT = env.str(
    "HOF_WEBSITE_CLIENT", default="apps.anbindungen.website.WebsiteHttpClient"
)
# Adresse und Token, mit denen das Hof-System die NW-IDs der Website abfragt (H1.6).
HOF_WEBSITE_URL = env.str("HOF_WEBSITE_URL", default="")  # z. B. https://noelle-wying.de
HOF_WEBSITE_TOKEN = env.str("HOF_WEBSITE_TOKEN", default="")
# Optional: Adressen oder Netze, von denen die Website Katalog und Ereignisse abrufen darf
# (Komma-getrennt, z. B. „100.64.0.7,2001:db8::/64“). Leer: jede Adresse mit gültigem Token
# (der Zugang ist dann nur über Tailscale offen).
HOF_WEBSITE_IP_FREIGABE: list[str] = env.list("HOF_WEBSITE_IP_FREIGABE", default=[])
# Verwaltio (V12): Der echte Client ist offen, bis die API-Dokumentation vorliegt.
HOF_VERWALTIO_CLIENT = env.str(
    "HOF_VERWALTIO_CLIENT", default="apps.anbindungen.verwaltio.VerwaltioApiClient"
)
VERWALTIO_URL = env.str("VERWALTIO_URL", default="")
VERWALTIO_TOKEN = env.str("VERWALTIO_TOKEN", default="")

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
    # Ablage und Belege (Schritt 3a). Das Rechnungspostfach liest der Lauf belege_postfach.
    # Der Client ist austauschbar; Standard ist der Fake, prod.py nimmt imaplib.
    "BELEGE_POSTFACH_CLIENT": env.str(
        "HOF_BELEGE_POSTFACH_CLIENT", default="apps.dokumente.postfach.FakePostfach"
    ),
    # imaps://benutzer:passwort@host:993 des eigenen Rechnungspostfachs (nicht kontakt@)
    "BELEGE_IMAP_URL": env.str("HOF_BELEGE_IMAP_URL", default=""),
    "BELEGE_IMAP_ORDNER": env.str("HOF_BELEGE_IMAP_ORDNER", default="INBOX"),
    # Was mit einer gelesenen Mail geschieht: „markieren“ (als gelesen) oder „verschieben“.
    "BELEGE_NACH_DEM_LESEN": env.str("HOF_BELEGE_NACH_DEM_LESEN", default="markieren"),
    "BELEGE_ZIELORDNER": env.str("HOF_BELEGE_ZIELORDNER", default="Abgelegt"),
    # Aufbewahrung je Dokumentart in Jahren, gerechnet ab Ende des Jahres des Dokuments
    # (H8.6). Nur Feld und Anzeige; den Löschlauf gibt es erst in v2.
    "LOESCHFRIST_JAHRE": {
        "beleg": 10,
        "mahnung_lieferant": 6,
        "ruecklastschrift": 6,
        "vollstreckung": 10,
        "sonstiges": 6,
    },
    # Monatsabschluss (H6.6): Welche Buchungen einen Beleg brauchen. Fehlt er, erscheint im
    # Abschluss ein Hinweis und ein Arbeitsposten „Belege fehlen“; der Abschluss geht trotzdem.
    "BELEGPFLICHT": {
        # Arten der Kategorie (kassenbuch.Kategorie.Art), für die ein Beleg Pflicht ist
        "ARTEN": env.list("HOF_BELEGPFLICHT_ARTEN", default=["ausgabe"]),
        # ab diesem Betrag in Euro (einschließlich)
        "AB_BETRAG": env.str("HOF_BELEGPFLICHT_AB_BETRAG", default="0.00"),
        # Kategorien ohne Belegpflicht, nach Bezeichnung (z. B. Kontoführung: Beleg ist der
        # Kontoauszug)
        "OHNE_KATEGORIEN": env.list("HOF_BELEGPFLICHT_OHNE_KATEGORIEN", default=[]),
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
