# Betriebshandbuch Hof-System

Stand: Schritt 0d. Der Server `hof-system` (Hetzner Cloud, Ubuntu 24.04) ist nur über Tailscale erreichbar. Alle Befehle hier führt Max auf dem Server aus; Claude Code verbindet sich nie mit dem Server.

## Überblick

| Was | Wo |
| --- | --- |
| Adresse | `https://hof-system.<tailnet>.ts.net` (nur im Tailscale-Netz) |
| Gesundheit | `https://hof-system.<tailnet>.ts.net/gesund` → `ok <version>` |
| Dienste | Docker Compose, Projekt `hof-system`: `app` (gunicorn), `db` (Postgres 16), `statusmcp` (Status-MCP, Schritt 11), `caddy` |
| Code und Versionen | `/opt/hof/repo` (Klon), `/opt/hof/versionen/<tag>`, `/opt/hof/aktuell` → laufende Version |
| Umgebungswerte | `/opt/hof/.env` (Besitzer `hof`, `chmod 600`) |
| Dokumente | `/srv/hof/medien` (gehört UID 10001, dem Benutzer im Container) |
| Datenbank | Docker-Volume `hof-system_db` |
| Sicherung | restic in die Storage Box; Zugang in `/etc/hof/backup.env` und `/etc/hof/restic-passwort` |
| Status | `/var/lib/hof/backup-status.json`, `/var/lib/hof/rueckspielprobe-status.json` |
| Dumps vor jedem Ausrollen | `/opt/hof/vor-deploy/` (die letzten fünf) |

**Nur über Tailscale.** Caddy veröffentlicht 80 und 443 ausschließlich auf der Tailscale-IP, App und Datenbank haben keinen veröffentlichten Port. `ufw` lässt eingehend nur `tailscale0` und UDP 41641 (Tailscale) zu. Docker umgeht `ufw` bei veröffentlichten Ports; deshalb ist die Bindung an die Tailscale-IP die eigentliche Sperre, nicht `ufw`. Als zweite Schicht empfiehlt sich die Hetzner Cloud Firewall mit nur „UDP 41641 eingehend“. Das Zertifikat für `*.ts.net` holt Caddy über den Socket von `tailscaled`; im Tailscale-Admin müssen MagicDNS und „HTTPS Certificates“ eingeschaltet sein.

## Einrichtung (einmalig)

**Eigener Server.** Das Hof-System läuft auf einem eigenen Hetzner-Server, nicht auf dem Host der Website und von Vlower (Bauplan E7). Dort hält Traefik die Ports 80 und 443 auf allen Adressen, Caddy käme nicht hoch. `server-setup.sh` würde dort Docker austauschen, mit ufw den öffentlichen SSH-Zugang der CI sperren und nachts neu starten. Ein kleiner Cloud-Server (2 vCPU, 4 GB) reicht. Die Website wird nur als Client angebunden: Ihr Host tritt dem Tailnet bei, eine ACL erlaubt ihm nur `hof-system:443` (siehe `docs/website-anbindung.md`).

1. Tailscale installieren, `tailscale up`, Name `hof-system`. Im Admin MagicDNS und HTTPS einschalten.
2. Über Tailscale verbinden (`ssh root@hof-system`), `scripts/server-setup.sh` auf den Server kopieren, `sudo HOF_STORAGEBOX_BENUTZER=u123456 bash server-setup.sh`. Das Skript bricht ab, wenn die Sitzung nicht über Tailscale kommt.
3. Deploy-Key: `sudo -iu hof ssh-keygen -t ed25519 -f ~/.ssh/github -N ''`, den öffentlichen Teil bei GitHub als Deploy-Key (nur lesen) eintragen, in `~hof/.ssh/config` für `github.com` als `IdentityFile` setzen; dann `sudo -iu hof git clone git@github.com:<konto>/hof-system.git /opt/hof/repo`.
4. `/opt/hof/.env` anlegen (`chmod 600`, Besitzer `hof`). Dieselbe Datei geht an App und Datenbank:
   - `POSTGRES_DB=hof`, `POSTGRES_USER=hof`, `POSTGRES_PASSWORD=<lang, zufällig>`
   - `DATABASE_URL=postgres://hof:<dasselbe Passwort>@db:5432/hof`. Host ist der Dienstname `db`, Port 5432 im Docker-Netz. Benutzer, Passwort und Datenbank müssen mit den `POSTGRES_*`-Werten übereinstimmen. Postgres liest diese nur beim allerersten Start (leeres Volume); ein späterer Passwortwechsel geht nur mit `ALTER USER` in der Datenbank.
   - `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS=hof-system.<tailnet>.ts.net`, `DJANGO_CSRF_TRUSTED_ORIGINS=https://hof-system.<tailnet>.ts.net`, `EMAIL_URL`; die weiteren Schlüssel stehen in `.env.example`.
   - Für Briefe: `HOF_ABSENDER_NAME`, `HOF_ABSENDER_ANSCHRIFT`, `HOF_ABSENDER_BANK`, `HOF_ABSENDER_TELEFON`, `HOF_ABSENDER_STEUERNUMMER`. Fehlen sie, sperrt der Briefkopf das Senden.
   - Je Anbindung, sobald sie genutzt wird: `ENABLE_BANKING_*`, `LETTERXPRESS_*`, `HOF_NOTION_*`, `HOF_NTFY_*`, `HOF_WEBSITE_*`, `VERWALTIO_*`, `HOF_BELEGE_IMAP_*`, `HOF_*_BEGINN`.
   - `HOF_STICHTAG` in prod **nicht** setzen; dort gilt das echte Datum.
5. Erstes Ausrollen, mit dem Skript aus dem Klon (`/opt/hof/aktuell` gibt es erst danach): `sudo -iu hof /opt/hof/repo/scripts/deploy.sh v0.1.0`. Die Datenbank ist leer, alle Migrationen laufen dabei. Dann Benutzer anlegen: `/opt/hof/aktuell/scripts/compose.sh exec app python manage.py benutzer_anlegen --email <adresse>`. Am Mac und am Handy (beide im Tailnet) anmelden und den zweiten Faktor einrichten.
6. Sicherung einrichten (siehe unten) und `sudo /opt/hof/aktuell/scripts/systemd-einrichten.sh`.

## Ausrollen

