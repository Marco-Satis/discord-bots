"""
Phase 13a: Web Dashboard — FastAPI Hauptanwendung

Startet den Web-Dashboard-Server mit Jinja2-Templates,
statischen Dateien und WebSocket-Unterstuetzung.
"""

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
    import secrets as _secrets
    WEB_SECRET_KEY = _secrets.token_hex(32)
    logger.warning("WEB_SECRET_KEY nicht in .env gesetzt! Generiere temporaeren Key. "
                    "Setze WEB_SECRET_KEY in config/.env fuer persistente Sessions.")

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
    """Verwaltet aktive WebSocket-Verbindungen fuer Echtzeit-Updates."""

    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        """Neue WebSocket-Verbindung akzeptieren und registrieren."""
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.debug(f"WebSocket verbunden. Aktive Verbindungen: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        """WebSocket-Verbindung entfernen."""
        self.active_connections.remove(websocket)
        logger.debug(f"WebSocket getrennt. Aktive Verbindungen: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        """Nachricht an alle verbundenen Clients senden."""
        import json
        data = json.dumps(message)
        for connection in self.active_connections[:]:
            try:
                await connection.send_text(data)
            except Exception:
                self.active_connections.remove(connection)


ws_manager = ConnectionManager()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket-Endpunkt fuer Echtzeit-Updates an das Dashboard."""
    await ws_manager.connect(websocket)
    try:
        while True:
            # Auf Nachrichten vom Client warten (Heartbeat/Ping)
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


# --- Routen einbinden ---

from web.auth import auth_router          # noqa: E402
from web.routes.dashboard import router as dashboard_router      # noqa: E402
from web.routes.errors_route import router as errors_router      # noqa: E402
from web.routes.config_route import router as config_router      # noqa: E402
from web.routes.system_route import router as system_router      # noqa: E402
from web.routes.server_detail import router as server_detail_router  # noqa: E402
from web.routes.analytics_route import router as analytics_router    # noqa: E402
from web.routes.admin_bot_route import router as admin_bot_router    # noqa: E402
from web.routes.health_route import router as health_router          # noqa: E402
from web.routes.security_route import router as security_router      # noqa: E402
from web.routes.sse_route import router as sse_router                # noqa: E402
from web.routes.forecast_route import router as forecast_router      # noqa: E402
from web.routes.backup_status_route import router as backup_status_router  # noqa: E402
from web.routes.export_route import router as export_router          # noqa: E402
from web.routes.changelog_route import router as changelog_router    # noqa: E402
from web.routes.theme_route import router as theme_router            # noqa: E402
from web.routes.config_reload_route import router as config_reload_router  # noqa: E402
from web.routes.webhook_route import router as webhook_router        # noqa: E402
from web.routes.correlation_route import router as correlation_router  # noqa: E402
from web.routes.search_route import router as search_router            # noqa: E402

app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(errors_router)
app.include_router(config_router)
app.include_router(system_router)
app.include_router(server_detail_router)
app.include_router(analytics_router)
app.include_router(admin_bot_router)
app.include_router(health_router)
app.include_router(security_router)
app.include_router(sse_router)
app.include_router(forecast_router)
app.include_router(backup_status_router)
app.include_router(export_router)
app.include_router(changelog_router)
app.include_router(theme_router)
app.include_router(config_reload_router)
app.include_router(webhook_router)
app.include_router(correlation_router)
app.include_router(search_router)


# F64: CSRF-Token wird jetzt direkt in der CSRFMiddleware (Pure ASGI) gesetzt.
# Die separate add_csrf_to_templates Middleware ist nicht mehr noetig,
# da CSRFMiddleware scope["state"].csrf_token setzt.


# --- Startup/Shutdown Events ---

@app.on_event("startup")
async def on_startup():
    """Wird beim Start des Servers ausgefuehrt."""
    # F28: SQLite-Datenbank initialisieren
    try:
        await init_db()
        logger.info("SQLite-Datenbank fuer Dashboard initialisiert")
    except Exception as e:
        logger.error(f"Datenbank-Initialisierung fehlgeschlagen: {e}")
    logger.info(f"Web Dashboard gestartet auf Port {WEB_PORT}")


@app.on_event("shutdown")
async def on_shutdown():
    """Wird beim Herunterfahren des Servers ausgefuehrt."""
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
