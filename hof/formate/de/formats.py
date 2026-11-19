"""Eingabeformate für Deutsch (ergänzt die Formate von Django, siehe FORMAT_MODULE_PATH).

Das erste Format ist das, in dem Formularfelder ihren Wert ausgeben. Datumsfelder sind
``<input type="date">`` und verlangen JJJJ-MM-TT; mit dem deutschen Format zeigt der
Browser ein leeres Feld (Review 2, A6). Gelesen wird auch das deutsche Format, etwa aus
Importen oder Tests. Alles andere (Anzeige, Dezimalkomma) kommt aus ``django.conf.locale.de``.
"""

DATE_INPUT_FORMATS = ["%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"]
