# Betriebshandbuch Hof-System

Stand: Schritt 0d. Der Server `hof-system` (Hetzner Cloud, Ubuntu 24.04) ist nur über Tailscale erreichbar. Alle Befehle hier führt Max auf dem Server aus; Claude Code verbindet sich nie mit dem Server.

## Überblick

| Was | Wo |
| --- | --- |
| Adresse | `https://hof-system.<tailnet>.ts.net` (nur im Tailscale-Netz) |
| Gesundheit | `https://hof-system.<tailnet>.ts.net/gesund` → `ok <version>` |
| Dienste | Docker Compose, Projekt `hof-system`: `app` (gunicorn), `db` (Postgres 16), `caddy` |
| Code und Versionen | `/opt/hof/repo` (Klon), `/opt/hof/versionen/<tag>`, `/opt/hof/aktuell` → laufende Version |
| Umgebungswerte | `/opt/hof/.env` (Besitzer `hof`, `chmod 600`) |
| Dokumente | `/srv/hof/medien` (gehört UID 10001, dem Benutzer im Container) |
| Datenbank | Docker-Volume `hof-system_db` |
| Sicherung | restic in die Storage Box; Zugang in `/etc/hof/backup.env` und `/etc/hof/restic-passwort` |
| Status | `/var/lib/hof/backup-status.json`, `/var/lib/hof/rueckspielprobe-status.json` |
| Dumps vor jedem Ausrollen | `/opt/hof/vor-deploy/` (die letzten fünf) |

**Nur über Tailscale.** Caddy veröffentlicht 80 und 443 ausschließlich auf der Tailscale-IP, App und Datenbank haben keinen veröffentlichten Port. `ufw` lässt eingehend nur `tailscale0` und UDP 41641 (Tailscale) zu. Docker umgeht `ufw` bei veröffentlichten Ports; deshalb ist die Bindung an die Tailscale-IP die eigentliche Sperre, nicht `ufw`. Als zweite Schicht empfiehlt sich die Hetzner Cloud Firewall mit nur „UDP 41641 eingehend“. Das Zertifikat für `*.ts.net` holt Caddy über den Socket von `tailscaled`; im Tailscale-Admin müssen MagicDNS und „HTTPS Certificates“ eingeschaltet sein.

## Einrichtung (einmalig)

1. Tailscale installieren, `tailscale up`, Name `hof-system`. Im Admin MagicDNS und HTTPS einschalten.
2. Über Tailscale verbinden (`ssh root@hof-system`), `scripts/server-setup.sh` auf den Server kopieren, `sudo HOF_STORAGEBOX_BENUTZER=u123456 bash server-setup.sh`. Das Skript bricht ab, wenn die Sitzung nicht über Tailscale kommt.
3. Deploy-Key: `sudo -iu hof ssh-keygen -t ed25519 -f ~/.ssh/github -N ''`, den öffentlichen Teil bei GitHub als Deploy-Key (nur lesen) eintragen, in `~hof/.ssh/config` für `github.com` als `IdentityFile` setzen; dann `sudo -iu hof git clone git@github.com:<konto>/hof-system.git /opt/hof/repo`.
4. `/opt/hof/.env` anlegen (`chmod 600`, Besitzer `hof`). Dieselbe Datei geht an App und Datenbank:
   - `POSTGRES_DB=hof`, `POSTGRES_USER=hof`, `POSTGRES_PASSWORD=<lang, zufällig>`
   - `DATABASE_URL=postgres://hof:<dasselbe Passwort>@db:5432/hof`. Host ist der Dienstname `db`, Port 5432 im Docker-Netz. Benutzer, Passwort und Datenbank müssen mit den `POSTGRES_*`-Werten übereinstimmen. Postgres liest diese nur beim allerersten Start (leeres Volume); ein späterer Passwortwechsel geht nur mit `ALTER USER` in der Datenbank.
   - `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS=hof-system.<tailnet>.ts.net`, `DJANGO_CSRF_TRUSTED_ORIGINS=https://hof-system.<tailnet>.ts.net`, `EMAIL_URL`; die weiteren Schlüssel stehen in `.env.example`.
5. Ausrollen (siehe unten), dann Benutzer anlegen: `/opt/hof/aktuell/scripts/compose.sh exec app python manage.py benutzer_anlegen --email <adresse>`. Am Mac und am Handy (beide im Tailnet) anmelden und den zweiten Faktor einrichten.
6. Sicherung einrichten (siehe unten) und `sudo /opt/hof/aktuell/scripts/systemd-einrichten.sh`.

