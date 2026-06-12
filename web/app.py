"""
Phase 13a: Web Dashboard — FastAPI Hauptanwendung

Startet den Web-Dashboard-Server mit Jinja2-Templates,
statischen Dateien und WebSocket-Unterstuetzung.
"""

import asyncio
import sys
from pathlib import Path

# Projekt-Root in sys.path einfuegen, damit utils/ importierbar ist
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from utils.config import load_env, get_env
from utils.logger import get_logger
from web.middleware.csrf import CSRFMiddleware
from web.middleware.session_timeout import SessionTimeoutMiddleware
from web.middleware.rate_limiter import RateLimitMiddleware
from modules.database.db_manager import init_db, close_db

# Umgebungsvariablen laden
load_env()

logger = get_logger("web.dashboard")

# Pruefen ob Web-Dashboard aktiviert ist
WEB_ENABLED = get_env("WEB_ENABLED", "false", cast=bool)
WEB_PORT = get_env("WEB_PORT", 8080, cast=int)
WEB_SECRET_KEY = get_env("WEB_SECRET_KEY", "")
if not WEB_SECRET_KEY:
    # M34-Fix: Prod (WEB_HTTPS=true) verlangt einen festen Key — siehe web/auth.py.
    if get_env("WEB_HTTPS", "true", cast=bool):
        raise RuntimeError(
            "WEB_SECRET_KEY fehlt in config/.env — Pflicht im Prod-Betrieb "
            "(WEB_HTTPS=true). Setze einen festen WEB_SECRET_KEY."
        )
    import secrets as _secrets
    WEB_SECRET_KEY = _secrets.token_hex(32)
    logger.warning("WEB_SECRET_KEY nicht gesetzt — temporaerer Dev-Key generiert (nur ohne WEB_HTTPS).")

if not WEB_ENABLED:
    logger.warning("WEB_ENABLED ist nicht 'true'. Dashboard ist deaktiviert.")
    logger.warning("Setze WEB_ENABLED=true in config/.env um das Dashboard zu starten.")
    sys.exit(0)

# FastAPI-App erstellen
app = FastAPI(
    title="Discord Bot Dashboard",
    description="Web-Dashboard fuer das Discord Bot System",
    version="1.0.0",
    docs_url=None,      # Swagger-UI deaktivieren in Produktion
    redoc_url=None       # ReDoc deaktivieren in Produktion
)

# HTTPS-Modus erkennen (Nginx Reverse-Proxy setzt X-Forwarded-Proto)
WEB_HTTPS = get_env("WEB_HTTPS", "true", cast=bool)

# Middleware-Reihenfolge (WICHTIG!):
# Starlette verarbeitet Middlewares in LIFO — die LETZTE add_middleware()
# wird als ERSTE ausgefuehrt (aeusserste Schicht).
# Session muss ZULETZT registriert werden, damit request.session fuer
# alle anderen Middlewares verfuegbar ist.

# CORS-Middleware (innerste Schicht — laeuft als letztes)
SERVER_IP = get_env("SERVER_IP", "203.0.113.10")
DOMAIN = get_env("WEB_DOMAIN", "marco-satisfactory.duckdns.org")
ALLOWED_ORIGINS = [
    f"https://{DOMAIN}",
    f"https://{SERVER_IP}:8443",
    f"https://{SERVER_IP}",
    f"http://{SERVER_IP}:8080",
    "http://127.0.0.1:8080",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With", "X-CSRF-Token"],
)

# F48: Rate-Limiting Middleware
app.add_middleware(RateLimitMiddleware)

# F64: CSRF-Schutz Middleware (braucht request.session)
app.add_middleware(CSRFMiddleware)

# F65: Session-Timeout Middleware (braucht request.session)
app.add_middleware(SessionTimeoutMiddleware)

# Session-Middleware (aeusserste Schicht — laeuft als ERSTE, stellt request.session bereit)
app.add_middleware(
    SessionMiddleware,
    secret_key=WEB_SECRET_KEY,
    session_cookie="dashboard_session",
    max_age=86400,       # 24 Stunden
    same_site="lax",
    https_only=WEB_HTTPS
)

# Statische Dateien und Templates einbinden
STATIC_DIR = Path(__file__).parent / "static"
TEMPLATE_DIR = Path(__file__).parent / "templates"

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


# --- WebSocket-Verbindungsverwaltung ---

