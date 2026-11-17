#!/usr/bin/env bash
#
# docker compose für die laufende Version, für Wartung auf dem Server. Beispiele:
#
#     /opt/hof/aktuell/scripts/compose.sh ps
#     /opt/hof/aktuell/scripts/compose.sh logs --tail 100 app
#     /opt/hof/aktuell/scripts/compose.sh exec app python manage.py benutzer_anlegen --email …
#
# Liest Version, Tailscale-IP und -Namen aus /opt/hof/aktuell.env (schreibt deploy.sh).
# Ausrollen nur mit deploy.sh, nicht mit „compose.sh up“ von Hand.

set -euo pipefail

BASIS="${HOF_BASIS:-/opt/hof}"
[[ -r "$BASIS/aktuell.env" ]] || { echo "$BASIS/aktuell.env fehlt: erst deploy.sh ausführen" >&2; exit 1; }

set -a
# shellcheck source=/dev/null
source "$BASIS/aktuell.env"
set +a

exec docker compose -p hof -f "$BASIS/aktuell/docker-compose.prod.yml" "$@"
