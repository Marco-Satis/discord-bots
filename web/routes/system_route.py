"""
Phase 13i: System / Webmin — Eingebettete Webmin-Verwaltung

Stellt eine Seite mit einem Iframe bereit, der auf das
lokale Webmin-Interface verweist. URL konfigurierbar via WEB_WEBMIN_URL.
Zeigt zusaetzlich System-Informationen (CPU, RAM, Disk, Uptime) an.
"""

import platform
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from utils.config import get_env
from utils.logger import get_logger
from web.auth import get_current_user

logger = get_logger("web.routes.system")

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter(tags=["System"])

# Webmin-URL konfigurierbar (Standard: lokaler Webmin-Port)
WEBMIN_URL = get_env("WEB_WEBMIN_URL", "https://localhost:10000")


def _get_system_info() -> dict:
    """Sammelt grundlegende System-Informationen via psutil (falls verfuegbar)."""
    info = {
        "hostname": platform.node(),
        "os": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "cpu_count": 0,
        "cpu_percent": 0.0,
        "ram_total_gb": 0.0,
        "ram_used_percent": 0.0,
        "disk_total_gb": 0.0,
        "disk_used_percent": 0.0,
        "uptime": "Unbekannt",
    }
    try:
        import psutil
        import time

        info["cpu_count"] = psutil.cpu_count(logical=True) or 0
        info["cpu_percent"] = psutil.cpu_percent(interval=0.1)

        mem = psutil.virtual_memory()
        info["ram_total_gb"] = round(mem.total / (1024 ** 3), 1)
        info["ram_used_percent"] = mem.percent

        disk = psutil.disk_usage("/")
        info["disk_total_gb"] = round(disk.total / (1024 ** 3), 1)
        info["disk_used_percent"] = disk.percent

        boot_time = psutil.boot_time()
        uptime_sec = int(time.time() - boot_time)
        days, remainder = divmod(uptime_sec, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, _ = divmod(remainder, 60)
        info["uptime"] = f"{days}d {hours}h {minutes}m"
    except ImportError:
        logger.debug("psutil nicht verfuegbar — System-Info eingeschraenkt")
    except Exception as e:
        logger.warning(f"Fehler beim Sammeln der System-Info: {e}")

    return info


@router.get("/system", response_class=HTMLResponse)
async def system_page(request: Request):
    """
    Zeigt die System-Verwaltungsseite mit System-Info und eingebettetem Webmin-Iframe.
    """
    user = get_current_user(request)
    if user is None:
        return RedirectResponse(url="/auth/login", status_code=302)

    system_info = _get_system_info()

    return templates.TemplateResponse("system.html", {
        "request": request,
        "user": user,
        "webmin_url": WEBMIN_URL,
        "system_info": system_info,
    })