class ConnectionManager:
    """Verwaltet aktive WebSocket-Verbindungen fuer Echtzeit-Updates.

    M01-Fix (Audit 2026-06-04): `set` statt `list` (O(1)-discard, kein ValueError
    bei Doppel-Remove) + `asyncio.Lock` um alle Mutationen, da broadcast/register/
    disconnect ueber await-Punkte hinweg nebenlaeufig laufen. Der Handshake-`accept()`
    + Auth-Check passiert im Endpoint VOR `register()` — daher kein `accept()` mehr hier.
    """

    def __init__(self):
        self.active_connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def register(self, websocket: WebSocket):
        """Bereits akzeptierte+authentifizierte WS-Verbindung registrieren."""
        async with self._lock:
            self.active_connections.add(websocket)
        logger.debug(f"WebSocket verbunden. Aktive Verbindungen: {len(self.active_connections)}")

    async def disconnect(self, websocket: WebSocket):
        """WS-Verbindung idempotent entfernen (discard wirft nicht bei Doppel-Remove)."""
        async with self._lock:
            self.active_connections.discard(websocket)
        logger.debug(f"WebSocket getrennt. Aktive Verbindungen: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        """Nachricht an alle verbundenen Clients senden. Tote Sockets gesammelt entfernen."""
        import json
        data = json.dumps(message)
        async with self._lock:
            targets = list(self.active_connections)
        dead = []
        for connection in targets:
            try:
                await connection.send_text(data)
            except Exception:  # noqa: BLE001
                dead.append(connection)
        if dead:
            async with self._lock:
                self.active_connections.difference_update(dead)


ws_manager = ConnectionManager()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket-Endpunkt fuer Echtzeit-Dashboard-Updates (auth-gated).

    Der Broadcaster (`_dashboard_broadcaster`) pusht alle DASHBOARD_PUSH_INTERVAL
    Sekunden den Payload an alle verbundenen Clients. Auth via JWT-Cookie —
    der Kanal streamt Server-/System-/Bot-Daten und muss wie die fruehere
    SSE-Variante (`require_auth_api`) angemeldet sein.
    """
    import json as _json
    from web.auth import get_ws_user
    from web.dashboard_feed import gather_dashboard_payload

    # Handshake annehmen, DANN Auth pruefen (accept-first ist version-robust;
    # close-before-accept liefert je nach uvicorn 403/404). Es werden keine Daten
    # vor dem Auth-Check gesendet -> kein Leak.
    await websocket.accept()
    if get_ws_user(websocket) is None:
        await websocket.close(code=1008)  # Policy Violation: nicht angemeldet
        return
    # M12-Fix (CSWSH): fremde Origins ablehnen. Browser senden Origin beim
    # WS-Handshake; non-Browser-Clients (ohne Origin-Header) bleiben erlaubt.
    origin = websocket.headers.get("origin")
    if origin is not None and origin not in ALLOWED_ORIGINS:
        await websocket.close(code=1008)  # Policy Violation: fremder Origin
        return

    await ws_manager.register(websocket)

    try:
        # Sofort ein erstes Update schicken (Client wartet nicht aufs naechste Intervall).
        try:
            payload = await gather_dashboard_payload()
            await websocket.send_text(_json.dumps({"type": "dashboard_update", **payload}))
        except Exception as e:  # noqa: BLE001
            logger.debug(f"WS-Initial-Payload fehlgeschlagen: {e}")

        while True:
            # Auf Nachrichten vom Client warten (Keep-Alive-Ping)
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        # M01-Fix: idempotenter Cleanup auf ALLEN Exit-Pfaden (nicht nur WebSocketDisconnect)
        await ws_manager.disconnect(websocket)


# --- Dashboard-Broadcaster (WebSocket-Push, ersetzt SSE) ---

# Sekunden zwischen zwei Dashboard-Pushes (analog altem SSE-DASHBOARD_INTERVAL).
DASHBOARD_PUSH_INTERVAL = 5
_broadcaster_task = None


async def _dashboard_broadcaster() -> None:
    """
    Pusht periodisch den Dashboard-Payload an alle verbundenen WS-Clients.

    Sammelt nur Daten wenn mind. ein Client verbunden ist (kein Leerlauf-IO).
    Ein einzelner Sammel-/Sende-Fehler bricht die Schleife nicht ab.
    """
    import asyncio as _asyncio
    from web.dashboard_feed import gather_dashboard_payload

    logger.info("Dashboard-WS-Broadcaster gestartet")
    try:
        while True:
            await _asyncio.sleep(DASHBOARD_PUSH_INTERVAL)
            if not ws_manager.active_connections:
                continue
            try:
                payload = await gather_dashboard_payload()
                await ws_manager.broadcast({"type": "dashboard_update", **payload})
            except _asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — Broadcaster nie sterben lassen
                logger.warning(f"Dashboard-Broadcast fehlgeschlagen: {e}")
    except _asyncio.CancelledError:
        logger.debug("Dashboard-WS-Broadcaster abgebrochen")
    finally:
        logger.info("Dashboard-WS-Broadcaster beendet")


# --- Routen einbinden ---

from web.auth import auth_router          # noqa: E402
from web.routes.dashboard import router as dashboard_router      # noqa: E402
from web.routes.errors_route import router as errors_router      # noqa: E402
from web.routes.config_route import router as config_router      # noqa: E402
from web.routes.system_route import router as system_router      # noqa: E402
from web.routes.server_detail_route import router as server_detail_router  # noqa: E402
from web.routes.analytics_route import router as analytics_router    # noqa: E402
from web.routes.marshal_bot_route import router as marshal_bot_router    # noqa: E402
from web.routes.health_route import router as health_router          # noqa: E402
from web.routes.security_route import router as security_router      # noqa: E402
from web.routes.forecast_route import router as forecast_router      # noqa: E402
from web.routes.backup_status_route import router as backup_status_router  # noqa: E402
from web.routes.export_route import router as export_router          # noqa: E402
from web.routes.changelog_route import router as changelog_router    # noqa: E402
from web.routes.theme_route import router as theme_router            # noqa: E402
from web.routes.config_reload_route import router as config_reload_router  # noqa: E402
from web.routes.webhook_route import router as webhook_router        # noqa: E402
from web.routes.correlation_route import router as correlation_router  # noqa: E402
from web.routes.search_route import router as search_router            # noqa: E402
from web.routes.leveling_route import router as leveling_router        # noqa: E402
from web.routes.moderation_route import router as moderation_router    # noqa: E402
from web.routes.lfg_route import router as lfg_router                  # noqa: E402
from web.routes.landing_route import router as landing_router          # noqa: E402  (D9: oeffentliche Landing `/`)
from web.routes.home_route import router as home_router                # noqa: E402  (D9: Post-Login `/home`)
from web.routes.rbac_route import router as rbac_router                # noqa: E402  (RBAC: /rbac — war nicht registriert)
from web.routes.audit_route import router as audit_router              # noqa: E402  (RBAC: /audit — war nicht registriert)

app.include_router(landing_router)   # D9: `/` (anon Landing) + `/partials/landing-stats`
app.include_router(home_router)      # D9: `/home` (Post-Login-Startseite)
app.include_router(rbac_router)      # RBAC: /rbac + /rbac/config (owner/perm-gated)
app.include_router(audit_router)     # RBAC: /audit (owner/perm-gated)
app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(errors_router)
app.include_router(config_router)
app.include_router(system_router)
app.include_router(server_detail_router)
app.include_router(analytics_router)
app.include_router(marshal_bot_router)
app.include_router(health_router)
app.include_router(security_router)
app.include_router(forecast_router)
app.include_router(backup_status_router)
app.include_router(export_router)
app.include_router(changelog_router)
app.include_router(theme_router)
app.include_router(config_reload_router)
app.include_router(webhook_router)
app.include_router(correlation_router)
app.include_router(search_router)
app.include_router(leveling_router)
app.include_router(moderation_router)
app.include_router(lfg_router)


# F64: CSRF-Token wird jetzt direkt in der CSRFMiddleware (Pure ASGI) gesetzt.
# Die separate add_csrf_to_templates Middleware ist nicht mehr noetig,
# da CSRFMiddleware scope["state"].csrf_token setzt.


# --- Startup/Shutdown Events ---

@app.on_event("startup")
async def on_startup():
    """Wird beim Start des Servers ausgefuehrt."""
    global _broadcaster_task
    # F28: SQLite-Datenbank initialisieren
    try:
        await init_db()
        logger.info("SQLite-Datenbank fuer Dashboard initialisiert")
    except Exception as e:
        logger.error(f"Datenbank-Initialisierung fehlgeschlagen: {e}")
    # D3: Dashboard-WS-Broadcaster starten (ersetzt SSE)
    import asyncio as _asyncio
    _broadcaster_task = _asyncio.create_task(_dashboard_broadcaster())
    logger.info(f"Web Dashboard gestartet auf Port {WEB_PORT}")


@app.on_event("shutdown")
async def on_shutdown():
    """Wird beim Herunterfahren des Servers ausgefuehrt."""
    global _broadcaster_task
    # D3: Broadcaster sauber beenden
    if _broadcaster_task is not None:
        _broadcaster_task.cancel()
        try:
            await _broadcaster_task
        except Exception:  # noqa: BLE001 — CancelledError o.ae. erwartet
            pass
        _broadcaster_task = None
    # F28: Datenbank sauber schliessen
    await close_db()
    logger.info("Web Dashboard wird heruntergefahren")


# --- Einstiegspunkt fuer direkten Start ---

if __name__ == "__main__":
    import os
    import uvicorn
    # Etappe 4-Followup B104: Default 127.0.0.1, override via WEB_HOST=0.0.0.0 nur in Dev
    # Production laeuft eh ueber systemd-ExecStart mit --host 127.0.0.1 (Etappe 1.4).
    uvicorn.run(
        "web.app:app",
        host=os.getenv("WEB_HOST", "127.0.0.1"),
        port=WEB_PORT,
        reload=False,
        log_level="info",
    )
