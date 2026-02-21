"""
Phase 1: Sicherheits-Dashboard — Fail2Ban, SSL, Port-Status.

Zeigt den aktuellen Sicherheitsstatus: Fail2Ban-Jails und gebannte IPs,
SSL-Zertifikat-Status und Port-Erreichbarkeit.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from utils.config import DATA_DIR
from utils.logger import get_logger
from web.auth import get_current_user

logger = get_logger("web.routes.security")

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter(tags=["Security"])

MONITOR_DATA_DIR = DATA_DIR / "monitor"


def _read_json(filepath: Path) -> dict:
    """Liest eine JSON-Datei oder gibt leeres Dict zurueck."""
    try:
        if filepath.exists():
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.debug(f"JSON lesen fehlgeschlagen: {filepath}: {e}")
    return {}


@router.get("/security", response_class=HTMLResponse)
async def security_page(request: Request):
    """Sicherheits-Dashboard mit Fail2Ban, SSL und Port-Status."""
    user = get_current_user(request)
    if user is None:
        return RedirectResponse(url="/auth/login", status_code=302)

    # Fail2Ban-Status aus StatusWriter JSON lesen
    fail2ban_data = _read_json(MONITOR_DATA_DIR / "fail2ban_status.json")
    ssl_data = _read_json(MONITOR_DATA_DIR / "ssl_status.json")
    port_data = _read_json(MONITOR_DATA_DIR / "port_status.json")

    return templates.TemplateResponse("security.html", {
        "request": request,
        "user": user,
        "fail2ban": fail2ban_data,
        "ssl": ssl_data,
        "ports": port_data,
    })
