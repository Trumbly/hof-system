"""Einstellungen für den Server der Oberflächentests (``make e2e``, ``make klickmessung``).

Wie die Entwicklung (Fakes, Dateien auf der Platte), aber mit ``DEBUG = False``: Fehlerseiten
und „Seite nicht gefunden“ sehen aus wie auf dem Server. Statische Dateien liefert
``runserver --insecure`` aus. Gestartet wird der Server von ``tests/e2e/server.py``.
"""

from .dev import *  # noqa: F403

DEBUG = False
