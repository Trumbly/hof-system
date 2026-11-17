#!/usr/bin/env bash
#
# Tägliche Sicherung des Hof-Systems mit restic (gestartet von hof-backup.timer).
#
# Was gesichert wird, in einem Snapshot mit dem Tag „hof“:
#   - datenbank.dump    pg_dump der Datenbank (Custom-Format) aus dem DB-Container
#   - zeilenzahlen.tsv  Zeilen je Tabelle, gezählt im selben Datenbank-Snapshot wie der
#                       Dump; die Rückspielprobe vergleicht damit
#   - der Medienordner  hochgeladene Dokumente
# Danach: Aufbewahrung 14 täglich, 8 wöchentlich, 12 monatlich, prune, restic check.
#
# Am Ende steht immer eine Statusdatei (JSON) mit Zeit, Ergebnis, Snapshot-ID und Dauer,
# auch bei einem Fehler. Die Lauf-Überwachung liest sie später.
#
# Umgebung (auf dem Server aus /etc/hof/backup.env, siehe docs/betrieb.md):
#   RESTIC_REPOSITORY      Pflicht, z. B. sftp:storagebox:hof-restic
#   RESTIC_PASSWORD_FILE   Datei mit dem Repo-Passwort (/etc/hof/restic-passwort).
#                          Das Passwort kommt nur aus dieser Datei, nie aus der Umgebung.
#   HOF_COMPOSE_PROJEKT    Name des Compose-Projekts (hof)
#   HOF_MEDIEN_DIR         Medienordner (/srv/hof/medien)
#   HOF_SICHERUNG_DIR      Ablage für Dump und Zeilenzahlen während der Sicherung
#                          (/var/lib/hof/sicherung); der Pfad steht so im Snapshot
#   HOF_BACKUP_STATUS      Statusdatei (/var/lib/hof/backup-status.json)
#   HOF_RESTIC_HOST        Host-Name im Snapshot (hof-system)
#
# Ausgaben nennen nur Zähler, Größen und IDs, keine Inhalte.

set -euo pipefail
umask 077

: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY fehlt (siehe /etc/hof/backup.env)}"
export RESTIC_REPOSITORY
export RESTIC_PASSWORD_FILE="${RESTIC_PASSWORD_FILE:-/etc/hof/restic-passwort}"
# Das Passwort nur aus der Datei: Werte aus der Umgebung werden nicht benutzt.
unset RESTIC_PASSWORD RESTIC_PASSWORD_COMMAND

PROJEKT="${HOF_COMPOSE_PROJEKT:-hof}"
MEDIEN_DIR="${HOF_MEDIEN_DIR:-/srv/hof/medien}"
SICHERUNG_DIR="${HOF_SICHERUNG_DIR:-/var/lib/hof/sicherung}"
STATUS_DATEI="${HOF_BACKUP_STATUS:-/var/lib/hof/backup-status.json}"
RESTIC_HOST="${HOF_RESTIC_HOST:-hof-system}"

BEGINN_EPOCHE=$(date +%s)
BEGINN=$(date -u +%Y-%m-%dT%H:%M:%SZ)
SCHRITT="start"
SNAPSHOT_ID=""

meldung() { printf '%s backup: %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }

status_schreiben() {
    local ergebnis=$1 ende dauer tmp
    ende=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    dauer=$(($(date +%s) - BEGINN_EPOCHE))
    mkdir -p "$(dirname "$STATUS_DATEI")"
    tmp=$(mktemp "${STATUS_DATEI}.XXXXXX")
    jq -n \
        --arg zeit "$ende" --arg beginn "$BEGINN" --arg ergebnis "$ergebnis" \
        --arg snapshot "$SNAPSHOT_ID" --arg schritt "$SCHRITT" --argjson dauer "$dauer" \
        '{zeit: $zeit, beginn: $beginn, ergebnis: $ergebnis,
          snapshot_id: (if $snapshot == "" then null else $snapshot end),
          dauer_sekunden: $dauer,
          fehler_bei: (if $ergebnis == "ok" then null else $schritt end)}' >"$tmp"
    # Lesbar für die Lauf-Überwachung; die Datei enthält keine Daten des Hofs.
    chmod 0644 "$tmp"
    mv -f "$tmp" "$STATUS_DATEI"
}

abschluss() {
    local code=$?
    # Dump und Zeilenzahlen nie liegen lassen: Sie stehen im Snapshot.
    rm -f "$SICHERUNG_DIR/datenbank.dump" "$SICHERUNG_DIR/zeilenzahlen.tsv"
    if [[ $code -eq 0 ]]; then
        status_schreiben ok
    else
        status_schreiben fehler
        meldung "FEHLER im Schritt '$SCHRITT' (Exit-Code $code)"
    fi
    exit "$code"
}
trap abschluss EXIT

# --- Vorbedingungen ----------------------------------------------------------------

SCHRITT="vorbedingungen"
for befehl in docker restic jq; do
    command -v "$befehl" >/dev/null || { meldung "$befehl fehlt"; exit 1; }
done
[[ -r "$RESTIC_PASSWORD_FILE" ]] || { meldung "Passwortdatei fehlt: $RESTIC_PASSWORD_FILE"; exit 1; }
[[ -d "$MEDIEN_DIR" ]] || { meldung "Medienordner fehlt: $MEDIEN_DIR"; exit 1; }

