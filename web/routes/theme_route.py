"""
Theme-Toggle API — Speichert und liefert die Theme-Praeferenz.

Stellt POST /api/theme/toggle und GET /api/theme bereit.
Die Praeferenz wird in der Server-Session gespeichert,
damit sie nach einem Browser-Neustart erhalten bleibt.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from utils.logger import get_logger
from web.auth import require_auth_api

logger = get_logger("web.routes.theme")

router = APIRouter(tags=["Theme"])


@router.get("/api/theme")
async def get_theme(request: Request, current_user: dict = Depends(require_auth_api)) -> JSONResponse:
    """
    Gibt die aktuelle Theme-Praeferenz des Benutzers zurueck.

    Liest den Wert aus der Session. Standard ist 'dark'.
    """
    # Theme aus Session lesen (Standard: dark)
    theme = request.session.get("theme", "dark")
    return JSONResponse(content={"theme": theme})


@router.post("/api/theme/toggle")
async def toggle_theme(request: Request, current_user: dict = Depends(require_auth_api)) -> JSONResponse:
    """
    Wechselt das Theme zwischen 'dark' und 'light'.

    Speichert die neue Praeferenz in der Server-Session
    und gibt das neue Theme zurueck.
    """
    # Aktuelles Theme lesen und umschalten
    current = request.session.get("theme", "dark")
    new_theme = "light" if current == "dark" else "dark"
    request.session["theme"] = new_theme

    username = current_user.get("username", "Unbekannt")
    logger.debug(f"Theme gewechselt auf '{new_theme}' fuer Benutzer '{username}'")

    return JSONResponse(content={"theme": new_theme})
