"""
F64: CSRF-Schutz fuer Dashboard — Cross-Site Request Forgery Prevention

Generiert CSRF-Token pro Session, validiert bei POST/PUT/DELETE.
HTMX-Requests senden Token als X-CSRF-Token Header.
"""

import secrets
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse

from utils.logger import get_logger

logger = get_logger("web.csrf")

# Methoden die CSRF-Schutz benoetigen
PROTECTED_METHODS = {"POST", "PUT", "DELETE", "PATCH"}

# Endpunkte die vom CSRF-Schutz ausgenommen sind
EXEMPT_PATHS = {
    "/auth/login",           # Login-Form (noch keine Session)
    "/auth/discord/callback",  # OAuth Callback
    "/api/health",           # Health-Endpoint (kein Auth)
    "/ws",                   # WebSocket
}


def generate_csrf_token(request: Request) -> str:
    """Generiert oder liest CSRF-Token aus der Session."""
    session = request.session
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_hex(32)
        session["csrf_token"] = token
    return token


def validate_csrf_token(request: Request, token: str) -> bool:
    """Validiert ein CSRF-Token gegen die Session."""
    session_token = request.session.get("csrf_token")
    if not session_token:
        return False
    return secrets.compare_digest(session_token, token)


class CSRFMiddleware(BaseHTTPMiddleware):
    """
    CSRF-Schutz Middleware fuer FastAPI.

    - Generiert Token und speichert in Session
    - Validiert Token bei geschuetzten Methoden
    - Token wird aus Header X-CSRF-Token oder Form-Feld csrf_token gelesen
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # CSRF-Token in Session sicherstellen
        generate_csrf_token(request)

        # Nur geschuetzte Methoden pruefen
        if request.method not in PROTECTED_METHODS:
            return await call_next(request)

        # Ausgenommene Pfade
        path = request.url.path
        if path in EXEMPT_PATHS:
            return await call_next(request)

        # Health-API Pfade ausnehmen
        if path.startswith("/api/health"):
            return await call_next(request)

        # Token aus Header oder Form lesen
        token = request.headers.get("X-CSRF-Token", "")

        if not token:
            # Versuche aus Form-Daten zu lesen (fuer normale Formulare)
            content_type = request.headers.get("content-type", "")
            if "form" in content_type:
                try:
                    form = await request.form()
                    token = form.get("csrf_token", "")
                except Exception:
                    token = ""

        # Token validieren
        if not token or not validate_csrf_token(request, token):
            # Nicht eingeloggte Benutzer nicht mit CSRF blockieren
            user = request.session.get("user")
            if not user:
                return await call_next(request)

            logger.warning(f"CSRF-Validierung fehlgeschlagen: {request.method} {path}")
            if "application/json" in request.headers.get("accept", ""):
                return JSONResponse(
                    {"detail": "CSRF-Token ungueltig oder fehlend"},
                    status_code=403
                )
            return Response(
                "403 Forbidden — CSRF-Token ungueltig",
                status_code=403,
                media_type="text/plain"
            )

        return await call_next(request)