```sh
git tag v0.2.0 && git push origin v0.2.0                 # am Mac, nach grüner Pipeline
sudo -iu hof /opt/hof/aktuell/scripts/deploy.sh v0.2.0   # auf dem Server (beim allerersten Mal: /opt/hof/repo/scripts/deploy.sh)
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

Ein Lauf, der täglich in der festen Reihenfolge laufen soll, bekommt **keinen** eigenen Zeitplan, sondern meldet sich beim Tageslauf an (nächster Abschnitt).

## Tageslauf (Schritt 9)

Der Lauf `tageslauf` (täglich 6:00, `deploy/systemd/laeufe/tageslauf.conf`) ruft die angemeldeten Läufe in der Reihenfolge des Bauplans. Jeder Teil ist ein eigener Lauf auf der Seite „Läufe“; der Tageslauf selbst nennt in seiner Meldung jeden Schritt mit Status und Lauf-Nummer.

| Rang | Schritt | Lauf |
| --- | --- | --- |
| 10 | Bank abrufen | `bank_abruf` |
| 20 | Umsätze einordnen | `kassenbuch_einordnen` (hängt an `bank_abruf`) |
| 30 | Sollstellung prüfen | `soll_monat` (idempotent, darum täglich) |
| 40 | Zahlungen abgleichen | kommt mit Schritt 6 (`abgleich`) |
| 50 | Mahnfälle fortschreiben, montags | kommt mit Schritt 7 |
| 60 | Fristen prüfen | `fristen` (markiert dabei auch überfällige Eingangsrechnungen) |
| 65 | Ablesung zum Stichtag | `zaehler_stichtag` (tut außerhalb des Ablesefensters nichts) |
| 70, 75 | Kennzahlen aus Verwaltio, ID-Abgleich | `verwaltio_kennzahlen`, `id_abgleich` |
| 78 | Arbeitsposten ohne Bezug schließen | `arbeitsposten_pflege` (Vermerk im Hinweis, Protokoll) |
| 80 | Hof-Cockpit: Aufgaben im Hof-Backlog | `aufgaben_abgleich` |
| 85 | Kennzahlen für den Status-MCP | `statusmcp_aktualisieren` (Schritt 11) |
| 90 | Sicherung prüfen | `sicherung_pruefen` |
| 100 | Benachrichtigung | `benachrichtigen` |

- **Bricht ein Teil ab**, laufen die übrigen weiter. Der Tageslauf steht dann auf „mit Fehlern“, seine Meldung nennt den Schritt (nur mit der Art des Fehlers), und die Benachrichtigung meldet beide Läufe. Hängt ein späterer Schritt am gescheiterten (z. B. Einordnen am Bankabruf), läuft er trotzdem und bekommt den Vermerk „lief trotz Fehler bei …“.
- **Von Hand:** `sudo systemctl start hof-lauf@tageslauf.service`, oder `compose.sh exec app python manage.py tageslauf --ohne bank_abruf` bzw. `--stichtag 2026-11-16` (wie an einem Montag).
- **Eigene Zeitpläne entfallen.** Seit dem Tageslauf gibt es `bank_abruf.conf`, `kassenbuch_einordnen.conf`, `soll_monat.conf`, `fristen.conf`, `zaehler_stichtag.conf`, `verwaltio_kennzahlen.conf` und `id_abgleich.conf` nicht mehr. `systemd-einrichten.sh` trägt die alten Timer beim nächsten Aufruf selbst aus (Schritt 3 des Skripts); also nach dem Ausrollen einmal `sudo /opt/hof/aktuell/scripts/systemd-einrichten.sh`. Eigene Zeitpläne behalten nur Läufe mit eigenem Rhythmus: `benachrichtigen` (dreimal täglich), `aufgaben_abgleich` (alle 30 Minuten), `versandstand_abrufen` (werktags), `kassenbuch_monatsabschluss` (am 3.), `kern_selbsttest` und `belege_postfach` (stündlich). Die Belegpflicht prüft der Monatsabschluss, nicht der Tageslauf.
- **Sicherung prüfen:** liest `HOF_BACKUP_STATUS` (Standard `/var/lib/hof/backup-status.json`; der Ordner `/var/lib/hof` ist im App-Container nur lesend eingehängt, `HOF_STATUS_DIR`). Fehlt die Datei, steht `ergebnis` nicht auf `ok` oder ist sie älter als `HOF_BACKUP_HOECHSTALTER_STUNDEN` (26), endet der Lauf mit Fehler und legt einen Arbeitsposten „Sicherung prüfen“ an; mit der nächsten guten Sicherung schließt er sich.
- **Neue Schritte** melden sich in `AppConfig.ready()` an: `tageslauf.schritt_anmelden(name, reihenfolge=…, nur_wochentag=…, abhaengig_von=(…))`, siehe `apps/cockpit/tageslauf.py`.

## Hof-Cockpit und Hof-Backlog (Schritt 9)

Die Startseite ist das Hof-Cockpit. Jede Kachel liest beim Aufruf direkt aus der Datenbank (keine Kopien) und führt auf ihre Liste: offene Freigaben je Art, Belege zuordnen, Fristen der nächsten 14 Tage, Rückstände (ab Schritt 6), offene Posten, offene Eingangsrechnungen (fällig in 14 Tagen, überfällig), Leerstand, Datenstand-Lücken, Wohnraum (Verwaltio), Zustand der Läufe und letzte Sicherung. Am Handy stehen die Kacheln untereinander.

**Aufgaben im Hof-Backlog (Notion).** Für die Arten „Mahnungen senden“, „Zahlungen klären“, „Rechnung zahlen“, „Frist fällig“ und „Ablesung fehlt“ (`HOF_NOTION_ARTEN` in `hof/settings/base.py`) steht im Hof-Backlog je Art eine Aufgabe, z. B. „3 Mahnungen senden“, mit NW-IDs bzw. Objekt-Kürzeln, der frühesten Fälligkeit und dem Link auf die Arbeitsliste; keine Namen, keine Beträge. Kommen Posten dazu oder werden welche erledigt, aktualisiert sich die Aufgabe; ist alles erledigt, steht sie auf Erledigt. Das Anlegen eines Postens merkt nur vor; Notion ruft erst der Lauf `aufgaben_abgleich` (alle 30 Minuten und im Tageslauf). Fällt Notion aus, geht im Hof-System nichts verloren: Der Lauf steht auf „mit Fehlern“ und holt es beim nächsten Mal nach.

Einrichten in `/opt/hof/.env` (Token nur auf dem Server):

- `HOF_NOTION_TOKEN=<Token der Integration>`; die Integration in Notion mit der Datenbank „Hof-Backlog“ verbinden.
- `HOF_NOTION_DATENBANK_ID=<ID der Datenbank>`; hat die Datenbank mehrere Datenquellen, stattdessen die ID der Datenquelle und `HOF_NOTION_PARENT=data_source_id`.
- Namen der Eigenschaften, falls sie abweichen: `HOF_NOTION_FELD_TITEL` (Standard `Name`), `HOF_NOTION_FELD_STATUS` (`Status`), `HOF_NOTION_STATUS_TYP` (`status` oder `select`), `HOF_NOTION_FELD_FAELLIG` (`Fällig`), `HOF_NOTION_FELD_LINK` (`Link`), `HOF_NOTION_FELD_TEXT` (`Details`, leer: kein Textfeld), `HOF_NOTION_WERT_OFFEN` (`Offen`), `HOF_NOTION_WERT_ERLEDIGT` (`Erledigt`).
- Ohne Token endet `aufgaben_abgleich` mit „nicht eingerichtet“, ohne Fehler. Testen: `compose.sh exec app python manage.py aufgaben_abgleich`.

## Benachrichtigung (H11.1)

Der Lauf `benachrichtigen` (7:30, 12:30, 18:30) meldet sich nur, wenn ein Lauf in den letzten 24 Stunden abgebrochen ist oder Fehler hatte, oder wenn Arbeitsposten bestimmter Arten offen warten (Standard: Frist fällig, die Bank-Posten Bankfreigabe läuft ab, Bankfreigabe fehlt, Bankabruf gescheitert, Bankumsätze fehlen und Saldo weicht ab, dazu Mahnungen senden, Zahlungen klären, Rechnung zahlen, Schreiben senden). Inhalt nur Zähler und Links, keine Namen oder Beträge. Dieselbe Lage kommt höchstens einmal am Tag; neu meldet sich nur, was dazukommt. Ohne Anlass bleibt es still.

Einrichten in `/opt/hof/.env`:

- `HOF_BASIS_URL=https://hof-system.<tailnet>.ts.net` (Ziel der Links)
- Mail: `HOF_BENACHRICHTIGUNG_MAIL=<deine Adresse>`; versendet über `EMAIL_URL`.
- Push über ntfy: `HOF_NTFY_URL=https://ntfy.sh` (oder der eigene Server), `HOF_NTFY_THEMA=<langes, zufälliges Thema>`, bei geschütztem Thema `HOF_NTFY_TOKEN=tk_…`. In der ntfy-App dasselbe Thema abonnieren.
- Optional: `HOF_BENACHRICHTIGUNG_ARTEN` (Arten, durch Komma getrennt), `HOF_BENACHRICHTIGUNG_FENSTER_STUNDEN` (Standard 24).

