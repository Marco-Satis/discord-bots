"""
F64: CSRF-Schutz fuer Dashboard — Cross-Site Request Forgery Prevention

Generiert CSRF-Token pro Session, validiert bei POST/PUT/DELETE.
HTMX-Requests senden Token als X-CSRF-Token Header.

WICHTIG: Pure ASGI Middleware (kein BaseHTTPMiddleware), um
Body-Forwarding-Probleme bei POST-Requests zu vermeiden.
BaseHTTPMiddleware kann den Request-Body konsumieren, sodass
nachfolgende Route-Handler leere Form-Daten erhalten.
"""

import hashlib
import hmac
import secrets
from starlette.datastructures import State
from starlette.requests import Request
from starlette.responses import Response, JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

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


def _identity_csrf_token(request: Request) -> str | None:
    """C2/M17-Fix: CSRF-Token an die authentifizierte Identitaet binden.

    Token = HMAC(WEB_SECRET_KEY, jwt['sub']). Stateless Double-Submit: ein
    fremder Origin kann den Token ohne den Server-Secret nicht berechnen, und
    er ist an genau diesen User gebunden. Gibt None zurueck wenn nicht
    eingeloggt (dann Session-Fallback fuer z.B. die Login-Form).
    """
    token = request.cookies.get("dashboard_token")
    if not token:
        return None
    try:
        from web.auth import WEB_SECRET_KEY, _decode_jwt
        payload = _decode_jwt(token)
        if not payload:
            return None
        sub = str(payload.get("sub", ""))
        if not sub:
            return None
        return hmac.new(WEB_SECRET_KEY.encode("utf-8"), sub.encode("utf-8"),
                        hashlib.sha256).hexdigest()
    except Exception:  # noqa: BLE001
        return None


def generate_csrf_token(request: Request) -> str:
    """Liefert das CSRF-Token: identitaets-gebunden (HMAC ueber JWT-sub) wenn
    eingeloggt, sonst Session-basiert (z.B. Login-Form vor Auth)."""
    identity_token = _identity_csrf_token(request)
    if identity_token:
        return identity_token
    session = request.session
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_hex(32)
        session["csrf_token"] = token
    return token


def validate_csrf_token(request: Request, token: str) -> bool:
    """Validiert ein CSRF-Token. Eingeloggt → gegen HMAC(JWT-sub); sonst Session."""
    if not token:
        return False
    identity_token = _identity_csrf_token(request)
    if identity_token is not None:
        return hmac.compare_digest(identity_token, token)
    session_token = request.session.get("csrf_token")
    if not session_token:
        return False
    return secrets.compare_digest(session_token, token)


class CSRFMiddleware:
    """
    Pure ASGI CSRF-Schutz Middleware.

    Validiert X-CSRF-Token Header bei geschuetzten HTTP-Methoden.
    Liest NICHT den Request-Body (im Gegensatz zu BaseHTTPMiddleware),
    um Body-Forwarding-Probleme zu vermeiden.

    Setzt ausserdem request.state.csrf_token fuer Template-Rendering,
    sodass die separate add_csrf_to_templates Middleware entfaellt.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Nur HTTP-Requests verarbeiten (kein WebSocket, Lifespan etc.)
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)

        # CSRF-Token in Session sicherstellen + in scope.state fuer Templates
        try:
            csrf_token = generate_csrf_token(request)
        except Exception:
            # Session noch nicht verfuegbar (z.B. Static Files)
            csrf_token = ""  # nosec B105 - leerer Default falls Session nicht initialisiert

        # Token in scope["state"] setzen fuer Template-Zugriff via request.state.csrf_token
        # Starlette setzt scope["state"] manchmal als dict, manchmal als State-Objekt.
        # Wir arbeiten direkt mit dem dict-Zugriff um kompatibel zu bleiben.
        if "state" not in scope:
            scope["state"] = {"csrf_token": csrf_token}
        elif isinstance(scope["state"], dict):
            scope["state"]["csrf_token"] = csrf_token
        else:
            # State-Objekt (hat __setattr__)
            scope["state"].csrf_token = csrf_token

        # Nur geschuetzte Methoden pruefen
        method = scope.get("method", "GET")
        if method not in PROTECTED_METHODS:
            await self.app(scope, receive, send)
            return

        # Pfad ermitteln
        path = scope.get("path", "")

        # Ausgenommene Pfade
        if path in EXEMPT_PATHS or path.startswith("/api/health"):
            await self.app(scope, receive, send)
            return

        # Webhook-Pfade ausnehmen (externe Systeme haben kein CSRF-Token)
        if path.startswith("/api/webhook"):
            await self.app(scope, receive, send)
            return

        # Token NUR aus Header lesen — NICHT aus Form-Body!
        # (Form-Body lesen wuerde den Body konsumieren und nachfolgende
        #  Handler bekommen leere Form-Daten)
        headers = dict(scope.get("headers", []))
        token = ""  # nosec B105 - leerer Default vor Header-Lookup
        for key, value in scope.get("headers", []):
            if key == b"x-csrf-token":
                token = value.decode("utf-8", errors="replace")
                break

        # Token validieren
        if not token or not validate_csrf_token(request, token):
            # Nicht eingeloggte Benutzer nicht mit CSRF blockieren
            # Auth laeuft per JWT-Cookie (dashboard_token), nicht per Session
            has_auth = request.cookies.get("dashboard_token") is not None

            if not has_auth:
                # Kein Login → durchlassen (Route prueft Auth separat)
                await self.app(scope, receive, send)
                return

            logger.warning(f"CSRF-Validierung fehlgeschlagen: {method} {path}")

            accept_header = ""
            for key, value in scope.get("headers", []):
                if key == b"accept":
                    accept_header = value.decode("utf-8", errors="replace")
                    break

            if "application/json" in accept_header:
                response = JSONResponse(
                    {"detail": "CSRF-Token ungueltig oder fehlend"},
                    status_code=403
                )
            else:
                response = Response(
                    "403 Forbidden — CSRF-Token ungueltig",
                    status_code=403,
                    media_type="text/plain"
                )
            await response(scope, receive, send)
            return

        # Alles OK — Request weiterleiten (Body bleibt unangetastet!)
        await self.app(scope, receive, send)
