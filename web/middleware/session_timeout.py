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

# Zweistufiger Idle-Timeout (Marco 2026-07-14), Sliding-Window auf `last_seen`:
#   SOFT (10 min): Soft-Logout → zur Login-Seite, Cookie BLEIBT erhalten. Rueckkehr < HARD
#     ist ein stiller Discord-Re-Login (prompt=consent ist entfernt). last_seen wird im
#     Soft-Fenster NICHT weitergeschoben → idle waechst weiter Richtung HARD.
#   HARD (60 min): Voll-Logout → Session leeren + dashboard_token-Cookie loeschen.
# Absolutes Cap deckt zusaetzlich das JWT selbst ab (exp = 24 h, web/auth.py).
SOFT_IDLE_TIMEOUT = 10 * 60   # 10 min — Soft-Logout, Cookie bleibt
HARD_IDLE_TIMEOUT = 60 * 60   # 60 min — Cookie wird geloescht

# Pfade die kein Login erfordern
PUBLIC_PATHS = {
    "/auth/login", "/auth/discord", "/auth/discord/callback",
    "/auth/logout", "/api/health", "/static", "/ws", "/favicon.ico",
}


class SessionTimeoutMiddleware:
    """
    Pure ASGI Middleware fuer Idle-Session-Timeout.

    C1/M06-Fix: Auth laeuft ueber den JWT-Cookie `dashboard_token` (NICHT
    request.session). Vorher pruefte die Middleware `session["user"]`, das nie
    gesetzt wurde → Idle-Timeout griff nie. Jetzt: dashboard_token dekodieren,
    `last_seen` in der signierten Session fuehren, bei >10min Idle ausloggen.
    Das absolute Timeout (24h) kommt aus dem JWT-`exp`.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        for public in PUBLIC_PATHS:
            if path.startswith(public):
                await self.app(scope, receive, send)
                return

        request = Request(scope)

        # Auth via JWT-Cookie. Kein Token → nicht eingeloggt, Route-Auth uebernimmt.
        token = request.cookies.get("dashboard_token")
        if not token:
            await self.app(scope, receive, send)
            return

        from web.auth import _decode_jwt
        payload = _decode_jwt(token)
        if payload is None:
            # Abgelaufen/ungueltig (24h-exp = absolutes Timeout) → Route-Auth faengt es ab.
            await self.app(scope, receive, send)
            return

        try:
            session = request.session
        except Exception:
            await self.app(scope, receive, send)
            return

        now = time.time()
        last_seen = session.get("last_seen")

        if last_seen:
            idle = now - last_seen
            if idle > HARD_IDLE_TIMEOUT:
                # 60 min inaktiv → Voll-Logout: Session leeren + Cookie loeschen.
                logger.info(f"Hard-Idle ({idle / 60:.0f}min) → Logout + Cookie-Delete fuer "
                            f"{payload.get('username', '?')}")
                session.clear()
                response = RedirectResponse(url="/auth/login?reason=inactive", status_code=302)
                response.delete_cookie("dashboard_token")
                await response(scope, receive, send)
                return
            if idle > SOFT_IDLE_TIMEOUT:
                # 10 min inaktiv → Soft-Logout: zur Login-Seite, Cookie BLEIBT (schnelle
                # Rueckkehr < 60 min via stillem Re-Login). last_seen NICHT sliden, damit
                # idle Richtung HARD weiterwaechst.
                response = RedirectResponse(url="/auth/login?reason=inactive", status_code=302)
                await response(scope, receive, send)
                return

        # Aktive Nutzung (idle <= SOFT) → Sliding-Window aktualisieren.
        session["last_seen"] = now
        await self.app(scope, receive, send)