Ein Kanal ohne Einstellung bleibt aus; `check --deploy` warnt, wenn keiner eingerichtet ist oder `HOF_BASIS_URL` fehlt. Ist gar kein Kanal eingerichtet, endet der Lauf auf dem Server (`DJANGO_ENV=prod`) „mit Fehlern“, in Entwicklung und Tests dagegen „übersprungen“ (kein Fehler, keine Meldung). Fällt ein Kanal aus, steht der Lauf auf „mit Fehlern“. Die Seite „Läufe“ zeigt bei jedem Lauf `benachrichtigen`, ob und über welche Kanäle gemeldet wurde. Testen: `compose.sh exec app python manage.py benachrichtigen`.

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

**Täglicher Ablauf.** Der hof-mac liefert um 5:30 die VR-Umsätze der letzten 14 Tage samt Saldo (`zulieferung/vr_hochladen.py`, launchd). Um 6:00 startet der Tageslauf als ersten Schritt `bank_abruf`: Er holt die Vivid-Umsätze seit dem letzten Umsatz (7 Tage Überlappung; Doppelte weist der Fingerabdruck ab) und den Saldo, prüft dann für beide Konten Saldo gegen Umsätze je Tag, die Bankfreigaben (14 Tage Vorlauf) und ob jedes Konto in den letzten zwei Tagen einen Abruf hatte. Was nicht passt, steht in der Arbeitsliste: „Saldo weicht ab“ (Konto-ID und Datum), „Bankabruf gescheitert“, „Bankfreigabe läuft ab“, „Bankumsätze fehlen“. Prüfen: Seite „Läufe“ und „Bank“, `journalctl -u hof-lauf@tageslauf -n 50`.

**Einrichten (einmalig).**

1. Bei Enable Banking eine App anlegen (Produktion), den privaten Schlüssel als PEM nach `/opt/hof/geheim/enablebanking.pem` legen: `chown 10001:10001` und `chmod 400` (der Container liest ihn als UID 10001). Als Rückkehr-Adresse der App `https://hof-system.<tailnet>.ts.net/bank/freigabe/rueckkehr/` eintragen.
2. In `/opt/hof/.env`: `ENABLE_BANKING_APP_ID=<App-ID>` und `ENABLE_BANKING_SCHLUESSEL=/run/hof-geheim/enablebanking.pem` (der Ordner `/opt/hof/geheim` ist im Container nur lesbar unter `/run/hof-geheim` eingehängt). Optional `ENABLE_BANKING_REDIRECT_URL`, falls die Adresse anders lautet.
3. Ausrollen, dann die Konten anlegen (IDs merken):
   `compose.sh exec app python manage.py bank_konto_anlegen --mandant GBR --bank Vivid --bezeichnung "Vivid Geschäftskonto" --iban <IBAN> --zugang enable_banking --eb-bank-name "<Name der Bank bei Enable Banking>"` und dasselbe für VR mit `--zugang zulieferung`.
4. Vivid freigeben (siehe unten). VR: Token erzeugen mit `compose.sh exec app python manage.py bank_zulieferung_token --konto <VR-ID>` (erscheint nur einmal) und den hof-mac einrichten, wie im Kopf von `zulieferung/vr_hochladen.py` beschrieben.
5. `sudo /opt/hof/aktuell/scripts/systemd-einrichten.sh` trägt den Tageslauf ein, der `bank_abruf` aufruft.

**Import alter Umsätze (H3.3).** Den Export nach `/srv/hof/medien/import/` auf dem Server kopieren (im Container `/app/medien/import/`; nie ins Repository), dann erst prüfen, dann übernehmen:

```sh
compose.sh exec app python manage.py bank_import /app/medien/import/vivid.csv --konto <id>              # Probelauf
compose.sh exec app python manage.py bank_import /app/medien/import/vivid.csv --konto <id> --ausfuehren
```

Der Bericht nennt nur Zähler und Fehlerarten. Übernommen werden nur Tage vor dem ersten Abruf des Kontos (Abruf und Export stellen Zwecke unterschiedlich dar); mit `--bis JJJJ-MM-TT` lässt sich die Grenze setzen. Die Spaltenzuordnung steht in `apps/bank/importformat.py`. Danach die Exportdatei löschen. Dasselbe geht über die Seite „Bank → Import“.

