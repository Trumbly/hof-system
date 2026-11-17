# Schritt 10 – Anbindung von Website und Verwaltio

Lies `CLAUDE.md`, im Bauplan „Schritt 10“, in der Prozessliste H1.6, W1, W10, V12. Leg mir zuerst deinen Plan vor und warte auf mein OK.

## Ausgangslage

Die Website noelle-wying.de läuft getrennt (eigenes Repository, Astro/Node, eigene Datenbank). Ich lege ihr Repository unter `../website` ab; lies dort Code und Schema, keine `.env` und keine Daten. Verwaltio ist noch im Test; die API-Dokumentation gebe ich dir, sobald ich sie habe.

## Aufgaben

1. App `anbindungen`.
2. **Website liest aus dem Hof-System** (W1): Endpunkt `/api/website/katalog` mit Einheiten, Typ, Fläche, Preis, frei ab, Bildern; nur vermarktbare Einheiten, keine Parteien. Zugang per Token, nur über Tailscale oder mit IP-Freigabe des Website-Servers. Auf der Website-Seite: Anpassung, die den Katalog von hier liest (als Pull Request im Website-Repository).
3. **Website meldet Ereignisse** (W10): Endpunkt für Buchung und Kündigung; legt eine `Buchungsanfrage` an der Einheit und einen Arbeitsposten an. Den Vertrag lege ich in v1 von Hand an.
4. Eine Rückgabe in Schritt 4 macht die Einheit ohne Handgriff wieder buchbar (Test über den Katalog).
5. **Verwaltio** (V12): Lauf `verwaltio_kennzahlen` nachts, holt je Wohneinheit offene Posten (Summe), Mahnfälle (Anzahl), Stand der NKA und Fristen; speichert als `Kennzahl` mit NW-ID, ohne Namen. Client mit Fake; echte Aufrufe erst mit der API-Dokumentation.
6. **ID-Abgleich** (H1.6): Lauf vergleicht NW-IDs in Website und Verwaltio mit dem Hof-System; jede fehlende oder abweichende ID wird `Abweichung` und Arbeitsposten.
7. Cockpit-Kachel „Wohnraum“ aus den Kennzahlen.

## Fertig, wenn (Abnahme Schritt 10)

- Website zeigt Belegung und Preise aus dem Hof-System; Rückgabe macht die Einheit wieder buchbar.
- Buchung auf der Website erscheint als Anfrage an der Einheit (Test).
- Cockpit zeigt für Wohnraum offene Posten, Mahnfälle, Stand der NKA und Fristen, ohne Namen.
- Jede fehlende oder abweichende ID steht in der Arbeitsliste (Test).

Am Ende: Pull Requests in beiden Repositories, kurzer Bericht.
