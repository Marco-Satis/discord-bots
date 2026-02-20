"""
Phase 13a: Web Dashboard — FastAPI Hauptanwendung

Startet den Web-Dashboard-Server mit Jinja2-Templates,
statischen Dateien und WebSocket-Unterstuetzung.
"""

import sys
import os
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

# Umgebungsvariablen laden
load_env()

logger = get_logger("web.dashboard")

# Pruefen ob Web-Dashboard aktiviert ist
WEB_ENABLED = get_env("WEB_ENABLED", "false", cast=bool)
WEB_PORT = get_env("WEB_PORT", 8080, cast=int)
WEB_SECRET_KEY = get_env("WEB_SECRET_KEY", "CHANGE_ME_INSECURE_DEFAULT_KEY")

if not WEB_ENABLED:
    print("[Web Dashboard] WEB_ENABLED ist nicht 'true'. Dashboard ist deaktiviert.")
    print("[Web Dashboard] Setze WEB_ENABLED=true in config/.env um das Dashboard zu starten.")
    sys.exit(0)

# FastAPI-App erstellen
app = FastAPI(
    title="Discord Bot Dashboard",
    description="Web-Dashboard fuer das Discord Bot System",
    version="1.0.0",
    docs_url=None,      # Swagger-UI deaktivieren in Produktion
    redoc_url=None       # ReDoc deaktivieren in Produktion
)

# Session-Middleware fuer Cookie-basierte Sessions
app.add_middleware(
    SessionMiddleware,
    secret_key=WEB_SECRET_KEY,
    session_cookie="dashboard_session",
    max_age=86400,       # 24 Stunden
    same_site="lax",
    https_only=False     # In Produktion auf True setzen
)

# CORS-Middleware fuer API-Zugriffe
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],           # In Produktion einschraenken
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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

app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(errors_router)
app.include_router(config_router)
app.include_router(system_router)
app.include_router(server_detail_router)
app.include_router(analytics_router)
app.include_router(admin_bot_router)


# --- Startup/Shutdown Events ---

@app.on_event("startup")
async def on_startup():
    """Wird beim Start des Servers ausgefuehrt."""
    logger.info(f"Web Dashboard gestartet auf Port {WEB_PORT}")


@app.on_event("shutdown")
async def on_shutdown():
    """Wird beim Herunterfahren des Servers ausgefuehrt."""
    logger.info("Web Dashboard wird heruntergefahren")


# --- Einstiegspunkt fuer direkten Start ---

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "web.app:app",
        host="0.0.0.0",
        port=WEB_PORT,
        reload=False,
        log_level="info"
    )