## Kassenbuch

**Täglicher Ablauf.** Im Tageslauf, direkt nach dem Bankabruf, ordnet `kassenbuch_einordnen` die neuen Umsätze nach den Zuordnungsregeln ein (Seite „Kassenbuch → Regeln“). Eindeutig ist ein Umsatz, wenn genau eine Regel mit der höchsten Priorität passt; dann wird er gebucht. Sonst steht er in der Arbeitsliste „Umsätze einordnen“ mit Zuordnen, Aufteilen, Regel anlegen und Zurückstellen. Eine neue Regel zeigt erst eine Vorschau mit Zählern und bucht offene Umsätze rückwirkend erst nach dem Klick. Gebuchtes wird nie geändert: „Stornieren“ am Umsatz oder „Zurücknehmen“ im Protokoll legen eine Stornobuchung an.

**Monatsabschluss (H6.6).** Am 3. jedes Monats um 7 Uhr schließt `kassenbuch_monatsabschluss` den Vormonat je Mandant mit Bankkonto ab: alle Umsätze eingeordnet, Saldo im Buch gleich Saldo der Bank (Kontostand am Monatsende; fehlt er, vom nächsten späteren Kontostand zurückgerechnet). Lücken stehen als „Monatsabschluss offen“ in der Arbeitsliste; nach dem Klären dort „Erneut prüfen“. Danach ist der Monat gesperrt (auch in der Datenbank). Ist ein Monat für alle Mandanten abgeschlossen, liegen die Monatsberichte je Objekt als PDF unter `/srv/hof/medien/kassenbuch/monatsberichte/<Jahr>/` und auf der Seite „Kassenbuch → Monatsbericht“.

**Einrichten (einmalig).**

1. In `/opt/hof/.env`: `HOF_KASSENBUCH_BEGINN=2026-01-01` (Läufe beginnen mit 2026; ältere Umsätze bleiben außen vor).
2. Ausrollen; `sudo /opt/hof/aktuell/scripts/systemd-einrichten.sh` trägt den Tageslauf (mit `kassenbuch_einordnen`) und den Timer `kassenbuch_monatsabschluss` ein.
3. Kategorien importieren (Datei nach `/srv/hof/medien/import/`, Spalten `Kategorie;Art;Mandant`, Zuordnung in `apps/kassenbuch/importformat.py`), erst prüfen, dann anlegen; danach die Datei löschen:

```sh
compose.sh exec app python manage.py kassenbuch_kategorien_import /app/medien/import/kategorien.csv               # Probelauf
compose.sh exec app python manage.py kassenbuch_kategorien_import /app/medien/import/kategorien.csv --ausfuehren
```

4. Regeln auf der Seite „Kassenbuch → Regeln“ anlegen, dann `compose.sh exec app python manage.py kassenbuch_einordnen` und die Arbeitsliste abarbeiten.
5. 2026 nachholen: `compose.sh exec app python manage.py kassenbuch_monatsabschluss --ab 2026-01 --bis 2026-12` (bzw. bis zum Vormonat). Er schließt der Reihe nach ab und hält beim ersten Monat mit Lücken an; nach dem Klären erneut starten.

**Warum kein Monat geschlossen ist (Review 2, A11).** Abgeschlossen wird der Reihe nach: Ein Monat braucht den abgeschlossenen Vormonat. Hat der erste Monat eine Lücke, bleiben alle späteren offen. Im Testhof ist das gewollt: Ohne `HOF_KASSENBUCH_BEGINN` beginnt die Kette beim ersten Umsatz (11/2024), und dort sind Umsätze nicht eingeordnet (etwa „Material Baumarkt“ ohne Regel, die mehrdeutige Versicherung); also bleibt alles ab 11/2024 offen. In Produktion setzt `HOF_KASSENBUCH_BEGINN` den Anfang. Die Seite „Kassenbuch → Monatsabschlüsse“ nennt je Mandant (mit Namen) den nächsten offenen Monat, verlinkt die offenen Umsätze und hat für einen bereiten Monat den Knopf „Monat jetzt abschließen“ (mit Rückfrage).

## Bankfreigabe erneuern

Vivid gibt den Zugriff für höchstens 180 Tage frei, VR (FinTS) etwa alle 90 Tage. 14 Tage vor Ablauf legt der Lauf `bank_abruf` einen Arbeitsposten „Bankfreigabe läuft ab“ an, je Freigabe genau einen; mit der neuen Freigabe ist er erledigt.

- **Vivid:** Im Arbeitsposten auf „Freigabe erneuern“ (oder „Bank → Konten → erneuern“), dann „Bei der Bank freigeben“. Es geht zu Vivid, dort in der App bestätigen; danach landet man wieder im Hof-System, die neue Freigabe steht mit Ablaufdatum unter „Konten“. Klappt die Rückkehr nicht, stimmt meist die Rückkehr-Adresse bei Enable Banking nicht.
- **VR:** Die Freigabe erneuert der FinTS-Abruf auf dem hof-mac (TAN in der VR-App). Danach auf der Seite „Freigabe erneuern“ des VR-Kontos eintragen, bis wann sie gilt; nur so kennt das System die Frist.
- Ist eine Freigabe schon abgelaufen, scheitert der Abruf („Bankabruf gescheitert“). Nach der Erneuerung holt der nächste Lauf die Lücke nach; von Hand: `compose.sh exec app python manage.py bank_abruf`.

## Versand einrichten (Schritt 3c)

Ohne diese Werte meldet der Versand „nicht eingerichtet“, und jedes Schreiben bleibt „bereit“. In `/opt/hof/.env`:

- `EMAIL_URL=smtp+tls://kontakt%40<domain>:<passwort>@<smtp-host>:587` und `HOF_MAIL_ABSENDER=kontakt@<domain>`.
- Kopie in „Gesendet“: `HOF_IMAP_URL=imaps://kontakt%40<domain>:<passwort>@<imap-host>:993`, `HOF_IMAP_ORDNER_GESENDET` (Standard `Sent`; je nach Anbieter z. B. `INBOX.Sent` oder `Gesendet`). Ohne `HOF_IMAP_URL` geht keine Mail hinaus.
- LetterXpress: `LETTERXPRESS_BENUTZER`, `LETTERXPRESS_APIKEY` (Kundenbereich → Mein Konto → Zugangsdaten → LXP API), `LETTERXPRESS_MODUS=test` (Aufträge landen nur in der Postbox). `live` erst nach dem Testbrief und Max' Freigabe.
- Briefkopf: `HOF_ABSENDER_NAME`, `HOF_ABSENDER_ANSCHRIFT` (Zeilen mit `;` trennen), `HOF_ABSENDER_TELEFON`, `HOF_ABSENDER_BANK`, `HOF_ABSENDER_STEUERNUMMER`. Der Briefkopf selbst ist eine Vorlage der Art „Briefkopf“; sie muss freigegeben sein.

