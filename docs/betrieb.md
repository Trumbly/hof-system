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

## Benachrichtigung (H11.1)

Der Lauf `benachrichtigen` (7:30, 12:30, 18:30) meldet sich nur, wenn ein Lauf in den letzten 24 Stunden abgebrochen ist oder Fehler hatte, oder wenn Arbeitsposten bestimmter Arten offen warten (Standard: Frist fällig, die Bank-Posten Bankfreigabe läuft ab, Bankfreigabe fehlt, Bankabruf gescheitert, Bankumsätze fehlen und Saldo weicht ab, dazu Mahnungen senden, Zahlungen klären, Rechnung zahlen, Schreiben senden). Inhalt nur Zähler und Links, keine Namen oder Beträge. Dieselbe Lage kommt höchstens einmal am Tag; neu meldet sich nur, was dazukommt. Ohne Anlass bleibt es still.

Einrichten in `/opt/hof/.env`:

- `HOF_BASIS_URL=https://hof-system.<tailnet>.ts.net` (Ziel der Links)
- Mail: `HOF_BENACHRICHTIGUNG_MAIL=<deine Adresse>`; versendet über `EMAIL_URL`.
- Push über ntfy: `HOF_NTFY_URL=https://ntfy.sh` (oder der eigene Server), `HOF_NTFY_THEMA=<langes, zufälliges Thema>`, bei geschütztem Thema `HOF_NTFY_TOKEN=tk_…`. In der ntfy-App dasselbe Thema abonnieren.
- Optional: `HOF_BENACHRICHTIGUNG_ARTEN` (Arten, durch Komma getrennt), `HOF_BENACHRICHTIGUNG_FENSTER_STUNDEN` (Standard 24).

Ein Kanal ohne Einstellung bleibt aus; `check --deploy` warnt, wenn keiner eingerichtet ist oder `HOF_BASIS_URL` fehlt. Fällt ein Kanal aus, steht der Lauf auf „mit Fehlern“. Die Seite „Läufe“ zeigt bei jedem Lauf `benachrichtigen`, ob und über welche Kanäle gemeldet wurde. Testen: `compose.sh exec app python manage.py benachrichtigen`.

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

## Kassenbuch

**Täglicher Ablauf.** Um 6:30, nach dem Bankabruf, ordnet `kassenbuch_einordnen` die neuen Umsätze nach den Zuordnungsregeln ein (Seite „Kassenbuch → Regeln“). Eindeutig ist ein Umsatz, wenn genau eine Regel mit der höchsten Priorität passt; dann wird er gebucht. Sonst steht er in der Arbeitsliste „Umsätze einordnen“ mit Zuordnen, Aufteilen, Regel anlegen und Zurückstellen. Eine neue Regel zeigt erst eine Vorschau mit Zählern und bucht offene Umsätze rückwirkend erst nach dem Klick. Gebuchtes wird nie geändert: „Stornieren“ am Umsatz oder „Zurücknehmen“ im Protokoll legen eine Stornobuchung an.

**Monatsabschluss (H6.6).** Am 3. jedes Monats um 7 Uhr schließt `kassenbuch_monatsabschluss` den Vormonat je Mandant mit Bankkonto ab: alle Umsätze eingeordnet, Saldo im Buch gleich Saldo der Bank (Kontostand am Monatsende; fehlt er, vom nächsten späteren Kontostand zurückgerechnet). Lücken stehen als „Monatsabschluss offen“ in der Arbeitsliste; nach dem Klären dort „Erneut prüfen“. Danach ist der Monat gesperrt (auch in der Datenbank). Ist ein Monat für alle Mandanten abgeschlossen, liegen die Monatsberichte je Objekt als PDF unter `/srv/hof/medien/kassenbuch/monatsberichte/<Jahr>/` und auf der Seite „Kassenbuch → Monatsbericht“.

**Einrichten (einmalig).**

1. In `/opt/hof/.env`: `HOF_KASSENBUCH_BEGINN=2026-01-01` (Läufe beginnen mit 2026; ältere Umsätze bleiben außen vor).
2. Ausrollen; `sudo /opt/hof/aktuell/scripts/systemd-einrichten.sh` trägt die Timer `kassenbuch_einordnen` und `kassenbuch_monatsabschluss` ein.
3. Kategorien importieren (Datei nach `/srv/hof/medien/import/`, Spalten `Kategorie;Art;Mandant`, Zuordnung in `apps/kassenbuch/importformat.py`), erst prüfen, dann anlegen; danach die Datei löschen:

