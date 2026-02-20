"""
Phase 13a: Dashboard-Uebersicht — Hauptseite des Web-Dashboards

Zeigt Server-Status, Bot-Status, System-Performance und
aktuelle Ereignisse in einer Kacheluebersicht an.
"""

import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from utils.config import PROJECT_ROOT, DATA_DIR, MONITOR_DATA_DIR
from utils.logger import get_logger
from web.auth import get_current_user

logger = get_logger("web.routes.dashboard")

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter(tags=["Dashboard"])


def _load_json_safe(filepath: Path) -> dict:
    """Laedt eine JSON-Datei sicher. Gibt leeres Dict bei Fehler zurueck."""
    try:
        if filepath.exists():
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Konnte {filepath} nicht laden: {e}")
    return {}


def _collect_server_status() -> list[dict]:
    """
    Sammelt Server-Status-Daten aus data/monitor/ JSON-Dateien.
    Gibt eine Liste von Server-Status-Dicts zurueck.
    """
    servers = []
    monitor_dir = MONITOR_DATA_DIR

    if not monitor_dir.exists():
        logger.debug(f"Monitor-Verzeichnis nicht gefunden: {monitor_dir}")
        return servers

    # Alle JSON-Dateien im Monitor-Verzeichnis durchsuchen
    for json_file in sorted(monitor_dir.glob("*.json")):
        data = _load_json_safe(json_file)
        if data:
            servers.append({
                "name": data.get("name", json_file.stem),
                "status": data.get("status", "unknown"),
                "players": data.get("players", 0),
                "max_players": data.get("max_players", 0),
                "uptime": data.get("uptime", "N/A"),
                "type": data.get("type", "unknown"),
                "source_file": json_file.name,
            })

    return servers


def _collect_system_stats() -> dict:
    """
    Sammelt System-Performance-Daten (CPU, RAM, Disk).
    Versucht psutil zu verwenden, gibt Fallback-Werte zurueck.
    """
    stats = {
        "cpu_percent": 0,
        "ram_percent": 0,
        "ram_used_gb": 0,
        "ram_total_gb": 0,
        "disk_percent": 0,
        "disk_used_gb": 0,
        "disk_total_gb": 0,
    }
    try:
        import psutil
        stats["cpu_percent"] = psutil.cpu_percent(interval=0.5)

        mem = psutil.virtual_memory()
        stats["ram_percent"] = mem.percent
        stats["ram_used_gb"] = round(mem.used / (1024 ** 3), 1)
        stats["ram_total_gb"] = round(mem.total / (1024 ** 3), 1)

        disk = psutil.disk_usage("/")
        stats["disk_percent"] = disk.percent
        stats["disk_used_gb"] = round(disk.used / (1024 ** 3), 1)
        stats["disk_total_gb"] = round(disk.total / (1024 ** 3), 1)
    except ImportError:
        logger.debug("psutil nicht installiert — System-Stats nicht verfuegbar")
    except Exception as e:
        logger.warning(f"Fehler beim Lesen der System-Stats: {e}")

    return stats


def _collect_bot_status() -> list[dict]:
    """
    Sammelt Bot-Status-Informationen aus bekannten Status-Dateien.
    """
    bots = []
    bot_names = {
        "gameserver": "GameServer Bot",
        "monitor": "Monitor Bot",
        "admin": "Admin Bot",
    }

    for bot_id, display_name in bot_names.items():
        status_file = DATA_DIR / bot_id / "bot_status.json"
        data = _load_json_safe(status_file)
        bots.append({
            "id": bot_id,
            "name": display_name,
            "status": data.get("status", "unknown"),
            "ping": data.get("ping_ms", "N/A"),
            "uptime": data.get("uptime", "N/A"),
        })

    return bots


def _collect_recent_events() -> list[dict]:
    """
    Sammelt die letzten Ereignisse aus Event-Log-Dateien.
    """
    events = []
    event_file = DATA_DIR / "monitor" / "events.json"
    data = _load_json_safe(event_file)

    if isinstance(data, list):
        events = data[-20:]  # Letzte 20 Ereignisse
    elif isinstance(data, dict) and "events" in data:
        events = data["events"][-20:]

    return events


@router.get("/", response_class=HTMLResponse)
async def dashboard_overview(request: Request):
    """
    Hauptseite des Dashboards mit Uebersicht aller Server,
    Bots und System-Performance.
    """
    user = get_current_user(request)
    if user is None:
        return RedirectResponse(url="/auth/login", status_code=302)

    servers = _collect_server_status()
    system_stats = _collect_system_stats()
    bot_status = _collect_bot_status()
    recent_events = _collect_recent_events()

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "servers": servers,
        "system": system_stats,
        "bots": bot_status,
        "events": recent_events,
    })
