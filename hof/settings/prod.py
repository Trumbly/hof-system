"""Einstellungen für den Server. Alle Werte kommen aus der Umgebung, ohne Standardwerte.

Einzige Ausnahme: ``DJANGO_ENV`` steht hier immer auf ``prod``, wenn nichts gesetzt ist.
Daran erkennen Werkzeuge wie der Testhof-Generator die Produktion und verweigern sich.
"""

import os

os.environ.setdefault("DJANGO_ENV", "prod")

from .base import *  # noqa: F403
from .base import env

DEBUG = False

# Caddy beendet TLS und reicht die Anfrage weiter.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = env.int("DJANGO_SECURE_HSTS_SECONDS", default=0)
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

# LOGGING kommt aus base.py, mit dem Filter für Personenfelder.
