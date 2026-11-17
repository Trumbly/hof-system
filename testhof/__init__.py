"""Generator für den erfundenen Testhof (Bauplan, Abschnitt „Testdaten“).

Aufruf: ``manage.py testhof --seed 42 [--leeren]``. Gleicher Seed ergibt exakt dieselben
Daten. Jede App bringt ein eigenes Modul mit (``testhof/kern.py``, später
``testhof/bank.py`` …), das sich mit seinen Abhängigkeiten anmeldet
(``testhof.register``). ``testhof.aufbau.aufbauen()`` ruft die Module in fester
Reihenfolge auf.

Bausteine:

- ``sperre``: Der Generator läuft nie in der Produktion (``DJANGO_ENV=prod``).
- ``register``: Anmeldung der Module, Reihenfolge nach Abhängigkeiten.
- ``aufbau``: Aufbau und Leeren über alle Module.
- ``erfunden``: Test-IBANs, Telefonnummern aus dem fiktiven Bereich, ``.invalid``-Mails.
- ``pruefsumme``: Hash über die Tabellen, um Gleichheit zweier Läufe zu prüfen.
- ``sonderfaelle``: die 16 Sonderfälle aus dem Bauplan.

Dieses Paket importiert hier bewusst nichts: ``sperre`` muss sich ohne Faker laden
lassen, damit die Sperre auch dort greift, wo nur die Laufzeit-Abhängigkeiten
installiert sind.
"""
