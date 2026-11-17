"""Einstellungen für den Server. Alle Werte kommen aus der Umgebung, ohne Standardwerte.

Einzige Ausnahme: ``DJANGO_ENV`` steht hier immer auf ``prod``, wenn nichts gesetzt ist.
Daran erkennen Werkzeuge wie der Testhof-Generator die Produktion und verweigern sich.
"""

import os

os.environ.setdefault("DJANGO_ENV", "prod")

from .base import *  # noqa: F403
from .base import ALLOWED_HOSTS, DOKUMENTE, MIDDLEWARE, env

DEBUG = False

# Healthcheck im Container fragt http://127.0.0.1:8000/gesund. Der App-Port ist nur im
# Docker-Netz offen; von außen kommt jede Anfrage über Caddy mit dem Tailscale-Namen.
ALLOWED_HOSTS = [*ALLOWED_HOSTS, "127.0.0.1", "localhost"]

# Statische Dateien liefert WhiteNoise aus dem Image (gebaut mit collectstatic):
# Sie gehören zur Version, ein Rückrollen bringt die passenden Dateien gleich mit.
MIDDLEWARE = [
    MIDDLEWARE[0],  # SecurityMiddleware
    "whitenoise.middleware.WhiteNoiseMiddleware",
    *MIDDLEWARE[1:],
]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# Caddy beendet TLS und reicht die Anfrage weiter.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
# Der Healthcheck im Container spricht HTTP direkt mit gunicorn, ohne Caddy.
SECURE_REDIRECT_EXEMPT = [r"^gesund$"]
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = env.int("DJANGO_SECURE_HSTS_SECONDS", default=0)
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

# Versand: echte Clients. Ohne HOF_IMAP_URL bzw. LetterXpress-Zugang melden sie
# „nicht eingerichtet“, und das Schreiben bleibt „bereit“.
DOKUMENTE = {
    **DOKUMENTE,
    "IMAP_CLIENT": env.str("HOF_IMAP_CLIENT", default="apps.dokumente.versand.ImaplibClient"),
    "BRIEF_CLIENT": env.str(
        "HOF_BRIEF_CLIENT", default="apps.dokumente.letterxpress.LetterXpressClient"
    ),
}

# LOGGING kommt aus base.py, mit dem Filter für Personenfelder.
