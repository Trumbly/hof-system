#!/usr/bin/env bash
#
# Grundeinrichtung des Servers für das Hof-System (Hetzner Cloud, Ubuntu 24.04).
# Nur Max führt das aus, als root, über Tailscale verbunden:
#
#     sudo bash server-setup.sh
#
# Idempotent: Jeder Schritt prüft oder überschreibt seinen eigenen Stand, das Skript kann
# beliebig oft laufen. Voraussetzung: Tailscale ist installiert und der Server ist im
# Tailscale-Netz angemeldet (`tailscale up`). Ohne das bricht das Skript ab, weil die
# Firewall danach nur noch Tailscale durchlässt.
#
# Optional:
#   HOF_STORAGEBOX_BENUTZER   Benutzer der Storage Box (z. B. u123456); dann wird der
#                             SSH-Zugang für restic in /root/.ssh/config eingetragen
#   HOF_SETUP_ERZWINGEN=1     Sperrschutz überspringen (siehe Schritt 0)

set -euo pipefail

schritt() { printf '\n==> %s\n' "$*"; }
abbruch() { printf '\nABBRUCH: %s\n' "$*" >&2; exit 1; }

# --- 0. Vorbedingungen ---------------------------------------------------------------------
# Nur als root, nur auf Ubuntu, nur mit laufendem Tailscale. Und: Die Sitzung, in der das
# Skript läuft, muss über Tailscale kommen. Sonst würde die Firewall in Schritt 4 genau
# diese Sitzung abschneiden.

[[ $EUID -eq 0 ]] || abbruch "Bitte als root ausführen (sudo)."
# shellcheck source=/dev/null
. /etc/os-release
[[ "${ID:-}" == ubuntu ]] || abbruch "Dieses Skript ist für Ubuntu geschrieben (gefunden: ${ID:-?})."
[[ "${VERSION_ID:-}" == 24.04 ]] || echo "WARNUNG: geschrieben für Ubuntu 24.04, gefunden ${VERSION_ID:-?}" >&2

command -v tailscale >/dev/null || abbruch "Tailscale fehlt. Erst installieren und 'tailscale up' ausführen."
TS_IP4=$(tailscale ip -4 2>/dev/null | head -n 1 || true)
TS_IP6=$(tailscale ip -6 2>/dev/null | head -n 1 || true)
[[ -n "$TS_IP4" ]] || abbruch "Tailscale ist nicht verbunden ('tailscale up' ausführen)."
echo "Tailscale-Adresse: $TS_IP4"

if [[ -n "${SSH_CONNECTION:-}" && "${HOF_SETUP_ERZWINGEN:-0}" != 1 ]]; then
    # SSH_CONNECTION = "<client-ip> <client-port> <server-ip> <server-port>"
    read -r _ _ server_ip _ <<<"$SSH_CONNECTION"
    if [[ "$server_ip" != "$TS_IP4" && "$server_ip" != "$TS_IP6" ]]; then
        abbruch "Diese SSH-Sitzung kommt nicht über Tailscale (Server-Adresse $server_ip).
Verbinde dich über Tailscale (ssh root@hof-system) und starte das Skript neu.
Sonst schneidet die Firewall diese Sitzung ab."
    fi
fi

export DEBIAN_FRONTEND=noninteractive

# --- 1. Pakete -----------------------------------------------------------------------------
# Updates einspielen und die Werkzeuge installieren, die die Skripte brauchen:
#   git (deploy.sh holt die Tags), jq und curl (Status, Gesundheitsprüfung), ufw (Firewall),
#   unattended-upgrades (Sicherheitsupdates), restic (Sicherung), util-linux (flock).

schritt "1. Pakete aktualisieren und installieren"
apt-get update -q
apt-get upgrade -y -q
apt-get install -y -q --no-install-recommends \
    ca-certificates curl git jq ufw unattended-upgrades restic util-linux openssh-client

# Zeitzone: Die Zeitpläne der Timer (3:30 Sicherung, Läufe) gelten in deutscher Zeit.
timedatectl set-timezone Europe/Berlin