```sh
compose.sh exec app python manage.py kassenbuch_kategorien_import /app/medien/import/kategorien.csv               # Probelauf
compose.sh exec app python manage.py kassenbuch_kategorien_import /app/medien/import/kategorien.csv --ausfuehren
```

4. Regeln auf der Seite „Kassenbuch → Regeln“ anlegen, dann `sudo systemctl start hof-lauf@kassenbuch_einordnen.service` und die Arbeitsliste abarbeiten.
5. 2026 nachholen: `compose.sh exec app python manage.py kassenbuch_monatsabschluss --ab 2026-01 --bis 2026-12` (bzw. bis zum Vormonat). Er schließt der Reihe nach ab und hält beim ersten Monat mit Lücken an; nach dem Klären erneut starten.

## Bankfreigabe erneuern

Vivid gibt den Zugriff für höchstens 180 Tage frei, VR (FinTS) etwa alle 90 Tage. 14 Tage vor Ablauf legt der Lauf `bank_abruf` einen Arbeitsposten „Bankfreigabe läuft ab“ an, je Freigabe genau einen; mit der neuen Freigabe ist er erledigt.

- **Vivid:** Im Arbeitsposten auf „Freigabe erneuern“ (oder „Bank → Konten → erneuern“), dann „Bei der Bank freigeben“. Es geht zu Vivid, dort in der App bestätigen; danach landet man wieder im Hof-System, die neue Freigabe steht mit Ablaufdatum unter „Konten“. Klappt die Rückkehr nicht, stimmt meist die Rückkehr-Adresse bei Enable Banking nicht.
- **VR:** Die Freigabe erneuert der FinTS-Abruf auf dem hof-mac (TAN in der VR-App). Danach auf der Seite „Freigabe erneuern“ des VR-Kontos eintragen, bis wann sie gilt; nur so kennt das System die Frist.
- Ist eine Freigabe schon abgelaufen, scheitert der Abruf („Bankabruf gescheitert“). Nach der Erneuerung holt der nächste Lauf die Lücke nach; von Hand: `sudo systemctl start hof-lauf@bank_abruf.service`.

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

Damit der Lieferant einer Mail erkannt wird, auf „Dokumente → Lieferanten“ seine Absenderadresse eintragen. Belege vom Handy: „Dokumente → Beleg hochladen“ (Foto oder PDF). Die Belegmappe je Objekt und Jahr entsteht auf „Dokumente → Belegmappe“.

## Stammdaten importieren (Schritt 4)

Reihenfolge: erst Einheiten, dann Parteien und Verträge. Beide Befehle laufen ohne Option als Probelauf; der Bericht nennt nur Zähler, NW-IDs, Vertragsnummern und Zeilennummern und darf mit Claude geteilt werden.

1. Notion-Export (CSV) und HelloHousing-Export (Mietverhältnisse, Mietverlauf) in den Container kopieren.
2. `compose.sh exec app python manage.py import_einheiten /pfad/einheiten.csv`, Bericht prüfen, dann mit `--ausfuehren`.
3. `compose.sh exec app python manage.py import_hellohousing /pfad/mieter.csv --mietverlauf /pfad/verlauf.csv`, Bericht prüfen, dann mit `--ausfuehren`.
4. Seite „Datenstand“: Kündigungsfrist, Kaution, Zahlweise und Scan je Vertrag aus dem PDF nachtragen.

Weichen die Spaltenköpfe ab, wird nur `apps/stammdaten/import_spalten.toml` angepasst. Kündigungsregeln je Vertragsart, Checklisten und Pflichtfelder stehen in `apps/stammdaten/konfiguration.toml`. Für Vertrag und Kündigungsbestätigung braucht es freigegebene Vorlagen der Arten „Vertrag“ (Name je Vertragsart, z. B. „Vertrag Garage“, sonst die neueste) und „Kündigungsbestätigung“; fehlt eine, entsteht ein Arbeitsposten „Schreiben erstellen“.

## Sollstellung (Schritt 5)

