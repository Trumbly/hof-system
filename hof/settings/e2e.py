"""Einstellungen für den Server der Oberflächentests (``make e2e``, ``make klickmessung``).

Wie die Entwicklung (Fakes, Dateien auf der Platte), aber mit ``DEBUG = False``: Fehlerseiten
und „Seite nicht gefunden“ sehen aus wie auf dem Server. Statische Dateien liefert
``runserver --insecure`` aus. Gestartet wird der Server von ``tests/e2e/server.py``.
"""

import os

from .dev import *  # noqa: F403

DEBUG = False
# Wie auf dem Server: die bisherige Gestaltung. Nur der Fall „Apple-Design“
# (tests/e2e/test_design_apple.py) startet einen zweiten Server mit HOF_DESIGN=apple.
HOF_DESIGN = os.environ.get("HOF_DESIGN", "standard")