# --- 2. Docker aus dem offiziellen Repository ------------------------------------------------
# Nicht das docker.io-Paket von Ubuntu, sondern Docker CE mit dem compose-Plugin, wie in
# https://docs.docker.com/engine/install/ubuntu/ beschrieben. Schlüssel und Quelle werden
# jedes Mal neu geschrieben; das ist idempotent.

schritt "2. Docker aus dem offiziellen Repository"
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
cat >/etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt-get update -q
apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker

# --- 3. Docker und Tailscale --------------------------------------------------------------
# Wichtig: Docker umgeht ufw. Ein veröffentlichter Port ("443:443") landet direkt in
# iptables und ist dann aus dem Internet erreichbar, egal was ufw sagt. Deshalb bindet
# docker-compose.prod.yml Caddy nur an die Tailscale-IP ("100.x.y.z:443:443"); App und
# Datenbank veröffentlichen gar keinen Port.
#
# Damit das nach einem Neustart klappt, muss Docker die Tailscale-IP binden dürfen, auch
# wenn tailscale0 noch nicht oben ist:
#   - Docker startet nach tailscaled (systemd-Drop-in).
#   - net.ipv4.ip_nonlocal_bind=1 erlaubt das Binden an eine Adresse, die (noch) nicht
#     auf dem Server liegt. Erreichbar ist sie trotzdem nur über tailscale0.

schritt "3. Docker nach Tailscale starten, Bindung an die Tailscale-IP erlauben"
install -d -m 0755 /etc/systemd/system/docker.service.d
cat >/etc/systemd/system/docker.service.d/hof-tailscale.conf <<'EOF'
# Hof-System: Docker bindet Ports an die Tailscale-IP, also erst nach tailscaled starten.
[Unit]
Wants=tailscaled.service
After=tailscaled.service
EOF
cat >/etc/sysctl.d/60-hof.conf <<'EOF'
# Hof-System: Docker darf Ports an die Tailscale-IP binden, bevor tailscale0 oben ist.
net.ipv4.ip_nonlocal_bind = 1
EOF
sysctl -q --system
systemctl daemon-reload

# --- 4. Firewall: alles zu außer Tailscale ----------------------------------------------
# Eingehend erlaubt sind nur:
#   - alles, was über das Tailscale-Interface tailscale0 kommt (SSH, HTTPS des Hof-Systems)
#   - UDP 41641 auf der öffentlichen Adresse, damit Tailscale direkte Verbindungen aufbauen
#     kann (sonst laufen sie über Relais, langsamer, aber es geht auch ohne)
# Ausgehend bleibt alles offen (Updates, GitHub, Storage Box, Bank-API).
# Ein früher erlaubtes SSH auf der öffentlichen Adresse wird entfernt. Zusätzlich lohnt die
# Hetzner Cloud Firewall mit derselben Regel (docs/betrieb.md).

schritt "4. Firewall (ufw): eingehend nur Tailscale"
ufw default deny incoming
ufw default allow outgoing
ufw allow in on tailscale0 comment 'Tailscale: alles im Tailnet'
ufw allow 41641/udp comment 'Tailscale: direkte Verbindungen'
for regel in "OpenSSH" "22/tcp" "22" "ssh"; do
    ufw delete allow "$regel" >/dev/null 2>&1 || true
done
ufw --force enable
ufw status verbose

# --- 5. Automatische Sicherheitsupdates -----------------------------------------------
# unattended-upgrades spielt täglich die Sicherheitsupdates von Ubuntu ein. Braucht ein
# Update einen Neustart (Kernel), startet der Server um 4:30 neu, nach der Sicherung um
# 3:30. Container starten danach von selbst wieder (restart: unless-stopped).
# Docker selbst ist nicht dabei: Ein Docker-Update startet alle Container neu; das macht
# Max von Hand (docs/betrieb.md, monatliche Pflege).

schritt "5. Automatische Sicherheitsupdates"
cat >/etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF
cat >/etc/apt/apt.conf.d/52hof-unattended-upgrades <<'EOF'
// Hof-System: Neustart bei Bedarf, nach der Sicherung.
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:30";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
EOF
systemctl enable --now unattended-upgrades

