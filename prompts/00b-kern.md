# Schritt 0b – Kern: Anmeldung, Protokoll, Läufe, Arbeitslisten

Lies `CLAUDE.md`, im Bauplan „Datenmodell“ (Regeln und Bereich Grundgerüst), „Dienste und Oberfläche“ und „Schritt 0“. Leg mir zuerst deinen Plan vor und warte auf mein OK.

## Ziel

Die App `kern` mit allem, worauf jeder spätere Dienst aufbaut. Abläufe aus der Prozessliste: H11.3, H11.6.

## Aufgaben

1. **Anmeldung** mit `django-allauth`: E-Mail und Passwort, zweiter Faktor per TOTP ist Pflicht. Keine Registrierung über die Oberfläche; der Benutzer entsteht per `manage.py`-Befehl. Sitzungen laufen nach 12 Stunden ab.
2. **Modelle** `Mandant` (GbR, Nachlass, KG-Linie, privat), `Objekt` (mit Mandant), `Lauf` (Name, Start, Ende, Status, Zähler gebucht/Arbeitsposten/Fehler, Meldung ohne Personendaten), `Arbeitsposten` (Art, Bezug als generischer Fremdschlüssel, Status offen/erledigt/zurückgestellt, Aktionen, Fälligkeit, erledigt am).
3. **Protokoll** mit `django-pghistory` für alle Modelle; ein Mixin oder Decorator, damit neue Modelle es ohne Zusatzarbeit bekommen. Jede Änderung trägt Benutzer oder Lauf als Kontext.
4. **Rücknahme**: `kern.services.zuruecknehmen(ereignis_id)` stellt den Stand vor einer Änderung wieder her, selbst als neue protokollierte Änderung. Gebuchte Werte (ab Schritt 2) werden storniert, nicht zurückgedreht; sieh dafür eine Erweiterungsstelle vor.
5. **Personenfelder**: eine Registry `kern.personenfelder` mit `register(model, [felder])` und `unbedenklich(model, [felder])`. Ein Test schlägt fehl, wenn ein `CharField` oder `TextField` in keiner der beiden Listen steht. Ein Logging-Filter ersetzt registrierte Werte in Logmeldungen durch `‹…›`.
6. **Lauf-Gerüst**: Basisklasse für Management-Befehle, die einen `Lauf` öffnet, schließt, Ausnahmen als Fehler festhält und die drei Körbe zählt. Ein Beispiel-Lauf `kern_selbsttest`, der nur einen Arbeitsposten anlegt.
7. **Oberfläche**: Seite „Arbeitslisten“ (alle offenen Arbeitsposten, gruppiert nach Art, mit Aktionen per HTMX), Seite „Protokoll“ (letzte Änderungen, filterbar nach Modell, mit Knopf „Zurücknehmen“), Seite „Läufe“ (letzte Läufe mit Status). Alles am Handy bedienbar.

## Fertig, wenn

- Abnahme Schritt 0, Punkt 2: Eine Teständerung steht im Protokoll und lässt sich zurücknehmen; ein Test zeigt das.
- Ohne zweiten Faktor kommt niemand über die Anmeldeseite hinaus (Test).
- Der Registry-Test schlägt bei einem neuen, nicht markierten Textfeld an (Test mit Testmodell).
- `kern_selbsttest` erzeugt einen Lauf und einen Arbeitsposten, beides ist in der Oberfläche sichtbar.

Am Ende: Branch `schritt-0b-kern`, Pull Request, kurzer Bericht.
