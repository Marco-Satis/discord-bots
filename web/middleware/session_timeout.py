"""
F65: Dashboard Session-Timeout — Automatischer Logout nach Inaktivitaet

- 60 Minuten Inaktivitaets-Timeout (konfigurierbar)
- 24 Stunden absolutes Timeout
- "Angemeldet bleiben" verlaengert auf 7 Tage

WICHTIG: Pure ASGI Middleware (kein BaseHTTPMiddleware), um
Body-Forwarding-Probleme bei POST-Requests zu vermeiden.
"""

import time
from starlette.requests import Request
from starlette.responses import RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from utils.logger import get_logger

logger = get_logger("web.session_timeout")

# Timeout-Werte in Sekunden
INACTIVITY_TIMEOUT = 3600       # 60 Minuten
ABSOLUTE_TIMEOUT = 86400        # 24 Stunden
REMEMBER_ME_TIMEOUT = 604800    # 7 Tage

# Pfade die kein Login erfordern
PUBLIC_PATHS = {
    "/auth/login", "/auth/discord", "/auth/discord/callback",
    "/auth/logout", "/api/health", "/static", "/ws", "/favicon.ico",
}


class SessionTimeoutMiddleware:
    """
    Pure ASGI Middleware fuer Session-Timeout-Pruefung.

    - Aktualisiert last_activity Timestamp
    - Leitet zu Login weiter wenn Timeout ueberschritten
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        # Oeffentliche Pfade ueberspringen
        for public in PUBLIC_PATHS:
            if path.startswith(public):
                await self.app(scope, receive, send)
                return

        request = Request(scope)

        try:
            session = request.session
        except Exception:
            await self.app(scope, receive, send)
            return

        user = session.get("user")

        if not user:
            # Kein Login, kein Timeout-Check noetig
            await self.app(scope, receive, send)
            return

        now = time.time()
        last_activity = session.get("last_activity", 0)
        login_time = session.get("login_time", 0)
        remember_me = session.get("remember_me", False)

        # Absolutes Timeout pruefen
        max_session = REMEMBER_ME_TIMEOUT if remember_me else ABSOLUTE_TIMEOUT
        if login_time and (now - login_time) > max_session:
            logger.info(f"Absolutes Timeout fuer {user.get('username', '?')} "
                        f"({(now - login_time) / 3600:.1f}h)")
            session.clear()
            response = RedirectResponse(url="/auth/login?reason=session_expired", status_code=302)
            await response(scope, receive, send)
            return

        # Inaktivitaets-Timeout pruefen
        inactivity_limit = REMEMBER_ME_TIMEOUT if remember_me else INACTIVITY_TIMEOUT
        if last_activity and (now - last_activity) > inactivity_limit:
            logger.info(f"Inaktivitaets-Timeout fuer {user.get('username', '?')} "
                        f"({(now - last_activity) / 60:.0f}min)")
            session.clear()
            response = RedirectResponse(url="/auth/login?reason=inactive", status_code=302)
            await response(scope, receive, send)
            return

        # Aktivitaet aktualisieren
        session["last_activity"] = now

        await self.app(scope, receive, send)