Vorlagen aus Notion übernehmen: Markdown-Export in den Container kopieren, dann `compose.sh exec app python manage.py vorlagen_import /pfad` (Probelauf), danach mit `--ausfuehren`. Alles kommt als Entwurf; freigeben auf der Seite „Vorlagen“.

Den Lauf `versandstand_abrufen` (werktags 10 und 16 Uhr) trägt `systemd-einrichten.sh` ein.

## Rechnungspostfach und Belege (Schritt 3a)

Der Lauf `belege_postfach` (stündlich 7 bis 21 Uhr) legt jede PDF-Anlage des Rechnungspostfachs als Beleg in die Ablage; Dubletten erkennt er an der Prüfsumme. Jeder neue Beleg wird ein Arbeitsposten „Belege zuordnen“. In `/opt/hof/.env`:

- `HOF_BELEGE_IMAP_URL=imaps://rechnungen%40<domain>:<passwort>@<imap-host>:993` (eigenes Postfach, nicht kontakt@), `HOF_BELEGE_IMAP_ORDNER` (Standard `INBOX`).
- `HOF_BELEGE_NACH_DEM_LESEN=markieren` (als gelesen markieren) oder `verschieben` mit `HOF_BELEGE_ZIELORDNER` (Standard `Abgelegt`; der Ordner muss im Postfach existieren).
- Belegpflicht im Monatsabschluss: `HOF_BELEGPFLICHT_ARTEN` (Standard `ausgabe`), `HOF_BELEGPFLICHT_AB_BETRAG` (Standard `0.00`), `HOF_BELEGPFLICHT_OHNE_KATEGORIEN` (Bezeichnungen mit Komma, z. B. `Kontoführung`). Fehlt ein Beleg, kommt ein Arbeitsposten „Belege fehlen“; der Abschluss geht trotzdem.
- Aufbewahrung je Dokumentart: `DOKUMENTE["LOESCHFRIST_JAHRE"]` in `hof/settings/base.py`. Gelöscht wird noch nichts (Löschlauf in v2).

Eingangsrechnungen (Schritt 3b): Wer beim Bestätigen eines Belegs „fällig am“ einträgt, bekommt eine offene Rechnung mit Arbeitsposten „Rechnung zahlen“ und einer Frist; drei Tage vorher erinnert der Lauf `fristen` (Posten „Frist fällig“), nach der Fälligkeit steht am Posten „Überfällig seit …“. Der Lauf `kassenbuch_einordnen` erkennt die Abbuchung an Betrag und Gegen-IBAN des Lieferanten oder an der Rechnungsnummer im Zweck, verbindet Buchung und Rechnung, schließt Posten und Frist und hängt den Beleg an die Buchung. Unsichere Treffer werden ein Arbeitsposten „Rechnung zuordnen“. Die Meldung ans Hof-Backlog in Notion (`apps/kern/aufgaben.py`) kommt mit Schritt 9.

Beleg und Abbuchung weichen ab (Review 2, A7): Passen Rechnungsnummer (im Zweck) oder Lieferant (Gegen-IBAN, dann höchstens 20 % Unterschied) zu einer Abbuchung, der Betrag aber nicht, entsteht ein Arbeitsposten „Beleg und Abbuchung weichen ab“. Ebenso, wenn ein schon verknüpfter Beleg (bzw. alle Teile einer Sammelrechnung) nicht mehr den Betrag seiner Abbuchung ergibt. Geprüft wird beim Bestätigen und Speichern eines Belegs, beim Verknüpfen und im Lauf `kassenbuch_einordnen`. Beide Beträge stehen auf der Seite des Belegs; der Hinweis am Posten (geht ins Hof-Backlog) nennt nur die Umsatz-ID. Ist die Abbuchung richtig (Skonto, Teilzahlung), den Posten erledigen; er kommt dann nicht wieder.

Damit der Lieferant einer Mail erkannt wird, auf „Dokumente → Lieferanten“ seine Absenderadresse eintragen; für die Abbuchung seine IBAN. Belege vom Handy: „Dokumente → Beleg hochladen“ (Foto oder PDF). Die Belegmappe je Objekt und Jahr entsteht auf „Dokumente → Belegmappe“.

## Stammdaten importieren (Schritt 4)

Reihenfolge: erst Einheiten, dann Parteien und Verträge. Beide Befehle laufen ohne Option als Probelauf; der Bericht nennt nur Zähler, NW-IDs, Vertragsnummern und Zeilennummern und darf mit Claude geteilt werden.

1. Notion-Export (CSV) und HelloHousing-Export (Mietverhältnisse, Mietverlauf) in den Container kopieren.
2. `compose.sh exec app python manage.py import_einheiten /pfad/einheiten.csv`, Bericht prüfen, dann mit `--ausfuehren`.
3. `compose.sh exec app python manage.py import_hellohousing /pfad/mieter.csv --mietverlauf /pfad/verlauf.csv`, Bericht prüfen, dann mit `--ausfuehren`.
4. Seite „Datenstand“: Kündigungsfrist, Kaution, Zahlweise und Scan je Vertrag aus dem PDF nachtragen.

**Zählung der Verträge (Review 2, A11).** Es gibt eine Zählung, `Vertrag.objects` (`apps/stammdaten/models.py`): „laufend“ heißt aktiv oder gekündigt. Diese Zahl steht in den Stammdaten und als Standard der Vertragsliste; Entwürfe und beendete Verträge stehen getrennt daneben. Früher zählte die Übersicht Entwürfe mit, die Liste auch beendete, daher 43/44/45. Katalog und Leerstand nutzen `bindend()` (mit Entwürfen), das Soll `mit_soll()` (mit beendeten).

Weichen die Spaltenköpfe ab, wird nur `apps/stammdaten/import_spalten.toml` angepasst. Kündigungsregeln je Vertragsart, Checklisten und Pflichtfelder stehen in `apps/stammdaten/konfiguration.toml`. Für Vertrag und Kündigungsbestätigung braucht es freigegebene Vorlagen der Arten „Vertrag“ (Name je Vertragsart, z. B. „Vertrag Garage“, sonst die neueste) und „Kündigungsbestätigung“; fehlt eine, entsteht ein Arbeitsposten „Schreiben erstellen“.

