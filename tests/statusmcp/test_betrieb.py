"""Betrieb des Status-MCP (Schritt 11): Compose, Caddy, Ausrollen, Betriebshandbuch."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

WURZEL = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def dienst() -> dict[str, Any]:
    compose = yaml.safe_load((WURZEL / "docker-compose.prod.yml").read_text())
    eintrag: dict[str, Any] = compose["services"]["statusmcp"]
    return eintrag


def test_compose_dienst_nur_intern(dienst: dict[str, Any]) -> None:
    """Schritt 11: Dienst ``statusmcp`` aus demselben Image, ohne veröffentlichten Port, mit
    Healthcheck, nach der Datenbank; eigene Umgebungsdatei statt der .env der App."""
    assert dienst["image"].startswith("hof-system:${HOF_VERSION")
    assert dienst["command"] == ["python", "-m", "apps.statusmcp"]
    assert "ports" not in dienst and "network_mode" not in dienst
    assert dienst["restart"] == "unless-stopped"
    assert dienst["depends_on"] == {"db": {"condition": "service_healthy"}}
    assert "/gesund" in " ".join(dienst["healthcheck"]["test"])
    assert dienst["env_file"] == [
        {"path": "${HOF_STATUSMCP_ENV_DATEI:-/opt/hof/statusmcp.env}", "required": False}
    ]
    umgebung = dienst["environment"]
    assert umgebung["HOF_STATUSMCP_BIND"] == "0.0.0.0"  # noqa: S104 – nur im Docker-Netz
    assert umgebung["HOF_STATUSMCP_PORT"] == "8020"
    assert "${HOF_HOSTNAME" in umgebung["HOF_STATUSMCP_HOSTS"]
    assert not any("DATABASE_URL" in k or "TOKEN" in k for k in umgebung)


def test_caddy_leitet_mcp_an_den_dienst() -> None:
    """Schritt 11: Der Gateway erreicht den Dienst über Caddy (HTTPS, Tailscale) unter /mcp;
    alles andere geht weiter an die App."""
    caddyfile = (WURZEL / "deploy" / "caddy" / "Caddyfile").read_text()
    assert re.search(r"@statusmcp path /mcp /mcp/\*", caddyfile)
    assert re.search(r"handle @statusmcp \{\s*reverse_proxy statusmcp:8020\s*\}", caddyfile)
    assert "reverse_proxy app:8000" in caddyfile


def test_deploy_startet_den_dienst_ohne_das_ausrollen_zu_gefaehrden() -> None:
    """Schritt 11: deploy.sh startet den Status-MCP nach der App; scheitert er, rollt das
    Skript die App nicht zurück."""
    text = (WURZEL / "scripts" / "deploy.sh").read_text()
    assert 'up -d --no-deps statusmcp ||\n    echo "Warnung' in text
    assert text.index('gesund_pruefen "$TAG" || zurueckrollen') < text.index("statusmcp")


def test_betriebshandbuch_status_mcp() -> None:
    """Schritt 11, Aufgabe 6: Anleitung in docs/betrieb.md: Start, Token, Lese-Benutzer,
    Anbindung des Gateways, Abschaltung der alten MCPs."""
    handbuch = (WURZEL / "docs" / "betrieb.md").read_text()
    assert "## Status-MCP (Schritt 11)" in handbuch
    abschnitt = handbuch.split("## Status-MCP (Schritt 11)", 1)[1].split("\n## ", 1)[0]
    for muss in (
        "statusmcp_benutzer --passwort-datei",
        "HOF_STATUSMCP_DATABASE_URL",
        "HOF_STATUSMCP_TOKEN",
        "/opt/hof/statusmcp.env",
        "Authorization: Bearer",
        "Streamable HTTP",
        "/mcp",
        "HelloHousing",
        "hof-fints",
        "statusmcp_aktualisieren",
    ):
        assert muss in abschnitt, muss
