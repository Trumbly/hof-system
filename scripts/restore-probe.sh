#!/usr/bin/env bash
#
# Rückspielprobe (H11.2): spielt die letzte Sicherung in eine getrennte Datenbank auf
# demselben Server zurück und vergleicht die Zeilenzahlen je Tabelle mit den Zahlen, die
# backup.sh im selben Snapshot gezählt hat. Die laufende Datenbank bleibt unberührt.
#
# Zusätzlich (abschaltbar mit HOF_PROBE_MEDIEN=0): Der Medienordner wird in einen
# temporären Ordner zurückgespielt, mit restic --verify geprüft und die Zahl der Dateien
# mit dem Snapshot verglichen.
#
# Ausgabe: nur Tabellennamen und Zahlen. Exit-Code 0, wenn alles übereinstimmt, sonst 1.
# Danach wird aufgeräumt: Probe-Datenbank gelöscht, temporäre Dateien entfernt.
#
# Umgebung wie backup.sh (RESTIC_REPOSITORY, RESTIC_PASSWORD_FILE, HOF_COMPOSE_PROJEKT,
# HOF_MEDIEN_DIR, HOF_SICHERUNG_DIR), dazu:
#   HOF_PROBE_DB       Name der Probe-Datenbank (hof_probe)
#   HOF_PROBE_STATUS   Statusdatei (/var/lib/hof/rueckspielprobe-status.json)
#   HOF_PROBE_MEDIEN   1 = Medienordner mitprüfen (Standard), 0 = nur Datenbank
#   HOF_PROBE_TMP      Ort für temporäre Dateien (/var/lib/hof/probe)

set -euo pipefail
umask 077

: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY fehlt (siehe /etc/hof/backup.env)}"
export RESTIC_REPOSITORY
export RESTIC_PASSWORD_FILE="${RESTIC_PASSWORD_FILE:-/etc/hof/restic-passwort}"
unset RESTIC_PASSWORD RESTIC_PASSWORD_COMMAND

PROJEKT="${HOF_COMPOSE_PROJEKT:-hof-system}"
MEDIEN_DIR="${HOF_MEDIEN_DIR:-/srv/hof/medien}"
SICHERUNG_DIR="${HOF_SICHERUNG_DIR:-/var/lib/hof/sicherung}"
PROBE_DB="${HOF_PROBE_DB:-hof_probe}"
STATUS_DATEI="${HOF_PROBE_STATUS:-/var/lib/hof/rueckspielprobe-status.json}"
MIT_MEDIEN="${HOF_PROBE_MEDIEN:-1}"
TMP_BASIS="${HOF_PROBE_TMP:-/var/lib/hof/probe}"

[[ "$PROBE_DB" =~ ^[a-z_][a-z0-9_]*$ ]] || { echo "Ungültiger Name der Probe-Datenbank" >&2; exit 2; }

BEGINN_EPOCHE=$(date +%s)
SNAPSHOT_ID=""
TABELLEN=0
ABWEICHUNGEN=0
ARBEIT=""
DB=""

meldung() { printf '%s probe: %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }

psql_db() {  # psql_db <datenbank> – liest SQL von stdin
    docker exec -i "$DB" sh -c \
        'exec psql -X -q -A -t -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$1"' sh "$1"
}

probe_db_loeschen() {
    docker exec "$DB" sh -c 'exec dropdb -U "$POSTGRES_USER" --if-exists --force "$1"' \
        sh "$PROBE_DB"
}

abschluss() {
    local code=$? ergebnis tmp
    # Aufräumen: Probe-Datenbank und zurückgespielte Dateien enthalten Echtdaten.
    if [[ -n "$DB" ]]; then probe_db_loeschen >/dev/null 2>&1 || true; fi
    if [[ -n "$ARBEIT" ]]; then rm -rf -- "$ARBEIT"; fi

    if [[ $code -eq 0 ]]; then ergebnis=ok; else ergebnis=fehler; fi
    mkdir -p "$(dirname "$STATUS_DATEI")"
    tmp=$(mktemp "${STATUS_DATEI}.XXXXXX")
    jq -n --arg zeit "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --arg ergebnis "$ergebnis" \
        --arg snapshot "$SNAPSHOT_ID" --argjson dauer "$(($(date +%s) - BEGINN_EPOCHE))" \
        --argjson tabellen "$TABELLEN" --argjson abweichungen "$ABWEICHUNGEN" \
        '{zeit: $zeit, ergebnis: $ergebnis,
          snapshot_id: (if $snapshot == "" then null else $snapshot end),
          dauer_sekunden: $dauer, tabellen: $tabellen, abweichungen: $abweichungen}' >"$tmp"
    chmod 0644 "$tmp"
    mv -f "$tmp" "$STATUS_DATEI"
    exit "$code"
}
trap abschluss EXIT

for befehl in docker restic jq awk; do
    command -v "$befehl" >/dev/null || { meldung "$befehl fehlt"; exit 1; }
done

