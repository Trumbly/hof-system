# Schritt 1 – Bank

Lies `CLAUDE.md`, im Bauplan „Schritt 1: Bank“ und „Umzug und Parallelbetrieb“, in der Prozessliste H3.1 bis H3.3. Leg mir zuerst deinen Plan vor und warte auf mein OK.

## Ausgangslage

Den bisherigen Code von hof-fints lege ich unter `../hof-fints` ab. Lies dort nur Python-Code und die README. **Nicht lesen:** Caches, Datenordner, `regeln*.yaml`, `zahlweise*.yaml`, `.env` – darin stehen Zahlernamen und Zugangsdaten. Wenn du das Format einer dieser Dateien brauchst, frag mich nach einer Beschreibung.

- Vivid läuft über Enable Banking (PSD2, nur lesend) und kommt auf den Server.
- Das VR-Konto läuft über FinTS und bleibt auf dem hof-mac, bis es im ersten Quartal 2027 endet. Die PIN kommt nie auf den Server.

## Aufgaben

1. App `bank`: Modelle `Bankkonto` (Bank, Mandant, Bezeichnung, IBAN als Personenfeld nur für fremde Konten; eigene Konten unbedenklich), `Bankfreigabe` (Konto, gültig bis), `Bankabruf` (Konto, Zeitraum, Zähler), `Bankumsatz` (Betrag, Richtung, Buchungsdatum, Valuta, Gegenpartei, Gegen-IBAN, Verwendungszweck, Rohdaten, Fingerabdruck). Gegenpartei, Gegen-IBAN, Zweck und Rohdaten sind Personenfelder.
2. Fingerabdruck gegen Doppelte: aus Konto, Datum, Betrag, Gegen-IBAN, Zweck und laufender Nummer am Tag; ein zweiter Abruf erzeugt keine Doppelten (Sonderfall 1 im Testhof).
3. Lauf `bank_abruf` für Vivid: übernimm die Anbindung an Enable Banking aus hof-fints, mit einem austauschbaren Client und einem Fake für Tests.
4. Endpunkt für die VR-Zulieferung: Der hof-mac schickt Umsätze per HTTPS über Tailscale an `/api/bank/zulieferung`, mit eigenem Token, nur für dieses Konto. Dazu ein kleines Skript `zulieferung/vr_hochladen.py` für den hof-mac, das den bestehenden FinTS-Abruf von hof-fints nutzt.
5. Import alter Umsätze (H3.3): Befehl `bank_import` mit `--probelauf`, Vorschau der Zähler, Prüfung auf Doppelte. Das Format beschreibe ich dir oder du nimmst das Format des Vivid-Exports als erfundene Beispieldatei in `tests/fixtures/`.
6. Saldo-Prüfung: Der Abruf liest den Kontostand mit und legt einen Arbeitsposten an, wenn Summe der Umsätze und Saldo auseinanderlaufen.
7. Bankfreigabe: 14 Tage vor Ablauf ein Arbeitsposten mit Link zur Erneuerung (H3.2).
8. Seiten: Konten mit Saldo und letztem Abruf, Umsätze mit Filter, Import.
9. Testhof-Modul `testhof/bank.py`: 2 Konten, 24 Monate, rund 1.800 Umsätze, Sonderfälle „doppelter Umsatz“ und „Rückbuchung“.
10. systemd-Timer für `bank_abruf` täglich um 06:00.

## Fertig, wenn (Abnahme Schritt 1)

- Ein Test zeigt: zweimaliger Abruf desselben Zeitraums erzeugt keine Doppelten.
- Ein Test zeigt: Saldo und Umsätze stimmen im Testhof je Tag überein, und eine absichtliche Lücke wird ein Arbeitsposten.
- Der Import läuft im Probelauf ohne Änderung und meldet nur Zähler.
- Eine ablaufende Bankfreigabe erzeugt 14 Tage vorher einen Arbeitsposten.
- Die Prüfung über 14 Tage ohne Lücke und der Import ab 01.01.2026 laufen danach bei mir auf dem Server.

Am Ende: Branch `schritt-1-bank`, Pull Request, Bericht mit den Schritten, die ich auf Server und hof-mac ausführen muss.