# Nie zwei Sicherungen gleichzeitig (etwa Timer und Handstart).
if command -v flock >/dev/null; then
    sperre=/run/lock/hof-backup.lock
    [[ -w /run/lock ]] || sperre="${TMPDIR:-/tmp}/hof-backup.lock"
    exec 9>"$sperre"
    flock -n 9 || { meldung "Eine andere Sicherung läuft noch"; exit 1; }
fi

DB=$(docker ps -q \
    --filter "label=com.docker.compose.project=$PROJEKT" \
    --filter "label=com.docker.compose.service=db" \
    --filter status=running | head -n 1)
[[ -n "$DB" ]] || { meldung "Datenbank-Container des Projekts $PROJEKT läuft nicht"; exit 1; }

mkdir -p "$SICHERUNG_DIR"
chmod 0700 "$SICHERUNG_DIR"

# --- Datenbank: Dump und Zeilenzahlen aus demselben Snapshot -------------------------
#
# Eine psql-Sitzung öffnet eine Transaktion (REPEATABLE READ) und exportiert ihren
# Snapshot. pg_dump liest mit --snapshot genau diesen Stand, und die Zeilenzahlen werden
# in derselben Transaktion gezählt. So passen Dump und Zahlen zusammen, auch wenn
# währenddessen ein Lauf schreibt.

SCHRITT="datenbank"

# Zählt die Zeilen jeder Tabelle im Schema public: „tabelle<TAB>anzahl“, sortiert.
ZAEHLEN_SQL="SELECT c.relname || chr(9) || (xpath('/row/n/text()', query_to_xml(format('SELECT count(*) AS n FROM %I.%I', n.nspname, c.relname), false, true, '')))[1]::text FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') ORDER BY c.relname;"
ENDE_MARKE="--hof-ende--"

coproc PSQL {
    docker exec -i "$DB" sh -c \
        'exec psql -X -q -A -t -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
}
# shellcheck disable=SC2153 # PSQL_PID setzt bash für den coproc PSQL
psql_pid=$PSQL_PID
exec {psql_ein}>&"${PSQL[1]}" {psql_aus}<&"${PSQL[0]}"

printf '%s\n' \
    "BEGIN ISOLATION LEVEL REPEATABLE READ, READ ONLY;" \
    "SELECT pg_export_snapshot();" >&"$psql_ein"
read -r -t 60 db_snapshot <&"$psql_aus" || { meldung "kein Snapshot von psql"; exit 1; }
[[ "$db_snapshot" =~ ^[0-9A-Fa-f-]+$ ]] || { meldung "unerwartete Antwort von psql"; exit 1; }

docker exec "$DB" sh -c \
    'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --snapshot="$1"' \
    sh "$db_snapshot" >"$SICHERUNG_DIR/datenbank.dump"

printf '%s\n' "$ZAEHLEN_SQL" "SELECT '$ENDE_MARKE';" >&"$psql_ein"
: >"$SICHERUNG_DIR/zeilenzahlen.tsv"
while true; do
    read -r -t 300 zeile <&"$psql_aus" || { meldung "Zeilenzahlen unvollständig"; exit 1; }
    [[ "$zeile" == "$ENDE_MARKE" ]] && break
    printf '%s\n' "$zeile" >>"$SICHERUNG_DIR/zeilenzahlen.tsv"
done
printf '%s\n' "COMMIT;" '\q' >&"$psql_ein"
exec {psql_ein}>&- {psql_aus}<&-
# Die Sitzung war nur lesend; ihr Exit-Code ändert nichts mehr am Dump.
wait "$psql_pid" 2>/dev/null || true

tabellen=$(wc -l <"$SICHERUNG_DIR/zeilenzahlen.tsv" | tr -d ' ')
groesse=$(wc -c <"$SICHERUNG_DIR/datenbank.dump" | tr -d ' ')
[[ "$tabellen" -gt 0 && "$groesse" -gt 0 ]] || { meldung "Dump oder Zeilenzahlen leer"; exit 1; }
meldung "Datenbank: $tabellen Tabellen, Dump $groesse Bytes"

# --- restic: sichern, aufräumen, prüfen ----------------------------------------------

SCHRITT="restic-backup"
ausgabe=$(restic backup --json --quiet --tag hof --host "$RESTIC_HOST" \
    "$SICHERUNG_DIR/datenbank.dump" "$SICHERUNG_DIR/zeilenzahlen.tsv" "$MEDIEN_DIR")
SNAPSHOT_ID=$(jq -r 'select(.message_type == "summary") | .snapshot_id' <<<"$ausgabe")
[[ -n "$SNAPSHOT_ID" && "$SNAPSHOT_ID" != "null" ]] || { meldung "keine Snapshot-ID"; exit 1; }
meldung "Snapshot ${SNAPSHOT_ID:0:8} angelegt"

SCHRITT="restic-forget"
restic forget --quiet --tag hof --host "$RESTIC_HOST" \
    --keep-daily 14 --keep-weekly 8 --keep-monthly 12 --prune

SCHRITT="restic-check"
restic check --quiet

SCHRITT="fertig"
meldung "Sicherung fertig in $(($(date +%s) - BEGINN_EPOCHE)) s"
