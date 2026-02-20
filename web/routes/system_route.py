"""
Phase 13a: System / Webmin — Eingebettete Webmin-Verwaltung

Stellt eine Seite mit einem Iframe bereit, der auf das
lokale Webmin-Interface (Port 10000) verweist.
"""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from utils.logger import get_logger
from web.auth import get_current_user

logger = get_logger("web.routes.system")

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter(tags=["System"])


@router.get("/system", response_class=HTMLResponse)
async def system_page(request: Request):
    """
    Zeigt die System-Verwaltungsseite mit eingebettetem Webmin-Iframe.
    """
    user = get_current_user(request)
    if user is None:
        return RedirectResponse(url="/auth/login", status_code=302)

    return templates.TemplateResponse("system.html", {
        "request": request,
        "user": user,
    })