## Sollstellung (Schritt 5)

**Ablauf.** Jeden Tag im Tageslauf (idempotent; neu ist meist nur am Monatsersten etwas) stellt `soll_monat` die Sollposten aller Verträge bis zum Monatsende und gleicht sie mit den Konditionen ab. Jede Änderung am Vertrag (Kondition, Laufzeit, Kündigung, Übergabe, Rückgabe) stellt das Soll des Vertrags sofort neu: Offene Posten, die nicht mehr passen, werden storniert und neu erzeugt. Bezahlte oder teilweise bezahlte Posten bleiben; sie stehen als „Sollposten prüfen“ in der Arbeitsliste. Die Seite „Offene Posten“ zeigt alle offenen Posten mit Filter, je Vertrag mit Fälligkeit und Posten von Hand (Kaution, Sonstiges). Fälligkeit und Feiertage stehen in `apps/soll/konfiguration.toml` (Standard: dritter Werktag, Feiertage NRW).

**Einrichten (einmalig).**

1. In `/opt/hof/.env`: `HOF_SOLL_BEGINN=2026-07-01` (Perioden, die früher beginnen, führt HelloHousing; Vorschlag, Max entscheidet).
2. Ausrollen; `sudo /opt/hof/aktuell/scripts/systemd-einrichten.sh` trägt den Tageslauf ein, der `soll_monat` aufruft.
3. Soll bis Dezember stellen: `compose.sh exec app python manage.py soll_monat --stichtag 2026-12-01`.
4. Vergleich mit dem Soll-Export aus HelloHousing (CSV in den Container kopieren): `compose.sh exec app python manage.py soll_vergleich --export /pfad/soll.csv --von 2026-07-01 --bis 2026-12-31`. Der Bericht nennt je Vertrag nur Zähler, Vertragsnummer und NW-ID und darf mit Claude geteilt werden; danach die Datei löschen. Weichen die Spaltenköpfe ab, wird nur der Abschnitt `[vergleich_hellohousing]` in `apps/soll/konfiguration.toml` angepasst.

## Website und Verwaltio (Schritt 10)

Schnittstelle und Checkliste für die Website: `docs/website-anbindung.md`.

- **Token der Website:** `compose.sh exec app python manage.py website_token --bezeichnung "Website-Server"`. Der Token erscheint einmal; auf dem Website-Server als `HOF_API_TOKEN` eintragen. Wechsel ohne Ausfall mit `--behalten`, alle sperren mit `--sperren`.
- **Einstellungen** in `/opt/hof/.env`: `HOF_WEBSITE_URL` und `HOF_WEBSITE_TOKEN` (für den ID-Abgleich, Endpunkt `/api/hof/einheiten` der Website), optional `HOF_WEBSITE_IP_FREIGABE` (Tailscale-IP des Website-Servers), `VERWALTIO_URL` und `VERWALTIO_TOKEN`.
- **Läufe:** `verwaltio_kennzahlen` und `id_abgleich`, beide im Tageslauf (Rang 70 und 75). Ohne Einstellungen melden sie „nicht eingerichtet“ und enden ohne Fehler. Der echte Verwaltio-Client ist offen, bis die API-Dokumentation vorliegt; mit gesetzter `VERWALTIO_URL` bricht `verwaltio_kennzahlen` bis dahin mit `NotImplementedError` ab.
- **Arbeitsposten:** „Buchungsanfrage prüfen“ (Seite „Anbindungen → Buchungsanfragen“) und „ID-Abweichung“ (schließt sich beim nächsten Lauf, sobald behoben). Beide melden sich über die Benachrichtigung.

## Zähler (Schritt 8)

Import nach den Einheiten: `compose.sh exec app python manage.py import_zaehler /pfad/zaehler.csv --staende /pfad/staende.csv` (Probelauf; Bericht nennt Anzahlen, Zählernummern und Zeilennummern), dann mit `--ausfuehren`. Spalten in `apps/zaehler/import_spalten.toml`, Beispiel unter `tests/fixtures/zaehler/`.

Der Lauf `zaehler_stichtag` (täglich im Tageslauf, prüft selbst) legt im Ablesefenster (15.12. bis 15.01.) je Zähler ohne Stand zum 31.12. einen Arbeitsposten „Ablesung fehlt“ an; er meldet sich über die Benachrichtigung. Stichtag, Fenster und Vorlauf der Eichfrist stehen in `apps/zaehler/konfiguration.toml`. Vor dem Besuch: Zähler → „Liste für deinen nächsten Besuch“ drucken, vor Ort Zähler → „Ablesen <Objekt>“ am Handy.

## Abgleich (Schritt 6)

**Ablauf.** Im Tageslauf, nach Bankabruf und Einordnen im Kassenbuch, gleicht `abgleich` die Einnahme-Buchungen ohne Wohnraum-Merkmal mit den offenen Sollposten ab. Gebucht wird nur, was genau passt (centgenau und genau ein Kandidat): Zahlerregel mit Vertrag auf die ältesten offenen Posten, Sammelzahler auf die Posten der Vertragsliste, Jahreszahler auf die Posten eines Jahres. Der Verwendungszweck wird immer geprüft. Überzahlungen werden Guthaben und mit dem nächsten Posten verrechnet; Teilzahlungen mit klarem Betrag bleiben als Rest offen. Alles andere steht in der Arbeitsliste „Zahlungen klären“ mit Vorschlag und Konfidenz und den Aktionen Zuordnen, Aufteilen, Regel anlegen, Zurückstellen und Nicht vertragsbezogen; Rückbuchungen mit Bestätigen oder Keine Rückbuchung. Barzahlung, Zahlung auf ein fremdes Konto und Erstattung erfasst du im Mietkonto des Vertrags als Handzahlung mit Grund und Beleg. Die Seite „Rückstände“ (Startseite) zeigt je Vertrag den Rückstand nach Alter; Posten, die nach dem Stand der Bankdaten fällig wurden, gelten als nicht prüfbar.

Der Beleg einer Handzahlung (PDF oder Foto) kommt in die Ablage (Dokumentart „Beleg“) und hängt über einen Dokumentbezug an der Handzahlung. Eingänge von Lieferanten (Erstattung, Rücklastschrift; Ausgabe-Kategorie oder IBAN eines Lieferanten) gehen nie in den Mieterabgleich, sondern als Klärfall „Eingang eines Lieferanten“ in die Arbeitsliste.