## Ausrollen

```sh
git tag v0.2.0 && git push origin v0.2.0                 # am Mac, nach grüner Pipeline
sudo -iu hof /opt/hof/aktuell/scripts/deploy.sh v0.2.0   # auf dem Server
```

`deploy.sh` holt den Tag, packt ihn nach `versionen/<tag>`, baut `hof-system:<tag>`, sichert die Datenbank nach `vor-deploy/`, führt `check --deploy` und die Migrationen in einem Einmal-Container aus, startet App und Caddy und fragt `/gesund` über Caddy ab, bis `ok <tag>` kommt (bis zu 120 s). Erst dann zeigt `aktuell` auf die neue Version.

- Scheitert Bauen, Prüfen oder Migrieren, läuft die alte Version unverändert weiter.
- Scheitert der Start oder `/gesund`, startet das Skript die alte Version wieder und endet mit Fehler.
- Migrationen dreht es nicht zurück. Deshalb gilt für Migrationen: nur hinzufügen; Spalten erst in einer späteren Version entfernen. Im Notfall liegt der Dump in `vor-deploy/` (Wiederherstellen siehe unten).
- Zurück auf eine ältere Version: `deploy.sh v0.1.0`. Ändern sich Dateien unter `deploy/systemd/`, danach `systemd-einrichten.sh` erneut ausführen.
- Hilfreich: `compose.sh ps`, `compose.sh logs --tail 100 app`, `docker stats`.

## Läufe eintragen

Läufe sind Management-Befehle (`LaufBefehl`) und werden von `hof-lauf@<name>.timer` gestartet; `hof-lauf@<name>.service` ruft `scripts/lauf.sh <name>` auf, das `manage.py <name>` im laufenden App-Container ausführt. Ein neuer Dienst trägt seinen Lauf so ein:

1. Datei `deploy/systemd/laeufe/<name>.conf` mit `[Timer]` und `OnCalendar=…` anlegen (Vorbild: `kern_selbsttest.conf`, montags 7 Uhr). Der Name ist der Befehlsname; ein Test prüft, dass es den Befehl gibt.
2. Ausrollen, dann `sudo /opt/hof/aktuell/scripts/systemd-einrichten.sh`.

Prüfen: `systemctl list-timers 'hof-*'`, `journalctl -u hof-lauf@<name> -n 50`, in der App die Seite „Läufe“. Von Hand starten: `sudo systemctl start hof-lauf@<name>.service`.

## Sicherung

**Einrichten.** Auf der Storage Box SSH einschalten, den Schlüssel aus `server-setup.sh` eintragen: `cat /root/.ssh/hof-storagebox.pub | ssh -p 23 u123456@u123456.your-storagebox.de install-ssh-key`. Dann als root:

```sh
openssl rand -base64 48 > /etc/hof/restic-passwort && chmod 600 /etc/hof/restic-passwort
printf 'RESTIC_REPOSITORY=sftp:hof-storagebox:hof-restic\n' > /etc/hof/backup.env && chmod 600 /etc/hof/backup.env
set -a; . /etc/hof/backup.env; set +a; RESTIC_PASSWORD_FILE=/etc/hof/restic-passwort restic init
```

Das restic-Passwort sofort in den Passwortmanager und in die Notfallmappe (Ort, nicht Wert). Ohne Passwort ist jede Sicherung wertlos.

**Ablauf.** `hof-backup.timer` startet täglich um 3:30 `scripts/backup.sh`: Dump der Datenbank und Zeilenzahlen je Tabelle aus demselben Datenbank-Snapshot, dazu `/srv/hof/medien`, ein restic-Snapshot mit Tag `hof`; danach `forget --keep-daily 14 --keep-weekly 8 --keep-monthly 12 --prune` und `restic check`.

**Prüfen.**

```sh
cat /var/lib/hof/backup-status.json      # ergebnis "ok", zeit von heute Nacht
systemctl list-timers 'hof-*'            # nächster Lauf
journalctl -u hof-backup -n 50
sudo bash -c 'set -a; . /etc/hof/backup.env; RESTIC_PASSWORD_FILE=/etc/hof/restic-passwort restic snapshots --tag hof'
```

