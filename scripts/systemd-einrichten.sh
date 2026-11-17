#!/usr/bin/env bash
#
# Richtet die systemd-Einheiten des Hof-Systems ein oder bringt sie auf den Stand der
# laufenden Version. Als root, nach dem ersten deploy.sh und immer dann, wenn sich unter
# deploy/systemd/ etwas geändert hat (neuer Lauf, anderer Zeitplan):
#
#     sudo /opt/hof/aktuell/scripts/systemd-einrichten.sh
#
# Was es tut (idempotent, beliebig oft ausführbar):
#   1. Einheiten aus deploy/systemd/ nach /etc/systemd/system kopieren:
#      hof-backup, hof-rueckspielprobe, hof-lauf@ (Vorlagen für Läufe)
#   2. Für jede Datei deploy/systemd/laeufe/<name>.conf den Zeitplan als Drop-in
#      /etc/systemd/system/hof-lauf@<name>.timer.d/zeitplan.conf eintragen und den Timer
#      hof-lauf@<name>.timer einschalten
#   3. Timer von Läufen, deren .conf es nicht mehr gibt, ausschalten und entfernen
#   4. Sicherung und Rückspielprobe einschalten, sobald /etc/hof/backup.env existiert

set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "Bitte als root ausführen (sudo)." >&2; exit 1; }

QUELLE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../deploy/systemd" && pwd)"
ZIEL=/etc/systemd/system

# 1. Einheiten kopieren (install ersetzt nur, wenn sich etwas geändert hat, schadet aber nie).
for einheit in hof-backup.service hof-backup.timer \
    hof-rueckspielprobe.service hof-rueckspielprobe.timer \
    hof-lauf@.service hof-lauf@.timer; do
    install -m 0644 "$QUELLE/$einheit" "$ZIEL/$einheit"
done

# 2. Zeitpläne der Läufe eintragen.
declare -A gewuenscht=()
for conf in "$QUELLE"/laeufe/*.conf; do
    [[ -e "$conf" ]] || continue
    name=$(basename "$conf" .conf)
    [[ "$name" =~ ^[a-z][a-z0-9_]*$ ]] || { echo "Ungültiger Laufname: $name" >&2; exit 1; }
    gewuenscht[$name]=1
    install -d -m 0755 "$ZIEL/hof-lauf@$name.timer.d"
    install -m 0644 "$conf" "$ZIEL/hof-lauf@$name.timer.d/zeitplan.conf"
done

systemctl daemon-reload

for name in "${!gewuenscht[@]}"; do
    systemctl enable --now "hof-lauf@$name.timer"
    # Ein geänderter Zeitplan gilt erst nach einem Neustart des Timers.
    systemctl restart "hof-lauf@$name.timer"
    echo "Lauf $name eingetragen"
done

# 3. Läufe austragen, deren Zeitplan es nicht mehr gibt.
for verzeichnis in "$ZIEL"/hof-lauf@*.timer.d; do
    [[ -d "$verzeichnis" ]] || continue
    name=$(basename "$verzeichnis" .timer.d)
    name=${name#hof-lauf@}
    if [[ -z "${gewuenscht[$name]:-}" ]]; then
        systemctl disable --now "hof-lauf@$name.timer" || true
        rm -rf -- "$verzeichnis"
        echo "Lauf $name ausgetragen"
    fi
done
systemctl daemon-reload

# 4. Sicherung und Rückspielprobe.
if [[ -r /etc/hof/backup.env && -r /etc/hof/restic-passwort ]]; then
    systemctl enable --now hof-backup.timer hof-rueckspielprobe.timer
    echo "Sicherung (täglich 3:30) und Rückspielprobe (monatlich) eingeschaltet"
else
    echo "HINWEIS: /etc/hof/backup.env oder /etc/hof/restic-passwort fehlt –" \
        "Sicherung noch nicht eingeschaltet (docs/betrieb.md, Sicherung einrichten)." >&2
fi

echo
systemctl list-timers 'hof-*' --all --no-pager
