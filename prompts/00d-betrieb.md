# Schritt 0d – Server, Ausrollen, Sicherung, Betriebshandbuch

Lies `CLAUDE.md` und im Bauplan „Technik und Betrieb“ und „Schritt 0“. Leg mir zuerst deinen Plan vor und warte auf mein OK.

Wichtig: Du schreibst Skripte und Anleitungen. Ausführen tue ich sie auf dem Server. Du verbindest dich nicht mit dem Server.

## Ausgangslage

- Ein Hetzner-Cloud-Server mit Ubuntu 24.04, im Tailscale-Netz unter dem Namen `hof-system`.
- Eine Hetzner Storage Box für Sicherungen.
- Zugang zum Server nur über Tailscale; der Server hat keinen offenen Port im Internet außer dem, den Tailscale selbst braucht.

## Aufgaben

1. `Dockerfile` für die Produktion (schlank, ohne Entwicklungswerkzeuge, läuft nicht als root) und `docker-compose.prod.yml` mit App (gunicorn), Postgres 16 und Caddy.
2. Caddy lauscht nur auf der Tailscale-Adresse und holt das Zertifikat über Tailscale (`*.ts.net`). Kein Port auf der öffentlichen Adresse.
3. `scripts/server-setup.sh`: Grundeinrichtung des Servers (Pakete, Docker, Firewall `ufw` mit „alles zu außer Tailscale“, automatische Sicherheitsupdates, eigener Benutzer). Idempotent, mit Erklärung jedes Schritts in Kommentaren.
4. `scripts/deploy.sh`: holt den getaggten Stand aus GitHub, baut, führt Migrationen aus, startet neu, prüft den Gesundheits-Endpunkt. Bricht bei Fehler ab und lässt die alte Version laufen.
5. Gesundheits-Endpunkt `/gesund` ohne Anmeldung, nur über Tailscale erreichbar, ohne Daten (nur „ok“ und Version).
6. `scripts/backup.sh` mit `restic`: täglicher Dump der Datenbank plus Dokumentenordner, verschlüsselt in die Storage Box, Aufbewahrung 14 täglich, 8 wöchentlich, 12 monatlich. Dazu ein systemd-Timer und eine Statusdatei, die der Lauf-Überwachung später sagt, ob die Sicherung lief.
7. `scripts/restore-probe.sh`: spielt die letzte Sicherung in eine getrennte Datenbank auf demselben Server zurück und vergleicht Zeilenzahlen je Tabelle. Gibt nur Zahlen aus.
8. systemd-Timer-Vorlage für Läufe (`hof-lauf@.timer`), damit spätere Dienste ihre Läufe nur noch eintragen.
9. `docs/betrieb.md`: zwei Seiten Betriebshandbuch – Ausrollen, Sicherung prüfen, Wiederherstellen, Bankfreigabe erneuern (Platzhalter), was tun bei Ausfall.
10. `docs/notfallmappe-vorlage.md`: welche Zugänge es gibt und wo sie liegen, ohne die Zugangsdaten selbst.

## Fertig, wenn

- Ich kann mit `server-setup.sh` und `deploy.sh` den Server aufsetzen und mich über Tailscale am Mac und am Handy mit zweitem Faktor anmelden (Abnahme Schritt 0, Punkt 1).
- Die Rückspielprobe lief einmal vollständig (Abnahme Schritt 0, Punkt 3) – das prüfe ich auf dem Server.
- Betriebshandbuch und Notfallmappe liegen vor (Abnahme Schritt 0, Punkt 5).

Am Ende: Branch `schritt-0d-betrieb`, Pull Request, Bericht mit einer nummerierten Liste der Befehle, die ich auf dem Server ausführen muss.