**Rückspielprobe** (H11.2, Abnahme Schritt 0). Monatlich am 1. um 5:00 per Timer, von Hand mit `sudo systemctl start hof-rueckspielprobe.service` und `journalctl -u hof-rueckspielprobe -n 60`. Sie spielt die letzte Sicherung in die Datenbank `hof_probe` im selben Container, vergleicht die Zeilenzahl jeder Tabelle mit den mitgesicherten Zahlen, spielt den Medienordner in einen temporären Ordner zurück (mit `--verify`) und räumt danach auf. Ergebnis in `/var/lib/hof/rueckspielprobe-status.json`; Exit-Code ≠ 0 bei einer Abweichung. Vor dem Umzug der Echtdaten muss sie einmal mit `ok` gelaufen sein.

## Wiederherstellen

Immer zuerst den Fehler eingrenzen und den jetzigen Stand sichern (`sudo systemctl start hof-backup.service`), außer die Datenbank ist zerstört.

1. App anhalten: `/opt/hof/aktuell/scripts/compose.sh stop app caddy`.
2. Snapshot wählen: `restic snapshots --tag hof` (Umgebung wie oben), ID notieren.
3. Dump holen: `restic dump <id> /var/lib/hof/sicherung/datenbank.dump > /var/lib/hof/probe/wiederherstellen.dump` (oder einen Dump aus `/opt/hof/vor-deploy/`).
4. Datenbank ersetzen (alles gelöscht und neu):

   ```sh
   DB=$(/opt/hof/aktuell/scripts/compose.sh ps -q db)
   docker exec "$DB" sh -c 'dropdb -U "$POSTGRES_USER" --force "$POSTGRES_DB" && createdb -U "$POSTGRES_USER" "$POSTGRES_DB"'
   docker exec -i "$DB" sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --exit-on-error' < /var/lib/hof/probe/wiederherstellen.dump
   rm /var/lib/hof/probe/wiederherstellen.dump
   ```

5. Dokumente, falls nötig: `restic restore <id> --target / --include /srv/hof/medien`, danach `chown -R 10001:10001 /srv/hof/medien`.
6. Die Version ausrollen, zu der der Snapshot passt (`deploy.sh <tag>`), oder `compose.sh up -d app caddy`. `/gesund` prüfen, anmelden, Protokoll ansehen.

**Neuer Server:** Einrichtung 1–4 durchführen, `/etc/hof/` aus dem Passwortmanager wiederherstellen, einmal `deploy.sh <tag>` (legt die leere Datenbank an), dann Schritte 1–6 oben.

## Bank

Vivid läuft über Enable Banking (PSD2, nur lesend) auf dem Server. Das VR-Konto ruft der hof-mac über FinTS ab und liefert die Umsätze an `/api/bank/zulieferung`; die PIN bleibt auf dem hof-mac.

**Täglicher Ablauf.** Der hof-mac liefert um 5:30 die VR-Umsätze der letzten 14 Tage samt Saldo (`zulieferung/vr_hochladen.py`, launchd). Um 6:00 startet `hof-lauf@bank_abruf.timer` den Lauf `bank_abruf`: Er holt die Vivid-Umsätze seit dem letzten Umsatz (7 Tage Überlappung; Doppelte weist der Fingerabdruck ab) und den Saldo, prüft dann für beide Konten Saldo gegen Umsätze je Tag, die Bankfreigaben (14 Tage Vorlauf) und ob jedes Konto in den letzten zwei Tagen einen Abruf hatte. Was nicht passt, steht in der Arbeitsliste: „Saldo weicht ab“ (Konto-ID und Datum), „Bankabruf gescheitert“, „Bankfreigabe läuft ab“, „Bankumsätze fehlen“. Prüfen: Seite „Läufe“ und „Bank“, `journalctl -u hof-lauf@bank_abruf -n 50`.

**Einrichten (einmalig).**

1. Bei Enable Banking eine App anlegen (Produktion), den privaten Schlüssel als PEM nach `/opt/hof/geheim/enablebanking.pem` legen: `chown 10001:10001` und `chmod 400` (der Container liest ihn als UID 10001). Als Rückkehr-Adresse der App `https://hof-system.<tailnet>.ts.net/bank/freigabe/rueckkehr/` eintragen.
2. In `/opt/hof/.env`: `ENABLE_BANKING_APP_ID=<App-ID>` und `ENABLE_BANKING_SCHLUESSEL=/run/hof-geheim/enablebanking.pem` (der Ordner `/opt/hof/geheim` ist im Container nur lesbar unter `/run/hof-geheim` eingehängt). Optional `ENABLE_BANKING_REDIRECT_URL`, falls die Adresse anders lautet.
3. Ausrollen, dann die Konten anlegen (IDs merken):
   `compose.sh exec app python manage.py bank_konto_anlegen --mandant GBR --bank Vivid --bezeichnung "Vivid Geschäftskonto" --iban <IBAN> --zugang enable_banking --eb-bank-name "<Name der Bank bei Enable Banking>"` und dasselbe für VR mit `--zugang zulieferung`.