# --- 6. Benutzer „hof“ und Ordner ---------------------------------------------------------
# Das Hof-System läuft unter dem eigenen Benutzer hof (Home /opt/hof). Er darf Docker
# bedienen (Gruppe docker; das ist so mächtig wie root, deshalb meldet sich niemand
# direkt als hof an, sondern Max mit `sudo -iu hof`).
#   /opt/hof            Klon des Repositorys (repo/), Versionen, .env (legt Max an)
#   /srv/hof/medien     Dokumente; gehört UID 10001, dem Benutzer im Container
#   /var/lib/hof        Statusdateien der Sicherung und Rückspielprobe
#   /etc/hof            Zugang zur Sicherung (backup.env, restic-passwort), nur root

schritt "6. Benutzer hof und Ordner"
if ! id hof >/dev/null 2>&1; then
    useradd --create-home --home-dir /opt/hof --shell /bin/bash hof
fi
usermod -aG docker hof
install -d -m 0750 -o hof -g hof /opt/hof
install -d -m 0750 -o hof -g hof /opt/hof/versionen
install -d -m 0700 -o hof -g hof /opt/hof/vor-deploy
install -d -m 0755 -o root -g root /srv/hof
install -d -m 0750 -o 10001 -g 10001 /srv/hof/medien
install -d -m 0755 -o root -g root /var/lib/hof
install -d -m 0700 -o root -g root /var/lib/hof/sicherung /var/lib/hof/probe
install -d -m 0700 -o root -g root /etc/hof
if [[ -e /opt/hof/.env ]]; then
    chown hof:hof /opt/hof/.env
    chmod 0600 /opt/hof/.env
fi

# --- 7. restic und Zugang zur Storage Box ------------------------------------------------
# restic kommt aus Ubuntu (Schritt 1). Für die Storage Box braucht root einen eigenen
# SSH-Schlüssel; er wird einmal erzeugt. Den öffentlichen Teil trägt Max in der Storage
# Box ein (docs/betrieb.md). Die Storage Box spricht SSH/SFTP auf Port 23.

schritt "7. restic und SSH-Schlüssel für die Storage Box"
restic version
install -d -m 0700 /root/.ssh
if [[ ! -f /root/.ssh/hof-storagebox ]]; then
    ssh-keygen -q -t ed25519 -N '' -C "hof-system restic" -f /root/.ssh/hof-storagebox
fi
if [[ -n "${HOF_STORAGEBOX_BENUTZER:-}" ]]; then
    [[ "$HOF_STORAGEBOX_BENUTZER" =~ ^u[0-9]+(-sub[0-9]+)?$ ]] ||
        abbruch "HOF_STORAGEBOX_BENUTZER sieht nicht aus wie u123456"
    touch /root/.ssh/config
    chmod 0600 /root/.ssh/config
    if ! grep -q '^Host hof-storagebox$' /root/.ssh/config; then
        cat >>/root/.ssh/config <<EOF

# Hof-System: restic-Repository auf der Hetzner Storage Box (RESTIC_REPOSITORY=sftp:hof-storagebox:hof-restic)
Host hof-storagebox
    HostName $HOF_STORAGEBOX_BENUTZER.your-storagebox.de
    User $HOF_STORAGEBOX_BENUTZER
    Port 23
    IdentityFile /root/.ssh/hof-storagebox
    IdentitiesOnly yes
    ServerAliveInterval 60
EOF
    fi
fi
echo "Öffentlicher Schlüssel für die Storage Box:"
cat /root/.ssh/hof-storagebox.pub

# --- Fertig ----------------------------------------------------------------------------------

schritt "Grundeinrichtung fertig"
cat <<EOF
Nächste Schritte (docs/betrieb.md, Abschnitt Einrichtung):
  1. Repository als Benutzer hof nach /opt/hof/repo klonen (Deploy-Key, nur lesen)
  2. /opt/hof/.env anlegen (chmod 600, Besitzer hof)
  3. sudo -iu hof /opt/hof/repo/scripts/deploy.sh <tag>
  4. /etc/hof/backup.env und /etc/hof/restic-passwort anlegen, restic init
  5. sudo /opt/hof/aktuell/scripts/systemd-einrichten.sh
EOF