**Einrichten (einmalig).**

1. In `/opt/hof/.env`: `HOF_ABGLEICH_BEGINN=2026-07-01` (Zahlungen davor und Posten, die davor enden, führt das alte System; leer heißt wie `HOF_SOLL_BEGINN`).
2. Ausrollen. Den Lauf `abgleich` startet der Tageslauf (Schritt 9b) nach Bankabruf und Einordnen; bis dahin von Hand: `compose.sh exec app python manage.py abgleich`.
3. Mietregeln aus hof-fints (Datei in den Container kopieren): `compose.sh exec app python manage.py abgleich_regeln_import /pfad/regeln.yaml`, Bericht prüfen, dann mit `--ausfuehren`. Der Bericht nennt nur Zähler, Eintrag-IDs und NW-IDs. Weicht das Format ab, wird nur `apps/abgleich/importformat.py` angepasst. Danach die Datei löschen.
4. Rückrechnung: `compose.sh exec app python manage.py abgleich_rueckrechnung --von 2026-07 --bis 2026-12`. Sie rechnet in einer Kopie (nichts wird gespeichert) und nennt je Vertrag bezahlt, offen oder in Klärung; den Vergleich mit dem alten Mietabgleich machst du. Der Bericht darf mit Claude geteilt werden.

## Status-MCP (Schritt 11)

Der Status-MCP ist im Betrieb die einzige Verbindung zwischen Claude und dem Hof-System. Er liefert nur Zähler, Summen, Fristen, Daten, Objekt-Kürzel und NW-IDs; keine Namen, Anschriften, IBANs, Verwendungszwecke, Beträge je Vertrag oder Dokumente. Alle Werkzeuge lesen nur: `ueberblick`, `rueckstaende`, `datenstand`, `leerstand`, `kassenbuch_monat(monat)` und `fristen(von, bis)`.

**Aufbau.**

- Eigener Dienst `statusmcp` in `docker-compose.prod.yml`: dasselbe Image, Start mit `python -m apps.statusmcp` (uvicorn, Port 8020, ohne Django). Kein veröffentlichter Port; Caddy leitet `https://hof-system.<tailnet>.ts.net/mcp` an ihn weiter, also nur im Tailscale-Netz und über HTTPS. Healthcheck: `/gesund` im Container (`ok` oder `nicht bereit`).
- Er liest ausschließlich die Sichten `sm_*` (Migration `apps/statusmcp/migrations/0002_sichten.py`), und zwar als eigener Datenbank-Benutzer mit der Rolle `hof_statusmcp`. Diese Rolle hat `SELECT` nur auf die Sichten; jede Tabelle ist gesperrt, auch künftige (Tests in `tests/statusmcp/test_rechte.py`). Die Sichten lesen keine Personenfelder (Test gegen `kern.personenfelder`).
- Rückstände, Datenstand, Leerstand und die NW-IDs zu Fristen rechnet der Lauf `statusmcp_aktualisieren` im Tageslauf (Rang 85) mit demselben Code wie das Hof-Cockpit und legt sie als Momentaufnahme ab; die Antworten nennen den Stand (`stand`). Arbeitsposten, Fristen, Läufe und Kassenbuch sind live. Von Hand: `compose.sh exec app python manage.py statusmcp_aktualisieren`.
- Kassenbuch: Kategorien erscheinen nur als ID und Art (Einnahme, Ausgabe, Entnahme, Umbuchung), denn ihre Bezeichnungen sind als Personenfeld markiert.

**Einrichten (einmalig, auf dem Server).**

1. Ausrollen (die Migration legt die Rolle `hof_statusmcp` ohne Login an).
2. Passwort für den Lese-Benutzer erzeugen und den Benutzer anlegen. Das Passwort steht nur in einer Datei, nie auf der Kommandozeile; `-` liest es von der Standardeingabe (`-T` reicht sie in den Container durch):

   ```sh
   umask 077; openssl rand -base64 36 | tr -d '/+=' > ~/statusmcp-passwort
   /opt/hof/aktuell/scripts/compose.sh exec -T app python manage.py statusmcp_benutzer --passwort-datei - < ~/statusmcp-passwort
   ```

   Der Befehl legt `hof_statusmcp_leser` an (anderer Name mit `--name`), gibt ihm nur die Rolle `hof_statusmcp`, nur lesende Transaktionen und 10 Sekunden je Anfrage. Ein zweiter Aufruf setzt das Passwort neu. Er gibt die Vorlage für die Datenbank-URL aus, ohne Passwort.
3. Token erzeugen: `openssl rand -hex 32` (mindestens 32 Zeichen).
4. `/opt/hof/statusmcp.env` anlegen (`chmod 600`, Besitzer `hof`). Nur diese beiden Werte, nicht die `.env` der App:

   ```sh
   HOF_STATUSMCP_DATABASE_URL=postgres://hof_statusmcp_leser:<passwort aus Schritt 2>@db:5432/hof
   HOF_STATUSMCP_TOKEN=<token aus Schritt 3>
   ```

   Danach die Passwortdatei aus Schritt 2 löschen.
5. Starten: `compose.sh up -d statusmcp` (danach startet `deploy.sh` ihn bei jedem Ausrollen mit). Prüfen: `compose.sh ps statusmcp` (healthy) und `curl -s -o /dev/null -w '%{http_code}\n' -X POST https://hof-system.<tailnet>.ts.net/mcp` → `401` ohne Token.

Fehlt `statusmcp.env` oder ein Wert darin, startet der Dienst trotzdem, antwortet aber überall mit 503 „nicht eingerichtet“ und steht auf unhealthy; das Ausrollen der App hängt nicht daran. Token wechseln: neuen Wert in `statusmcp.env` und im Gateway eintragen, `compose.sh up -d --force-recreate statusmcp`.

**Gateway anbinden.** Im hof-mcp-gateway (eigenes Projekt, trägt Max dort selbst ein) einen Server mit diesen Angaben anlegen:

| Angabe | Wert |
| --- | --- |
| URL | `https://hof-system.<tailnet>.ts.net/mcp` (der Gateway muss im Tailnet sein) |
| Transport | Streamable HTTP (zustandslos, Antworten als JSON; kein SSE, keine Sitzung nötig) |
| Anmeldung | Kopfzeile `Authorization: Bearer <HOF_STATUSMCP_TOKEN>` |
| Werkzeuge | `ueberblick`, `rueckstaende`, `datenstand`, `leerstand`, `kassenbuch_monat`, `fristen`; alle nur lesend |

