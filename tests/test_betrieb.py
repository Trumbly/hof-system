"""Betrieb (Schritt 0d): Prüfungen an Dockerfile, Compose, Caddy, Skripten und systemd.

Die Skripte selbst laufen nur auf dem Server. Diese Tests sichern die Regeln ab, die beim
Ändern leicht verloren gehen: kein Port im offenen Netz, kein root im Container, keine
Geheimnisse im Image, Läufe nur mit vorhandenen Befehlen.
"""

from __future__ import annotations

import configparser
import os
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from django.core.management import get_commands

WURZEL = Path(__file__).resolve().parent.parent
SKRIPTE = sorted((WURZEL / "scripts").glob("*.sh"))
SYSTEMD = WURZEL / "deploy" / "systemd"


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    daten: dict[str, Any] = yaml.safe_load((WURZEL / "docker-compose.prod.yml").read_text())
    return daten


def _systemd(pfad: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    parser.read_string(pfad.read_text())
    return parser


# --- Docker Compose und Caddy ----------------------------------------------------------


def test_kein_port_auf_der_oeffentlichen_adresse(compose: dict[str, Any]) -> None:
    """Abnahme Schritt 0d, Aufgabe 2: Kein Port auf der öffentlichen Adresse. Nur Caddy
    veröffentlicht Ports, und nur auf der Tailscale-IP (Docker umgeht ufw)."""
    dienste = compose["services"]
    assert set(dienste) == {"app", "db", "caddy"}
    assert "ports" not in dienste["app"]
    assert "ports" not in dienste["db"]
    for port in dienste["caddy"]["ports"]:
        assert isinstance(port, str)
        assert re.fullmatch(r"\$\{HOF_TAILSCALE_IP(:\?[^}]*)?\}:(80|443):(80|443)", port), port
    assert all(d.get("network_mode") != "host" for d in dienste.values())


def test_compose_dienste_mit_healthcheck_und_volumes(compose: dict[str, Any]) -> None:
    """Schritt 0d, Aufgabe 1: App (gunicorn aus dem Image), Postgres 16 mit Volume und
    Caddy, jeweils mit Healthcheck; Medienordner als Volume; Version aus HOF_VERSION."""
    dienste = compose["services"]
    for name in ("app", "db", "caddy"):
        assert "test" in dienste[name]["healthcheck"], name
        assert dienste[name]["restart"] == "unless-stopped", name

    assert dienste["db"]["image"].startswith("postgres:16")
    assert "db:/var/lib/postgresql/data" in dienste["db"]["volumes"]
    assert dienste["app"]["image"].startswith("hof-system:${HOF_VERSION")
    assert dienste["app"]["environment"]["HOF_VERSION"] == "${HOF_VERSION}"
    assert any(v.endswith(":/app/medien") for v in dienste["app"]["volumes"])
    assert "/gesund" in " ".join(dienste["app"]["healthcheck"]["test"])
    assert compose["name"] == "hof"


def test_umgebung_nur_ueber_env_file(compose: dict[str, Any]) -> None:
    """Schritt 0d, Aufgabe 1: Umgebungswerte kommen nur aus der .env auf dem Server
    (env_file); in der Compose-Datei steht kein Geheimnis."""
    for name in ("app", "db"):
        env_file = compose["services"][name]["env_file"]
        assert env_file == [{"path": "${HOF_ENV_DATEI:-/opt/hof/.env}", "required": True}]
    text = (WURZEL / "docker-compose.prod.yml").read_text()
    for schluessel in ("PASSWORD", "SECRET_KEY", "DATABASE_URL"):
        assert not re.search(rf"^\s*\w*{schluessel}\w*\s*[:=]", text, re.MULTILINE), schluessel


def test_caddy_holt_zertifikat_ueber_tailscale(compose: dict[str, Any]) -> None:
    """Abnahme Schritt 0d, Aufgabe 2: Caddy holt das Zertifikat über Tailscale (*.ts.net):
    tailscaled-Socket eingehängt, Name aus HOF_HOSTNAME, Medien nicht direkt ausgeliefert."""
    caddy = compose["services"]["caddy"]
    assert any(v.endswith(":/var/run/tailscale") for v in caddy["volumes"])
    assert caddy["environment"]["HOF_HOSTNAME"].startswith("${HOF_HOSTNAME:?")

    caddyfile = (WURZEL / "deploy" / "caddy" / "Caddyfile").read_text()
    assert re.search(r"^\{\$HOF_HOSTNAME\} \{", caddyfile, re.MULTILINE)
    assert "reverse_proxy app:8000" in caddyfile
    assert "file_server" not in caddyfile
    assert "acme" not in caddyfile.lower()


# --- Dockerfile -------------------------------------------------------------------------


def test_dockerfile_schlank_und_nicht_root() -> None:
    """Abnahme Schritt 0d, Aufgabe 1: Produktions-Image ohne Entwicklungsabhängigkeiten,
    läuft nicht als root, statische Dateien im Build, Bibliotheken für WeasyPrint."""
    text = (WURZEL / "Dockerfile").read_text()
    assert "uv sync --locked --no-dev" in text
    assert "collectstatic --no-input" in text
    assert re.search(r"^FROM python:3\.12|^ARG PYTHON_IMAGE=python:3\.12[.\d]*-slim", text, re.M)
    benutzer = re.findall(r"^USER\s+(\S+)", text, re.MULTILINE)
    assert benutzer, "USER fehlt"
    assert benutzer[-1] not in {"root", "0"}
    for paket in ("libpango-1.0-0", "libpangoft2-1.0-0", "libharfbuzz-subset0"):
        assert paket in text, paket
    assert 'CMD ["gunicorn"' in text


def test_dockerignore_haelt_geheimnisse_und_daten_fern() -> None:
    """Schritt 0d, Aufgabe 1: .env, Datenordner und Sicherungen kommen nie ins Image."""
    eintraege = {
        zeile.strip()
        for zeile in (WURZEL / ".dockerignore").read_text().splitlines()
        if zeile.strip() and not zeile.startswith("#")
    }
    for muss in (".env", ".env.*", ".git", ".venv", "data/", "import/", "backups/", "media/"):
        assert muss in eintraege, muss


# --- Skripte ----------------------------------------------------------------------------


def test_alle_skripte_vorhanden() -> None:
    """Schritt 0d, Aufgaben 3, 4, 6, 7: Skripte für Einrichtung, Ausrollen, Sicherung und
    Rückspielprobe liegen unter scripts/."""
    namen = {s.name for s in SKRIPTE}
    assert {"server-setup.sh", "deploy.sh", "backup.sh", "restore-probe.sh"} <= namen


@pytest.mark.parametrize("skript", SKRIPTE, ids=lambda p: p.name)
def test_skripte_brechen_bei_fehlern_ab(skript: Path) -> None:
    """Schritt 0d, Aufgabe 4: Jedes Skript bricht bei Fehlern ab (set -euo pipefail) und
    ist ausführbar."""
    zeilen = skript.read_text().splitlines()
    assert zeilen[0] == "#!/usr/bin/env bash"
    assert "set -euo pipefail" in zeilen
    assert os.access(skript, os.X_OK), f"{skript.name} ist nicht ausführbar"


def test_deploy_prueft_gesund_und_rollt_zurueck() -> None:
    """Abnahme Schritt 0d, Aufgabe 4: deploy.sh nimmt einen Tag, migriert in einem
    Einmal-Container, prüft /gesund mit Wartezeit und startet bei Fehler die alte Version."""
    text = (WURZEL / "scripts" / "deploy.sh").read_text()
    assert 'TAG="${1:-}"' in text
    assert "refs/tags/$TAG^{commit}" in text
    assert "run --rm --no-deps app python manage.py migrate" in text
    assert "/gesund" in text
    assert "GESUND_WARTEN" in text
    assert "zurueckrollen" in text
    assert '"ok $version"' in text


def test_backup_aufbewahrung_und_passwort_aus_datei() -> None:
    """Abnahme Schritt 0d, Aufgabe 6: Aufbewahrung 14 täglich, 8 wöchentlich, 12 monatlich;
    Passwort nur aus einer Datei; Statusdatei als JSON."""
    text = (WURZEL / "scripts" / "backup.sh").read_text()
    assert "--keep-daily 14 --keep-weekly 8 --keep-monthly 12 --prune" in text
    assert "unset RESTIC_PASSWORD RESTIC_PASSWORD_COMMAND" in text
    assert "/var/lib/hof/backup-status.json" in text
    assert "pg_dump" in text
    assert "--snapshot=" in text
    for feld in ("zeit", "ergebnis", "snapshot_id", "dauer_sekunden"):
        assert f"{feld}:" in text, feld


def test_rueckspielprobe_in_getrennte_datenbank() -> None:
    """Abnahme Schritt 0d, Aufgabe 7: Die Rückspielprobe nutzt eine eigene Datenbank,
    vergleicht Zeilenzahlen, räumt auf und meldet Abweichungen mit Exit-Code."""
    text = (WURZEL / "scripts" / "restore-probe.sh").read_text()
    assert 'PROBE_DB="${HOF_PROBE_DB:-hof_probe}"' in text
    assert "pg_restore" in text
    assert "ABWEICHUNG" in text
    assert "trap abschluss EXIT" in text
    assert "dropdb" in text


def test_server_setup_firewall_nur_tailscale() -> None:
    """Abnahme Schritt 0d, Aufgabe 3: ufw lässt eingehend nur Tailscale zu; Docker,
    automatische Updates, eigener Benutzer und restic werden eingerichtet."""
    text = (WURZEL / "scripts" / "server-setup.sh").read_text()
    assert "ufw default deny incoming" in text
    assert "ufw allow in on tailscale0" in text
    assert "ufw allow 41641/udp" in text
    assert "download.docker.com" in text
    assert "unattended-upgrades" in text
    assert "useradd" in text
    assert "restic" in text
    # Kein Port im offenen Netz außer Tailscale.
    erlaubt = re.findall(r"^ufw allow .*$", text, re.MULTILINE)
    assert len(erlaubt) == 2, erlaubt


# --- systemd ----------------------------------------------------------------------------


def test_backup_timer_taeglich() -> None:
    """Abnahme Schritt 0d, Aufgabe 6: Ein systemd-Timer startet die Sicherung täglich."""
    timer = _systemd(SYSTEMD / "hof-backup.timer")
    assert timer["Timer"]["OnCalendar"] == "*-*-* 03:30:00"
    assert timer["Timer"]["Persistent"] == "true"
    dienst = _systemd(SYSTEMD / "hof-backup.service")
    assert dienst["Service"]["ExecStart"] == "/opt/hof/aktuell/scripts/backup.sh"
    assert dienst["Service"]["EnvironmentFile"] == "/etc/hof/backup.env"


def test_lauf_vorlage_ohne_eigenen_zeitplan() -> None:
    """Abnahme Schritt 0d, Aufgabe 8: hof-lauf@.timer ist eine Vorlage; der Zeitplan kommt je
    Lauf aus deploy/systemd/laeufe/<name>.conf, der Dienst ruft manage.py <name> auf."""
    timer = _systemd(SYSTEMD / "hof-lauf@.timer")
    assert "OnCalendar" not in timer["Timer"]
    assert timer["Timer"]["Unit"] == "hof-lauf@%i.service"
    dienst = _systemd(SYSTEMD / "hof-lauf@.service")
    assert dienst["Service"]["ExecStart"] == "/opt/hof/aktuell/scripts/lauf.sh %i"
    assert dienst["Service"]["User"] == "hof"
    assert "manage.py" in (WURZEL / "scripts" / "lauf.sh").read_text()


def test_eingetragene_laeufe_gibt_es_als_befehl() -> None:
    """Abnahme Schritt 0d, Aufgabe 8: Jeder eingetragene Lauf hat einen Zeitplan und einen
    Management-Befehl gleichen Namens (Beispiel: kern_selbsttest)."""
    eintraege = sorted((SYSTEMD / "laeufe").glob("*.conf"))
    assert "kern_selbsttest" in {e.stem for e in eintraege}
    befehle = get_commands()
    for eintrag in eintraege:
        assert re.fullmatch(r"[a-z][a-z0-9_]*", eintrag.stem), eintrag.name
        assert eintrag.stem in befehle, f"Befehl {eintrag.stem} fehlt"
        assert _systemd(eintrag)["Timer"]["OnCalendar"], eintrag.name


# --- Dokumentation ----------------------------------------------------------------------


def test_betriebshandbuch_und_notfallmappe() -> None:
    """Abnahme Schritt 0, Punkt 5: Betriebshandbuch und Notfallmappe liegen vor."""
    handbuch = (WURZEL / "docs" / "betrieb.md").read_text()
    for abschnitt in (
        "## Ausrollen",
        "## Sicherung",
        "## Wiederherstellen",
        "## Bankfreigabe erneuern",
        "## Was tun bei Ausfall",
        "## Läufe eintragen",
    ):
        assert abschnitt in handbuch, abschnitt
    assert "DATABASE_URL=postgres://hof:" in handbuch

    mappe = (WURZEL / "docs" / "notfallmappe-vorlage.md").read_text()
    assert "## 2. Zugänge" in mappe
    assert "restic-Passwort" in mappe
