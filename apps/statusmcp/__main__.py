"""Start des Status-MCP: ``python -m apps.statusmcp`` (ohne Django).

Umgebungswerte (auf dem Server aus ``/opt/hof/statusmcp.env``, siehe docs/betrieb.md):

- ``HOF_STATUSMCP_DATABASE_URL``: Verbindung als Lese-Benutzer (``statusmcp_benutzer``),
  z. B. ``postgres://hof_statusmcp_leser:<passwort>@db:5432/hof``. Nie die URL der App.
- ``HOF_STATUSMCP_TOKEN``: Bearer-Token für den Gateway, mindestens 32 Zeichen.
- ``HOF_STATUSMCP_HOSTS``: erlaubte Host-Header, mit Komma getrennt
  (Standard ``127.0.0.1:*,localhost:*,statusmcp:*``).
- ``HOF_STATUSMCP_BIND`` (Standard ``127.0.0.1``) und ``HOF_STATUSMCP_PORT`` (8020).
"""

from __future__ import annotations

import logging
import os

import uvicorn
from starlette.types import ASGIApp

from apps.statusmcp.quelle import DsnQuelle
from apps.statusmcp.server import TOKEN_MINDESTLAENGE, asgi_app, nicht_eingerichtet

logger = logging.getLogger("apps.statusmcp")

STANDARD_HOSTS = "127.0.0.1:*,localhost:*,statusmcp:*"


def app_aus_umgebung() -> ASGIApp:
    dsn = os.environ.get("HOF_STATUSMCP_DATABASE_URL", "").strip()
    token = os.environ.get("HOF_STATUSMCP_TOKEN", "").strip()
    hosts = [
        h.strip()
        for h in os.environ.get("HOF_STATUSMCP_HOSTS", STANDARD_HOSTS).split(",")
        if h.strip()
    ]
    if not dsn:
        logger.error("Status-MCP nicht eingerichtet: HOF_STATUSMCP_DATABASE_URL fehlt.")
        return nicht_eingerichtet("Datenbank")
    if len(token) < TOKEN_MINDESTLAENGE:
        logger.error(
            "Status-MCP nicht eingerichtet: HOF_STATUSMCP_TOKEN fehlt oder ist kürzer als %s.",
            TOKEN_MINDESTLAENGE,
        )
        return nicht_eingerichtet("Token")
    return asgi_app(DsnQuelle(dsn), token, erlaubte_hosts=hosts)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    uvicorn.run(
        app_aus_umgebung(),
        host=os.environ.get("HOF_STATUSMCP_BIND", "127.0.0.1"),
        port=int(os.environ.get("HOF_STATUSMCP_PORT", "8020")),
        proxy_headers=False,
        server_header=False,
        access_log=True,
    )


if __name__ == "__main__":
    main()
