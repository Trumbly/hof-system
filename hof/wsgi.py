"""WSGI-Einstieg für den Server."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hof.settings.prod")

application = get_wsgi_application()