Erlaubte Host-Header stehen in `HOF_STATUSMCP_HOSTS` (Compose setzt `statusmcp:*` und den Tailscale-Namen); ein anderer Host wird abgewiesen.

**Alte MCPs abschalten.** Sobald der Gateway auf den Status-MCP zeigt und der jeweilige Dienst ins Hof-System umgezogen ist, schaltet Max die alten MCPs ab: den HelloHousing-MCP (nach dem Umzug von Stammdaten, Soll und Abgleich) und den hof-fints-MCP (nach dem Umzug von Bank und Kassenbuch), dazu die Zeitpläne mit Mieterdaten, die an ihnen hängen. Im Gateway die beiden Einträge entfernen; danach erreicht Claude Mieterdaten nicht mehr.

## Mahnwesen (Schritt 7)

**Ablauf.** Montags im Tageslauf, nach dem Abgleich, schreibt `mahnlauf` die Mahnfälle fort (nur Verträge im Hof-System; Wohnraum mahnt Verwaltio). Der Rückstand kommt aus dem laufenden Konto; Posten nach dem Stand der Bankdaten zählen nicht. Je fälliger Stufe entsteht ein Schreiben „bereit“ und ein Arbeitsposten „Mahnungen senden“ (Hof-Backlog und Benachrichtigung). Die Stufen stehen je Vertragsart auf der Seite „Mahnwesen → Stufen“ (Startdaten nach der SOP: Erinnerung 5 Tage nach Fälligkeit, Mahnung nach 14 Tagen mit 10 Tagen Frist, Abmahnung bei der zweiten offenen Monatsmiete, fristlose Kündigung hilfsweise ordentlich ab zwei Monatsmieten Rückstand). Eine Stufe folgt erst, wenn die vorige belegt ist: gesendet, bei Mahnung, Abmahnung und Kündigung zugestellt mit Zustellnachweis. Dieselbe Stufe kommt frühestens nach 14 Tagen wieder. Zahlt der Mieter, schließt sich der Fall.

**Sperren.** Ein offener Klärfall sperrt automatisch; Rechtsfall, Ratenplan und Sperren von Hand setzt du auf der Seite „Mahnwesen → Sperren“. Gesperrte Fälle schreiten nicht fort, bereite Schreiben werden verworfen, im Fall steht der Grund.

**Probebetrieb.** Eine Stufe ist erst scharf, wenn ihre Vorlage freigegeben ist (Arten „Zahlungserinnerung“, „Mahnung mit Fristsetzung“, „Abmahnung mit Kündigungsandrohung“, „Kündigung durch den Hof“ mit dem Namen „Fristlose Kündigung“). Ohne sie entsteht nur der Arbeitsposten „Probebetrieb: Vorlage fehlt“; die Mahnung geht auf dem alten Weg hinaus, und du belegst die Stufe auf der Seite des Falls von Hand.

**Kündigung.** Jede Kündigung trägt den Hinweis „vor Versand zum Anwalt“; ihr Senden-Knopf geht nur mit dem Haken „Der Anwalt hat diese Kündigung gesehen“. Die ordentliche Kündigung durch den Vermieter (H4.6) erzeugst du auf der Seite des Falls („Ordentlich kündigen“, Adresse `/mahnwesen/vertrag/<id>/kuendigen/`): Das System rechnet das Fristende nach der Kündigungsfrist der Vertragsart und nimmt die Vorlage „Kündigung <Vertragsart>“, sonst „Kündigung durch den Hof“. Die Kündigung am Vertrag (Vertragsende, Rückgabe) erfasst du nach der Zustellung wie im Standardablauf 4.

**Vergleich mit dem alten Mahnlauf** (vier Wochen): `compose.sh exec app python manage.py mahnwesen_vergleich --von 2026-11-02 --bis 2026-11-29` nennt je Woche die Vorschläge je Stufe mit NW-IDs, die offenen und die gesperrten Fälle. Mit `--alt /pfad/alt.csv` (Semikolon, Spalten „Woche;NW-ID;Stufe“, Beispiel `tests/fixtures/mahnwesen/alter_mahnlauf.csv`) dazu die Abweichungen je NW-ID. Der Bericht enthält keine Namen und Beträge und darf mit Claude geteilt werden. Weichen die Spalten ab, wird nur `SPALTEN` bzw. `STUFEN_ALT` in `apps/mahnwesen/vergleich.py` angepasst.

## Was tun bei Ausfall

| Zeichen | Prüfen | Abhilfe |
| --- | --- | --- |
| Seite lädt nicht | Mac im Tailnet? `tailscale status`; `tailscale ping hof-system` | Tailscale-App neu verbinden; ist der Server weg: Hetzner-Konsole, Server neu starten |
| Zertifikatsfehler | `compose.sh logs caddy` | HTTPS im Tailscale-Admin an? `systemctl status tailscaled`; `compose.sh restart caddy` |
| `/gesund` meldet `fehler` | `compose.sh ps`, `compose.sh logs db` | Datenbank-Container prüfen, `compose.sh up -d db`; Platte voll? `df -h` |
| 502 von Caddy | `compose.sh logs --tail 100 app` | Letzte Version war kaputt: `deploy.sh <vorheriger tag>` |
| Status-MCP unhealthy | `compose.sh logs --tail 50 statusmcp` | „nicht eingerichtet“: `/opt/hof/statusmcp.env` prüfen (siehe Status-MCP); „nicht bereit“: Lese-Benutzer und Passwort prüfen, `statusmcp_benutzer` erneut ausführen |
| Tageslauf „mit Fehlern“ | Seite „Läufe“: Die Meldung des Tageslaufs nennt den Schritt | Lauf des Schritts ansehen, Ursache beheben, Schritt von Hand starten (`compose.sh exec app python manage.py <schritt>`) |
| Sicherung `fehler` | `fehler_bei` in der Statusdatei, `journalctl -u hof-backup` | Storage Box erreichbar? `sftp -P 23 hof-storagebox`; bei „locked“: `restic unlock` (nur wenn keine Sicherung läuft) |
| Nach Neustart nichts erreichbar | `systemctl status docker tailscaled`, `compose.sh ps` | `compose.sh up -d` (startet mit der laufenden Version) |
| Server verloren | — | Wiederherstellen auf neuem Server, siehe oben; Notfallmappe |

**Monatliche Pflege** (15 Minuten): Status beider JSON-Dateien ansehen, `apt update && apt upgrade` (auch Docker; startet die Container kurz neu), `df -h`, Snapshots in der Storage Box prüfen, alte Arbeitsposten „Selbsttest“ abhaken.
