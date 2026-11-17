# Schritt 0c – Testhof-Generator

Lies `CLAUDE.md` und im Bauplan den Abschnitt „Testdaten“. Leg mir zuerst deinen Plan vor und warte auf mein OK.

## Ziel

Ein Generator, der einen erfundenen Hof in der Größe des echten erzeugt. Er wächst mit jedem Bauschritt: Jede App bringt ihr eigenes Modul für den Generator mit.

## Aufgaben

1. Paket `testhof/` mit einem Befehl `manage.py testhof --seed 42 [--leeren]`. Gleicher Seed ergibt exakt dieselben Daten.
2. Aufbau in Modulen, je App eines (`testhof/kern.py`, später `testhof/bank.py` …), aufgerufen in fester Reihenfolge. Ein Modul meldet sich mit seinen Abhängigkeiten an.
3. Für jetzt: Mandanten (GbR, Nachlass, KG-Linie, privat), 3 Objekte mit erfundenen Adressen, ein Benutzer `test@testhof.invalid` mit festem TOTP-Geheimnis nur für dev und Tests.
4. Eine Liste der Sonderfälle aus dem Bauplan (16 Fälle) als Datenstruktur `testhof/sonderfaelle.py` mit Name, Beschreibung und den Schritten, die ihn prüfen. Jeder Fall bekommt später sein Erzeugungsmodul; für jetzt nur die Liste und ein Test, der meldet, welche Fälle noch kein Modul haben (als Warnung, nicht als Fehler).
5. Erfundene Namen nur mit `Faker('de_DE')`. Alle E-Mail-Adressen enden auf `.invalid`, alle IBANs sind gültig berechnete Test-IBANs mit erfundener Bankleitzahl, alle Telefonnummern aus dem fiktiven Bereich.
6. Eine Fixture `testhof` für pytest, die den Generator einmal pro Testlauf in einer Transaktion aufbaut.
7. Der Generator weigert sich zu laufen, wenn `DJANGO_ENV=prod` gesetzt ist.

## Fertig, wenn

- `make dev` und `manage.py testhof --seed 42` ergeben einen Hof, der in der Oberfläche sichtbar ist.
- Zwei Läufe mit demselben Seed ergeben identische Daten (Test über einen Hash der Tabellen).
- Der Generator bricht in `prod` mit klarer Meldung ab (Test).
- Abnahme Schritt 0, Punkt 4: Die Pipeline nutzt den Testhof; im Repository liegen keine Echtdaten.

Am Ende: Branch `schritt-0c-testhof`, Pull Request, kurzer Bericht.