4. Vivid freigeben (siehe unten). VR: Token erzeugen mit `compose.sh exec app python manage.py bank_zulieferung_token --konto <VR-ID>` (erscheint nur einmal) und den hof-mac einrichten, wie im Kopf von `zulieferung/vr_hochladen.py` beschrieben.
5. `sudo /opt/hof/aktuell/scripts/systemd-einrichten.sh` trägt den Timer `bank_abruf` ein.

**Import alter Umsätze (H3.3).** Den Export nach `/srv/hof/medien/import/` auf dem Server kopieren (im Container `/app/medien/import/`; nie ins Repository), dann erst prüfen, dann übernehmen:

```sh
compose.sh exec app python manage.py bank_import /app/medien/import/vivid.csv --konto <id>              # Probelauf
compose.sh exec app python manage.py bank_import /app/medien/import/vivid.csv --konto <id> --ausfuehren
```

Der Bericht nennt nur Zähler und Fehlerarten. Übernommen werden nur Tage vor dem ersten Abruf des Kontos (Abruf und Export stellen Zwecke unterschiedlich dar); mit `--bis JJJJ-MM-TT` lässt sich die Grenze setzen. Die Spaltenzuordnung steht in `apps/bank/importformat.py`. Danach die Exportdatei löschen. Dasselbe geht über die Seite „Bank → Import“.

## Bankfreigabe erneuern

Vivid gibt den Zugriff für höchstens 180 Tage frei, VR (FinTS) etwa alle 90 Tage. 14 Tage vor Ablauf legt der Lauf `bank_abruf` einen Arbeitsposten „Bankfreigabe läuft ab“ an, je Freigabe genau einen; mit der neuen Freigabe ist er erledigt.

- **Vivid:** Im Arbeitsposten auf „Freigabe erneuern“ (oder „Bank → Konten → erneuern“), dann „Bei der Bank freigeben“. Es geht zu Vivid, dort in der App bestätigen; danach landet man wieder im Hof-System, die neue Freigabe steht mit Ablaufdatum unter „Konten“. Klappt die Rückkehr nicht, stimmt meist die Rückkehr-Adresse bei Enable Banking nicht.
- **VR:** Die Freigabe erneuert der FinTS-Abruf auf dem hof-mac (TAN in der VR-App). Danach auf der Seite „Freigabe erneuern“ des VR-Kontos eintragen, bis wann sie gilt; nur so kennt das System die Frist.
- Ist eine Freigabe schon abgelaufen, scheitert der Abruf („Bankabruf gescheitert“). Nach der Erneuerung holt der nächste Lauf die Lücke nach; von Hand: `sudo systemctl start hof-lauf@bank_abruf.service`.

## Was tun bei Ausfall

| Zeichen | Prüfen | Abhilfe |
| --- | --- | --- |
| Seite lädt nicht | Mac im Tailnet? `tailscale status`; `tailscale ping hof-system` | Tailscale-App neu verbinden; ist der Server weg: Hetzner-Konsole, Server neu starten |
| Zertifikatsfehler | `compose.sh logs caddy` | HTTPS im Tailscale-Admin an? `systemctl status tailscaled`; `compose.sh restart caddy` |
| `/gesund` meldet `fehler` | `compose.sh ps`, `compose.sh logs db` | Datenbank-Container prüfen, `compose.sh up -d db`; Platte voll? `df -h` |
| 502 von Caddy | `compose.sh logs --tail 100 app` | Letzte Version war kaputt: `deploy.sh <vorheriger tag>` |
| Sicherung `fehler` | `fehler_bei` in der Statusdatei, `journalctl -u hof-backup` | Storage Box erreichbar? `sftp -P 23 hof-storagebox`; bei „locked“: `restic unlock` (nur wenn keine Sicherung läuft) |
| Nach Neustart nichts erreichbar | `systemctl status docker tailscaled`, `compose.sh ps` | `compose.sh up -d` (startet mit der laufenden Version) |
| Server verloren | — | Wiederherstellen auf neuem Server, siehe oben; Notfallmappe |

**Monatliche Pflege** (15 Minuten): Status beider JSON-Dateien ansehen, `apt update && apt upgrade` (auch Docker; startet die Container kurz neu), `df -h`, Snapshots in der Storage Box prüfen, alte Arbeitsposten „Selbsttest“ abhaken.
