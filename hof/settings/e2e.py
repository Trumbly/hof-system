"""Einstellungen für den Server der Oberflächentests (``make e2e``, ``make klickmessung``).

Wie die Entwicklung (Fakes, Dateien auf der Platte), aber mit ``DEBUG = False``: Fehlerseiten
und „Seite nicht gefunden“ sehen aus wie auf dem Server. Statische Dateien liefert
``runserver --insecure`` aus. Gestartet wird der Server von ``tests/e2e/server.py``.
"""

import os

from .dev import *  # noqa: F403

DEBUG = False
# Die Oberflächentests laufen im Apple-Design. Nur der Fall „bisherige Gestaltung“
# (tests/e2e/test_design_standard.py) startet einen zweiten Server mit
# HOF_DESIGN_STANDARD=standard.
HOF_DESIGN_STANDARD = os.environ.get("HOF_DESIGN_STANDARD", "apple")
