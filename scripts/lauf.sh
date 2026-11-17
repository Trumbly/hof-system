#!/usr/bin/env bash
#
# Startet einen Lauf (Management-Befehl) im laufenden App-Container:
#
#     lauf.sh <name> [argumente …]      z. B. lauf.sh kern_selbsttest
#
# Aufgerufen von hof-lauf@<name>.service (siehe deploy/systemd/). Der Lauf trägt sich
# selbst in kern.Lauf ein (LaufBefehl); dieses Skript gibt nur seinen Exit-Code weiter.
# Läuft der App-Container nicht, endet es mit Exit-Code 1, und systemd meldet den Fehler.

set -euo pipefail

NAME="${1:-}"
[[ "$NAME" =~ ^[a-z][a-z0-9_]*$ ]] || { echo "Aufruf: lauf.sh <name des befehls>" >&2; exit 2; }
shift

PROJEKT="${HOF_COMPOSE_PROJEKT:-hof}"
APP=$(docker ps -q \
    --filter "label=com.docker.compose.project=$PROJEKT" \
    --filter "label=com.docker.compose.service=app" \
    --filter status=running | head -n 1)
[[ -n "$APP" ]] || { echo "App-Container des Projekts $PROJEKT läuft nicht" >&2; exit 1; }

exec docker exec "$APP" python manage.py "$NAME" "$@"
