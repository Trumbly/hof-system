"""Der Status-MCP (Schritt 11, H11.5, C3): MCP-Server über Streamable HTTP, nur lesend.

Ohne Django. Start: ``python -m apps.statusmcp`` (siehe ``__main__.py``, docs/betrieb.md).

- Werkzeuge: ``ueberblick``, ``rueckstaende``, ``datenstand``, ``leerstand``,
  ``kassenbuch_monat``, ``fristen`` (Logik in ``werkzeuge.py``). Sie lesen nur die Sichten
  ``sm_*`` über eine ``Quelle`` (``quelle.py``).
- Transport: Streamable HTTP unter ``/mcp``, zustandslos und mit JSON-Antworten. So braucht
  es keine Sitzungen; ein Neustart oder ein Proxy dazwischen stört nicht.
- Anmeldung: ``Authorization: Bearer <HOF_STATUSMCP_TOKEN>``, zeitkonstant verglichen
  (``hmac.compare_digest``). Ohne Token antwortet ``/mcp`` mit 401. Nur ``/gesund`` ist
  offen; es sagt „ok“ oder „nicht bereit“, sonst nichts.
- Ohne Token oder Datenbank-URL startet der Dienst trotzdem, antwortet aber überall mit 503
  („nicht eingerichtet“). So läuft kein Container in einer Neustart-Schleife, und der
  Healthcheck zeigt den Zustand.
"""

from __future__ import annotations

import datetime
import hmac
import logging
from collections.abc import Callable
from typing import Any
from zoneinfo import ZoneInfo

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from apps.statusmcp import werkzeuge
from apps.statusmcp.quelle import ZEITZONE, Quelle

logger = logging.getLogger("apps.statusmcp")

NAME = "hof-status"
PFAD = "/mcp"
GESUND = "/gesund"
TOKEN_MINDESTLAENGE = 32

ANLEITUNG = (
    "Status des Hof-Systems (Garagen, Stellplätze, Lager, Hallen, Außenflächen; kein Wohnraum). "
    "Alle Werkzeuge lesen nur. Antworten enthalten nur Zähler, Summen, Fristen, Daten, "
    "Objekt-Kürzel und NW-IDs (z. B. NW-G-0001), nie Namen, Anschriften, IBANs, "
    "Verwendungszwecke oder Beträge je Vertrag. Beträge sind Text mit zwei Nachkommastellen "
    "in Euro. Rückstände, Datenstand und Leerstand sind der Stand des letzten Tageslaufs "
    "(Feld 'stand'); Arbeitsposten, Fristen, Läufe und Kassenbuch sind live."
)

NUR_LESEN = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)


def heute_berlin() -> datetime.date:
    return datetime.datetime.now(ZoneInfo(ZEITZONE)).date()


def mcp_server(quelle: Quelle, *, heute: Callable[[], datetime.date] = heute_berlin) -> MCPServer:
    """Der MCP-Server mit den sechs Werkzeugen über ``quelle``."""
    server: MCPServer = MCPServer(NAME, title="Hof-System Status", instructions=ANLEITUNG)

    def lesen(funktion: Callable[..., dict[str, Any]], *argumente: Any) -> dict[str, Any]:
        try:
            with quelle.lesen() as leser:
                return funktion(leser, *argumente)
        except werkzeuge.Eingabefehler as fehler:
            raise ToolError(str(fehler)) from fehler

    @server.tool(
        description=(
            "Überblick: offene Arbeitsposten je Art (Anzahl, überfällig, nächste Fälligkeit), "
            "überfällige und nächste Fristen (Datum, Art, NW-ID bzw. Objekt-Kürzel), letzter "
            "Lauf je Name mit Status und Zählern, Zustand der Sicherungsprüfung."
        ),
        annotations=NUR_LESEN,
    )
    def ueberblick() -> dict[str, Any]:
        return lesen(werkzeuge.ueberblick, heute())

    @server.tool(
        description=(
            "Rückstände: Anzahl Verträge im Rückstand und Summen je Objekt und gesamt, nach "
            "Alter (0-30, 31-60, über 60 Tage), Verträge in Klärung, nicht prüfbarer Teil "
            "nach Datenstand der Bank; dazu die NW-IDs mit Alter der ältesten Fälligkeit, ohne "
            "Beträge je Vertrag."
        ),
        annotations=NUR_LESEN,
    )
    def rueckstaende() -> dict[str, Any]:
        return lesen(werkzeuge.rueckstaende)

    @server.tool(
        description=(
            "Datenstand: fehlende Pflichtfelder je Bereich (Einheit, Partei, Vertrag, Kaution, "
            "Schlüssel, Zähler) und die NW-IDs bzw. Objekt-Kürzel mit Lücken."
        ),
        annotations=NUR_LESEN,
    )
    def datenstand() -> dict[str, Any]:
        return lesen(werkzeuge.datenstand)

    @server.tool(
        description="Leerstand: freie Einheiten mit NW-ID, Objekt, Typ, frei seit und Tagen frei.",
        annotations=NUR_LESEN,
    )
    def leerstand() -> dict[str, Any]:
        return lesen(werkzeuge.leerstand)

    @server.tool(
        description=(
            "Kassenbuch eines Monats: Summen je Objekt und Kategorie (Kategorie nur als ID und "
            "Art) mit Vorzeichen, Summen je Art und gesamt. monat im Format JJJJ-MM."
        ),
        annotations=NUR_LESEN,
    )
    def kassenbuch_monat(monat: str) -> dict[str, Any]:
        return lesen(werkzeuge.kassenbuch_monat, monat)

    @server.tool(
        description=(
            "Offene Fristen im Zeitraum von bis (JJJJ-MM-TT, beide eingeschlossen; Standard: "
            "heute bis heute + 90 Tage, höchstens drei Jahre) mit Datum, Art, Quelle, fällig "
            "ab und NW-IDs bzw. Objekt-Kürzel."
        ),
        annotations=NUR_LESEN,
    )
    def fristen(von: str | None = None, bis: str | None = None) -> dict[str, Any]:
        return lesen(werkzeuge.fristen, heute(), von, bis)

    return server


