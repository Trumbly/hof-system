#!/usr/bin/env bash
#
# Rollt eine getaggte Version des Hof-Systems aus. Nur Max führt das aus, auf dem Server,
# als Benutzer „hof“:
#
#     sudo -iu hof /opt/hof/repo/scripts/deploy.sh v0.1.0
#
# Ablauf:
#   1. Tag aus GitHub holen und prüfen, dass es ihn gibt
#   2. Stand des Tags nach /opt/hof/versionen/<tag> auspacken (git archive)
#   3. Image hof-system:<tag> bauen
#   4. Datenbank starten, Sicherung vor dem Ausrollen (pg_dump nach vor-deploy/)
#   5. Prüfung (manage.py check --deploy) und Migrationen in einem Einmal-Container
#   6. App und Caddy mit der neuen Version starten, warten bis gesund
#   7. /gesund über Caddy und Tailscale abfragen: „ok <tag>“
#   8. erst dann: /opt/hof/aktuell auf die neue Version zeigen lassen
#
# Bei einem Fehler in 1 bis 5 läuft die alte Version unverändert weiter. Bei einem Fehler
# in 6 oder 7 startet das Skript die alte Version wieder und endet mit Exit-Code 1.
# Migrationen werden dabei nicht zurückgedreht: Sie müssen so geschrieben sein, dass die
# alte Version mit dem neuen Schema läuft (nur hinzufügen, nicht umbenennen oder löschen).
# Für den Notfall liegt der Dump aus Schritt 4 in /opt/hof/vor-deploy/.
#
# Umgebung (alles optional):
#   HOF_BASIS            Basisordner (/opt/hof)
#   HOF_TAILSCALE_IP     sonst: tailscale ip -4
#   HOF_HOSTNAME         sonst: Name aus tailscale status (hof-system.<tailnet>.ts.net)
#   HOF_GESUND_WARTEN    Sekunden, die auf „ok“ gewartet wird (120)
#   HOF_VERSIONEN_BEHALTEN  so viele Versionen bleiben für ein Zurückrollen liegen (5)

set -euo pipefail
umask 027