**Ablauf.** Am 1. jedes Monats um 5:30 stellt `soll_monat` die Sollposten aller Verträge bis zum Monatsende und gleicht sie mit den Konditionen ab. Jede Änderung am Vertrag (Kondition, Laufzeit, Kündigung, Übergabe, Rückgabe) stellt das Soll des Vertrags sofort neu: Offene Posten, die nicht mehr passen, werden storniert und neu erzeugt. Bezahlte oder teilweise bezahlte Posten bleiben; sie stehen als „Sollposten prüfen“ in der Arbeitsliste. Die Seite „Offene Posten“ zeigt alle offenen Posten mit Filter, je Vertrag mit Fälligkeit und Posten von Hand (Kaution, Sonstiges). Fälligkeit und Feiertage stehen in `apps/soll/konfiguration.toml` (Standard: dritter Werktag, Feiertage NRW).

**Einrichten (einmalig).**

1. In `/opt/hof/.env`: `HOF_SOLL_BEGINN=2026-07-01` (Perioden, die früher beginnen, führt HelloHousing; Vorschlag, Max entscheidet).
2. Ausrollen; `sudo /opt/hof/aktuell/scripts/systemd-einrichten.sh` trägt den Timer `soll_monat` ein.
3. Soll bis Dezember stellen: `compose.sh exec app python manage.py soll_monat --stichtag 2026-12-01`.
4. Vergleich mit dem Soll-Export aus HelloHousing (CSV in den Container kopieren): `compose.sh exec app python manage.py soll_vergleich --export /pfad/soll.csv --von 2026-07-01 --bis 2026-12-31`. Der Bericht nennt je Vertrag nur Zähler, Vertragsnummer und NW-ID und darf mit Claude geteilt werden; danach die Datei löschen. Weichen die Spaltenköpfe ab, wird nur der Abschnitt `[vergleich_hellohousing]` in `apps/soll/konfiguration.toml` angepasst.

## Website und Verwaltio (Schritt 10)

Schnittstelle und Checkliste für die Website: `docs/website-anbindung.md`.

- **Token der Website:** `compose.sh exec app python manage.py website_token --bezeichnung "Website-Server"`. Der Token erscheint einmal; auf dem Website-Server als `HOF_API_TOKEN` eintragen. Wechsel ohne Ausfall mit `--behalten`, alle sperren mit `--sperren`.
- **Einstellungen** in `/opt/hof/.env`: `HOF_WEBSITE_URL` und `HOF_WEBSITE_TOKEN` (für den ID-Abgleich, Endpunkt `/api/hof/einheiten` der Website), optional `HOF_WEBSITE_IP_FREIGABE` (Tailscale-IP des Website-Servers), `VERWALTIO_URL` und `VERWALTIO_TOKEN`.
- **Läufe:** `verwaltio_kennzahlen` (3:15) und `id_abgleich` (5:30). Ohne Einstellungen melden sie „nicht eingerichtet“ und enden ohne Fehler. Der echte Verwaltio-Client ist offen, bis die API-Dokumentation vorliegt; mit gesetzter `VERWALTIO_URL` bricht `verwaltio_kennzahlen` bis dahin mit `NotImplementedError` ab.
- **Arbeitsposten:** „Buchungsanfrage prüfen“ (Seite „Anbindungen → Buchungsanfragen“) und „ID-Abweichung“ (schließt sich beim nächsten Lauf, sobald behoben). Beide melden sich über die Benachrichtigung.

## Zähler (Schritt 8)

Import nach den Einheiten: `compose.sh exec app python manage.py import_zaehler /pfad/zaehler.csv --staende /pfad/staende.csv` (Probelauf; Bericht nennt Anzahlen, Zählernummern und Zeilennummern), dann mit `--ausfuehren`. Spalten in `apps/zaehler/import_spalten.toml`, Beispiel unter `tests/fixtures/zaehler/`.

Der Lauf `zaehler_stichtag` (täglich 6:20, prüft selbst) legt im Ablesefenster (15.12. bis 15.01.) je Zähler ohne Stand zum 31.12. einen Arbeitsposten „Ablesung fehlt“ an; er meldet sich über die Benachrichtigung. Stichtag, Fenster und Vorlauf der Eichfrist stehen in `apps/zaehler/konfiguration.toml`. Vor dem Besuch: Zähler → „Liste für deinen nächsten Besuch“ drucken, vor Ort Zähler → „Ablesen <Objekt>“ am Handy.

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
