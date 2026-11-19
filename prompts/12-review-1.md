# Review 1 – Korrekturen nach dem ersten Durchlauf (19.11.2026)

Lies `CLAUDE.md`. Diese Punkte kommen aus einem Durchgang durch die laufende Anwendung mit dem Testhof. Leg mir zuerst einen Plan vor, in welcher Reihenfolge und in welchen Pull Requests du sie angehst, und warte auf mein OK. Die Punkte 1 bis 4 gehen vor allem anderen.

## Muss vor dem ersten echten Import behoben sein

1. **Eine Wahrheit für „offen“.** Die Seite Offene Posten zeigt 859 überfällige Posten über 209.533,70 €, das Cockpit 3 Verträge im Rückstand über 117,15 €. Ursache: Die Sollstellung beginnt im November 2024 (`HOF_SOLL_BEGINN`), Abgleich und Mahnlauf werten erst ab einem späteren Datum aus.
   - Die Sollstellung beginnt je Vertrag mit dem Stichtag des Umzugs. Was davor offen war, kommt als ein Posten „Vortrag“ je Vertrag.
   - Eine Funktion `soll.services.offen(vertrag=None, stichtag=None)` liefert offene Posten und Summen; Cockpit, Offene Posten, Rückstände, Mahnlauf und Status-MCP nutzen nur sie.
   - Die Summe auf der Seite Offene Posten zählt alle Treffer des Filters, nicht nur die ersten 500 angezeigten.
   - Test: Testhof neu aufbauen, dann stimmen Cockpit, Offene Posten und Mahnlauf in Anzahl und Summe überein.
2. **Bankabruf und Bankfreigabe widersprechen sich.** Vivid zeigt „gescheitert“ und die Meldung „keine gültige Bankfreigabe“, obwohl die Freigabe bis 17.05.2027 gilt. Der Saldo steht auf „Ende 31.10.2026“, obwohl am 18.11. abgerufen wurde. Prüf, welche Freigabe der Abruf liest, und zeig den Saldo mit dem Datum des letzten erfolgreichen Abrufs.
3. **Testhof neu aufbauen räumt nicht auf.** Arbeitsposten #17 und #18 verweisen auf Konto #1, das es nicht mehr gibt. `testhof --leeren` muss auch Arbeitsposten, Läufe, Fristen, Schreiben und die Aufgaben im Backlog-Fake löschen. Ein Arbeitsposten, dessen Bezug fehlt, wird beim nächsten Lauf geschlossen und protokolliert.
4. **Senden trotz unvollständigem Briefkopf.** Das Schreiben #1 trägt „Hof (Absender nicht eingerichtet)“ und lässt sich trotzdem senden. Der Senden-Knopf bleibt gesperrt, solange Pflichtangaben des Briefkopfs fehlen; die Seite sagt, welche.
   - Vorlagen bekommen „freigegeben von“ (Pflichtfeld bei Freigabe). Der Testhof legt mindestens eine Vorlage als Entwurf an, damit der Probebetrieb im Mahnwesen getestet wird.

## Sollte bald behoben werden

5. **Gleichartige Arbeitsposten bündeln.** Die 50 Posten „Umsätze einordnen“ haben zwei Ursachen: Regeln #118 und #119 mit gleicher Priorität auf Konto #4, und ein wiederkehrender Umsatz ohne Regel auf Konto #3. Posten mit derselben Ursache erscheinen als eine Gruppe mit Aktion „für alle anwenden“. Zwei Regeln mit gleicher Priorität und überlappenden Bedingungen lassen sich gar nicht erst speichern.
6. **Fristen im Cockpit trennen:** „überfällig“ und „in den nächsten 14 Tagen“ als zwei Listen.
7. **Cockpit aufräumen:** „Belege zuordnen“ steht doppelt. Offene Freigaben nach Dringlichkeit sortieren, die ersten fünf Arten zeigen, den Rest hinter einem Link. Selbsttest und „Testhof legt keine Schreiben an“ erscheinen nicht als Arbeitsposten.
8. **Arbeitsposten mit passenden Aktionen:** „Bankabruf gescheitert“ bekommt „Erneut abrufen“ und „Freigabe erneuern“ statt „Erledigt“. Pfade in Meldungen sind Links. Jeder Posten hat einen Link zu seinem Bezug.
9. **Benachrichtigen ohne Kanal** ist in dev kein Fehler, sondern „übersprungen“. In prod bleibt es ein Fehler.
10. **Bagatellgrenze im Mahnwesen:** Rest unter einem einstellbaren Betrag (Standard 20 €) löst keine eigene Stufe aus, sondern wird beim nächsten Posten mitgenannt.
11. **Zahlen und Tabellen auf Deutsch:** Tausenderpunkt und Komma überall (`209.533,70 €`, `9.602,7`). Tabellenköpfe mit Abstand (Mahnfall: „BetragoffenStatus“).
12. **DEBUG:** Ein Test stellt sicher, dass `prod` mit `DEBUG=False` startet und eine eigene 404-Seite zeigt.
13. **Navigation gruppieren:** Heute (Start, Arbeitslisten) · Verträge (Stammdaten, Offene Posten, Mahnwesen) · Geld (Bank, Kassenbuch) · Hof (Zähler, Fristen, Dokumente, Schreiben) · System (Anbindungen, Protokoll, Läufe, Verwaltung, Konto). Am Handy als ausklappbare Gruppen.
14. **Simulierter Stichtag sichtbar:** Wenn ein fester Stichtag gesetzt ist (Testhof: 02.11.2026), zeigt jede Seite oben ein Band „Testhof · Stichtag 02.11.2026“. In prod gibt es keinen festen Stichtag; ein Test prüft das.

Am Ende: Pull Requests, Bericht mit erledigten Punkten und dem, was ich prüfen muss.