TAG="${1:-}"
if [[ -z "$TAG" || "$TAG" == -* || ! "$TAG" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
    echo "Aufruf: deploy.sh <tag>   (z. B. deploy.sh v0.1.0)" >&2
    exit 2
fi

BASIS="${HOF_BASIS:-/opt/hof}"
REPO="$BASIS/repo"
VERSIONEN="$BASIS/versionen"
AKTUELL="$BASIS/aktuell"
VOR_DEPLOY="$BASIS/vor-deploy"
PROJEKT=hof
GESUND_WARTEN="${HOF_GESUND_WARTEN:-120}"
BEHALTEN="${HOF_VERSIONEN_BEHALTEN:-5}"

schritt() { printf '\n==> %s\n' "$*"; }
abbruch() { printf '\nABBRUCH: %s\n' "$*" >&2; exit 1; }

for befehl in git docker curl jq tailscale flock tar; do
    command -v "$befehl" >/dev/null || abbruch "$befehl fehlt (scripts/server-setup.sh ausführen)"
done
[[ -d "$REPO/.git" ]] || abbruch "$REPO ist kein Git-Klon (siehe docs/betrieb.md, Einrichtung)"
mkdir -p "$VERSIONEN" "$VOR_DEPLOY"
chmod 0700 "$VOR_DEPLOY"

# Nie zwei Ausrollvorgänge gleichzeitig.
exec 9>"$BASIS/.deploy.lock"
flock -n 9 || abbruch "Ein anderes deploy.sh läuft noch"

TS_IP="${HOF_TAILSCALE_IP:-$(tailscale ip -4 | head -n 1)}"
HOSTNAME_TS="${HOF_HOSTNAME:-$(tailscale status --json | jq -r '.Self.DNSName' | sed 's/\.$//')}"
[[ "$TS_IP" =~ ^100\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || abbruch "keine Tailscale-IP gefunden"
[[ "$HOSTNAME_TS" == *.ts.net ]] || abbruch "kein Tailscale-Name (*.ts.net) gefunden"

# docker compose mit allen Variablen für eine bestimmte Version.
compose() {
    local version=$1 verzeichnis=$2
    shift 2
    HOF_VERSION="$version" HOF_TAILSCALE_IP="$TS_IP" HOF_HOSTNAME="$HOSTNAME_TS" \
        docker compose -p "$PROJEKT" -f "$verzeichnis/docker-compose.prod.yml" "$@"
}

# Fragt /gesund über Caddy auf der Tailscale-Adresse ab, bis „ok <version>“ kommt.
gesund_pruefen() {
    local version=$1 bis=$((SECONDS + GESUND_WARTEN)) antwort=""
    while ((SECONDS < bis)); do
        antwort=$(curl -fsS --max-time 5 --resolve "$HOSTNAME_TS:443:$TS_IP" \
            "https://$HOSTNAME_TS/gesund" 2>/dev/null || true)
        if [[ "$antwort" == "ok $version" ]]; then
            echo "gesund: $antwort"
            return 0
        fi
        sleep 3
    done
    echo "gesund: keine passende Antwort nach $GESUND_WARTEN s (zuletzt: ${antwort:-nichts})" >&2
    return 1
}

ALT_TAG=""
if [[ -L "$AKTUELL" ]]; then
    ALT_TAG=$(basename "$(readlink "$AKTUELL")")
fi
echo "Server: $HOSTNAME_TS ($TS_IP) · alt: ${ALT_TAG:-keine} · neu: $TAG"

# --- 1. Tag holen ------------------------------------------------------------------------

schritt "Tag $TAG aus GitHub holen"
git -C "$REPO" fetch --quiet --tags --force --prune origin
git -C "$REPO" rev-parse --verify --quiet "refs/tags/$TAG^{commit}" >/dev/null ||
    abbruch "Tag $TAG gibt es nicht"
COMMIT=$(git -C "$REPO" rev-parse --short "refs/tags/$TAG^{commit}")

# --- 2. Auspacken ----------------------------------------------------------------------

ZIEL="$VERSIONEN/$TAG"
if [[ -d "$ZIEL" ]]; then
    echo "$ZIEL liegt schon vor"
else
    schritt "Stand $COMMIT nach $ZIEL auspacken"
    neu=$(mktemp -d "$VERSIONEN/.neu.XXXXXX")
    git -C "$REPO" archive "refs/tags/$TAG" | tar -x -C "$neu"
    chmod 0750 "$neu"
    mv "$neu" "$ZIEL"
fi

# --- 3. Bauen --------------------------------------------------------------------------

schritt "Image hof-system:$TAG bauen"
docker build --pull --build-arg "HOF_VERSION=$TAG" -t "hof-system:$TAG" "$ZIEL"

# --- 4. Datenbank und Sicherung vor dem Ausrollen ---------------------------------------

schritt "Datenbank starten"
compose "$TAG" "$ZIEL" up -d --wait --wait-timeout 120 db

DB=$(compose "$TAG" "$ZIEL" ps -q db)
dump="$VOR_DEPLOY/$(date +%Y%m%d-%H%M%S)-vor-$TAG.dump"
schritt "Sicherung vor dem Ausrollen: $dump"
docker exec "$DB" sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' >"$dump"
# Nur die letzten Dumps behalten.
find "$VOR_DEPLOY" -maxdepth 1 -name '*.dump' -printf '%T@ %p\n' | sort -rn |
    tail -n +"$((BEHALTEN + 1))" | cut -d' ' -f2- | xargs -r rm -f --

# --- 5. Prüfen und migrieren (Einmal-Container) ------------------------------------------

schritt "Prüfen (check --deploy) und migrieren"
compose "$TAG" "$ZIEL" run --rm --no-deps app python manage.py check --deploy --fail-level ERROR ||
    abbruch "check --deploy fehlgeschlagen; die alte Version ${ALT_TAG:-} läuft weiter"
compose "$TAG" "$ZIEL" run --rm --no-deps app python manage.py migrate --no-input ||
    abbruch "Migration fehlgeschlagen; die alte Version ${ALT_TAG:-} läuft weiter. Dump: $dump"

# --- 6./7. Neu starten und Gesundheit prüfen --------------------------------------------

zurueckrollen() {
    echo >&2
    if [[ -n "$ALT_TAG" && -d "$VERSIONEN/$ALT_TAG" && "$ALT_TAG" != "$TAG" ]]; then
        echo "Starte die alte Version $ALT_TAG wieder ..." >&2
        if compose "$ALT_TAG" "$VERSIONEN/$ALT_TAG" up -d --wait --wait-timeout 180 app caddy &&
            gesund_pruefen "$ALT_TAG"; then
            abbruch "$TAG ist nicht gesund gestartet. Die alte Version $ALT_TAG läuft wieder."
        fi
        abbruch "$TAG ist nicht gesund gestartet, und $ALT_TAG kam auch nicht hoch. Siehe docs/betrieb.md, Ausfall."
    fi
    compose "$TAG" "$ZIEL" stop app caddy || true
    abbruch "$TAG ist nicht gesund gestartet; es gab keine ältere Version. App und Caddy sind gestoppt."
}

schritt "App und Caddy mit $TAG starten"
compose "$TAG" "$ZIEL" up -d --wait --wait-timeout 180 --remove-orphans app caddy || zurueckrollen

schritt "/gesund über https://$HOSTNAME_TS prüfen"
gesund_pruefen "$TAG" || zurueckrollen

# --- 8. Umschalten und aufräumen --------------------------------------------------------

schritt "aktuell -> versionen/$TAG"
ln -sfn "versionen/$TAG" "$BASIS/.aktuell.neu"
mv -Tf "$BASIS/.aktuell.neu" "$AKTUELL"
# Für scripts/compose.sh: welche Version läuft, auf welcher Adresse. Keine Geheimnisse.
cat >"$BASIS/aktuell.env" <<EOF
HOF_VERSION=$TAG
HOF_TAILSCALE_IP=$TS_IP
HOF_HOSTNAME=$HOSTNAME_TS
EOF

# Ältere Versionen (Ordner und Images) wegräumen, die letzten $BEHALTEN bleiben.
find "$VERSIONEN" -mindepth 1 -maxdepth 1 -type d ! -name '.*' -printf '%T@ %f\n' | sort -rn |
    tail -n +"$((BEHALTEN + 1))" | cut -d' ' -f2- | while read -r alt; do
    [[ "$alt" == "$TAG" || "$alt" == "$ALT_TAG" ]] && continue
    echo "räume Version $alt weg"
    rm -rf -- "${VERSIONEN:?}/$alt"
    docker image rm "hof-system:$alt" >/dev/null 2>&1 || true
done
docker image prune -f >/dev/null

schritt "Fertig: $TAG ($COMMIT) läuft auf https://$HOSTNAME_TS"