# --- Anmeldung ------------------------------------------------------------------------


class BearerSperre:
    """ASGI-Middleware: Ohne passendes Bearer-Token kommt keine Anfrage an den MCP-Server."""

    def __init__(self, app: ASGIApp, token: str, offen: frozenset[str] = frozenset({GESUND})):
        self.app = app
        self._erwartet = f"Bearer {token}".encode()
        self._offen = offen

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in self._offen:
            await self.app(scope, receive, send)
            return
        angegeben = b""
        for name, wert in scope.get("headers", []):
            if name.lower() == b"authorization":
                angegeben = wert
                break
        if not hmac.compare_digest(angegeben, self._erwartet):
            antwort = JSONResponse(
                {"fehler": "nicht angemeldet"},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="hof-status"'},
            )
            await antwort(scope, receive, send)
            return
        await self.app(scope, receive, send)


# --- ASGI-App -------------------------------------------------------------------------


def _gesund_route(quelle: Quelle) -> Callable[[Request], Any]:
    async def gesund(_: Request) -> Response:
        import anyio.to_thread

        def pruefen() -> bool:
            try:
                with quelle.lesen() as leser:
                    leser.abfragen("SELECT count(*) AS n FROM sm_laeufe")
                return True
            except Exception:
                logger.exception("Status-MCP: Datenbank nicht lesbar")
                return False

        ok = await anyio.to_thread.run_sync(pruefen)
        if ok:
            return PlainTextResponse("ok")
        return PlainTextResponse("nicht bereit", status_code=503)

    return gesund


def nicht_eingerichtet(grund: str) -> Starlette:
    """Platzhalter-App: 503 auf jede Anfrage, bis Token und Datenbank eingetragen sind."""

    async def antwort(_: Request) -> Response:
        return PlainTextResponse(f"nicht eingerichtet: {grund}", status_code=503)

    return Starlette(
        routes=[Route("/{pfad:path}", antwort, methods=["GET", "POST", "DELETE", "HEAD"])]
    )


def asgi_app(
    quelle: Quelle,
    token: str,
    *,
    erlaubte_hosts: list[str] | None = None,
    heute: Callable[[], datetime.date] = heute_berlin,
) -> ASGIApp:
    """Die fertige ASGI-App: MCP unter ``/mcp`` hinter dem Token, ``/gesund`` offen.

    ``erlaubte_hosts``: Werte des Host-Headers, die der MCP-Endpunkt annimmt (Schutz gegen
    DNS-Rebinding), z. B. ``statusmcp:*`` und der Name im Tailscale-Netz.
    """
    if len(token) < TOKEN_MINDESTLAENGE:
        raise ValueError(f"HOF_STATUSMCP_TOKEN braucht mindestens {TOKEN_MINDESTLAENGE} Zeichen.")
    server = mcp_server(quelle, heute=heute)
    server.custom_route(GESUND, methods=["GET"])(_gesund_route(quelle))
    hosts = erlaubte_hosts or ["127.0.0.1:*", "localhost:*"]
    sicherheit = TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=[]
    )
    app = server.streamable_http_app(
        streamable_http_path=PFAD,
        stateless_http=True,
        json_response=True,
        transport_security=sicherheit,
    )
    return BearerSperre(app, token)
