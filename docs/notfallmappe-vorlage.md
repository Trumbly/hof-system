# Notfallmappe Hof-System – Vorlage

Diese Vorlage nennt jeden Zugang, den das Hof-System braucht, und **wo** er liegt. Zugangsdaten selbst (Passwörter, Schlüssel, PINs, Wiederherstellungscodes) gehören nie in dieses Dokument und nie ins Repository.

Ausgefüllt wird eine Kopie außerhalb des Repositorys: ausgedruckt im Tresor oder Ordner „Notfall“ und als Notiz im Passwortmanager. Stand und Unterschrift unten. Bei jeder Änderung eines Zugangs: Zeile anpassen, Datum setzen.

## 1. Wer hilft

| Rolle | Name | Erreichbar | Darf |
| --- | --- | --- | --- |
| Betreiber | Max | … | alles |
| Vertretung im Notfall | … | … | Server neu starten, Sicherung zurückspielen lassen |
| Technische Hilfe (extern) | … | … | nur mit Freigabe von Max, nur über Tailscale-Einladung |
| Steuerberatung / Kanzlei | … | … | keine Systemzugänge |

## 2. Zugänge

| Zugang | Wofür | Wo liegt er | 2. Faktor / Wiederherstellung liegt | Zuletzt geprüft |
| --- | --- | --- | --- | --- |
| Passwortmanager (Haupttresor) | öffnet alles Weitere | Master-Passwort: … | Notfallkit des Passwortmanagers: … | |
| Tailscale-Konto | Zugang zum Server, Mac, Handy | Passwortmanager, Eintrag „Tailscale“ | … | |
| Hetzner Cloud (Konsole) | Server neu starten, Konsole, Snapshots | Passwortmanager, Eintrag „Hetzner“ | … | |
| Hetzner Storage Box | Sicherungen (restic) | Konto im Hetzner-Robot; Benutzer `u…` im Passwortmanager | — | |
| restic-Passwort | entschlüsselt die Sicherungen; **ohne es sind alle Sicherungen verloren** | Server: `/etc/hof/restic-passwort`; Kopie im Passwortmanager „restic hof“; Ausdruck: … | — | |
| SSH-Schlüssel Storage Box | Server schreibt in die Storage Box | Server: `/root/.ssh/hof-storagebox` (neu erzeugbar) | — | |
| GitHub (Repository `hof-system`) | Code, Pipeline, Tags | Passwortmanager, Eintrag „GitHub“ | Wiederherstellungscodes: … | |
| Deploy-Key GitHub | Server holt Tags | Server: `/opt/hof/.ssh/github` (neu erzeugbar) | — | |
| Server-Umgebung `.env` | Datenbank-Passwort, Django-Schlüssel, Mail | Server: `/opt/hof/.env`; Kopie im Passwortmanager „hof .env“ | — | |
| Anmeldung Hof-System | Benutzer der Anwendung | Passwortmanager, Eintrag „Hof-System“ | TOTP in der Authenticator-App auf …; Wiederherstellungscodes: … | |
| Mail-Postfach kontakt@ (SMTP/IMAP) | Versand, Rechnungseingang (ab Schritt 3) | Passwortmanager | … | |
| LetterXpress | Briefversand (ab Schritt 3) | Passwortmanager; API-Schlüssel in `/opt/hof/.env` | … | |
| Enable Banking / Vivid | Bankabruf (ab Schritt 1) | Passwortmanager; Freigabe läuft ab am: … | Vivid-App auf … | |
| VR-Bank FinTS | Abruf auf dem hof-mac (ab Schritt 1) | PIN nur auf dem hof-mac / im Passwortmanager, nie auf dem Server | TAN-Verfahren: … | |
| Domain / DNS (Website) | nicht Teil des Hof-Systems, nur zur Abgrenzung | … | … | |

## 3. Wichtige Orte

| Was | Wo |
| --- | --- |
| Server | Hetzner Cloud, Projekt …, Name `hof-system`, Standort … |
| Adresse der Anwendung | `https://hof-system.<tailnet>.ts.net` (nur im Tailnet) |
| Sicherungen | Storage Box `u…`, Ordner `hof-restic` (restic, verschlüsselt) |
| Betriebshandbuch | `docs/betrieb.md` im Repository, Ausdruck bei dieser Mappe |
| Letzte erfolgreiche Rückspielprobe | Datum: … (aus `/var/lib/hof/rueckspielprobe-status.json`) |

## 4. Im Notfall in dieser Reihenfolge

1. Passwortmanager öffnen (Notfallkit, falls Max nicht erreichbar ist).
2. Tailscale: Gerät der helfenden Person ins Tailnet einladen.
3. Hetzner-Konsole: Läuft der Server? Sonst neu starten.
4. `docs/betrieb.md`, Abschnitt „Was tun bei Ausfall“.
5. Server verloren: neuer Server nach `docs/betrieb.md`, „Wiederherstellen“; dafür braucht es Storage Box, restic-Passwort, `.env` und GitHub.

Stand: … · geprüft von: … · nächste Prüfung: …