DB=$(docker ps -q \
    --filter "label=com.docker.compose.project=$PROJEKT" \
    --filter "label=com.docker.compose.service=db" \
    --filter status=running | head -n 1)
[[ -n "$DB" ]] || { meldung "Datenbank-Container des Projekts $PROJEKT läuft nicht"; exit 1; }

mkdir -p "$TMP_BASIS"
chmod 0700 "$TMP_BASIS"
ARBEIT=$(mktemp -d "$TMP_BASIS/lauf.XXXXXX")

# --- Letzte Sicherung holen ------------------------------------------------------------

SNAPSHOT_ID=$(restic snapshots --json --tag hof --latest 1 | jq -r 'max_by(.time) | .id // empty')
[[ -n "$SNAPSHOT_ID" ]] || { meldung "keine Sicherung mit dem Tag hof gefunden"; exit 1; }
echo "snapshot ${SNAPSHOT_ID:0:8}"

restic dump --quiet "$SNAPSHOT_ID" "$SICHERUNG_DIR/datenbank.dump" >"$ARBEIT/datenbank.dump"
restic dump --quiet "$SNAPSHOT_ID" "$SICHERUNG_DIR/zeilenzahlen.tsv" >"$ARBEIT/soll.tsv"

# --- In die Probe-Datenbank zurückspielen ---------------------------------------------

probe_db_loeschen >/dev/null 2>&1
docker exec "$DB" sh -c 'exec createdb -U "$POSTGRES_USER" "$1"' sh "$PROBE_DB"
docker exec -i "$DB" sh -c \
    'exec pg_restore -U "$POSTGRES_USER" -d "$1" --no-owner --no-acl --exit-on-error' \
    sh "$PROBE_DB" <"$ARBEIT/datenbank.dump"

ZAEHLEN_SQL="SELECT c.relname || chr(9) || (xpath('/row/n/text()', query_to_xml(format('SELECT count(*) AS n FROM %I.%I', n.nspname, c.relname), false, true, '')))[1]::text FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') ORDER BY c.relname;"
psql_db "$PROBE_DB" <<<"$ZAEHLEN_SQL" >"$ARBEIT/ist.tsv"

# --- Vergleichen: Tabelle, Sicherung, Probe ---------------------------------------------

printf '%-45s %12s %12s  %s\n' "tabelle" "sicherung" "probe" "ergebnis"
awk -F '\t' -v ergebnisdatei="$ARBEIT/ergebnis" '
    NR == FNR { soll[$1] = $2; namen[$1] = 1; next }
    { ist[$1] = $2; namen[$1] = 1 }
    END {
        n = 0
        for (t in namen) liste[++n] = t
        # sortieren (ohne gawk-Erweiterungen)
        for (i = 2; i <= n; i++) { v = liste[i]; j = i - 1
            while (j > 0 && liste[j] > v) { liste[j + 1] = liste[j]; j-- }
            liste[j + 1] = v }
        abw = 0
        for (i = 1; i <= n; i++) {
            t = liste[i]
            s = (t in soll) ? soll[t] : "-"; p = (t in ist) ? ist[t] : "-"
            ok = (s == p && s != "-")
            if (!ok) abw++
            printf "%-45s %12s %12s  %s\n", t, s, p, (ok ? "ok" : "ABWEICHUNG")
        }
        print n " " abw > ergebnisdatei
    }
' "$ARBEIT/soll.tsv" "$ARBEIT/ist.tsv"
read -r TABELLEN ABWEICHUNGEN <"$ARBEIT/ergebnis"

# --- Medienordner -----------------------------------------------------------------------

if [[ "$MIT_MEDIEN" == "1" ]]; then
    soll_dateien=$(restic ls --json --recursive "$SNAPSHOT_ID" "$MEDIEN_DIR" |
        jq -s '[.[] | select(((.struct_type // .message_type) == "node") and .type == "file")] | length')
    restic restore --quiet --verify "$SNAPSHOT_ID" --target "$ARBEIT/medien" \
        --include "$MEDIEN_DIR" >/dev/null
    ist_dateien=0
    if [[ -d "$ARBEIT/medien$MEDIEN_DIR" ]]; then
        ist_dateien=$(find "$ARBEIT/medien$MEDIEN_DIR" -type f | wc -l | tr -d ' ')
    fi
    if [[ "$soll_dateien" == "$ist_dateien" ]]; then ergebnis=ok; else ergebnis=ABWEICHUNG; fi
    printf '%-45s %12s %12s  %s\n' "medien (dateien)" "$soll_dateien" "$ist_dateien" "$ergebnis"
    [[ "$ergebnis" == ok ]] || ABWEICHUNGEN=$((ABWEICHUNGEN + 1))
fi

if [[ "$TABELLEN" -eq 0 ]]; then
    meldung "keine Tabellen verglichen"
    exit 1
fi
if [[ "$ABWEICHUNGEN" -ne 0 ]]; then
    echo "ergebnis ABWEICHUNG $ABWEICHUNGEN"
    exit 1
fi
echo "ergebnis ok $TABELLEN"
