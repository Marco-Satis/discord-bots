"""
Dashboard-Live-Daten-Sammler (D3: WebSocket-Push statt SSE).

Sammelt den Echtzeit-Payload für das Dashboard (Server-Status, System-Stats,
Bot-Status, letzte Events). Früher im SSE-Generator (`web/routes/sse_route.py`, 2026-08-14 geloescht,
2026-06-04 entfernt) — jetzt vom WebSocket-Broadcaster in `web/app.py` genutzt.

Quelle der Daten: `data/monitor/*_status.json`, `data/<bot>/bot_status.json`,
psutil (System), SQLite (`events`). Alle Sammler sind sync/IO-leicht und werden
im Broadcaster via `asyncio.to_thread` aufgerufen (kein Event-Loop-Block).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from utils.config import DATA_DIR, MONITOR_DATA_DIR, get_env
from utils.logger import get_logger
from modules.database.db_manager import get_read_db

logger = get_logger("web.dashboard_feed")


def _load_json_safe(filepath: Path) -> dict:
    """Lädt eine JSON-Datei sicher. Gibt leeres Dict bei Fehler zurück."""
    try:
        if filepath.exists():
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Konnte {filepath} nicht laden: {e}")
    return {}


# Status-Dateien in data/monitor/, die KEINE Game-Server sind und daher nicht
# als Server-Karte auftauchen duerfen (sonst: toter /server/<id>-Details-Link).
# ssl_status.json = SSL-Zertifikats-Monitor -> gehoert auf /security, nicht hierher.
_NON_SERVER_STATUS = {"bot_status.json", "ssl_status.json"}


def collect_server_status() -> list[dict]:
    """Server-Status aus data/monitor/*_status.json (ohne Nicht-Server-Monitore)."""
    servers: list[dict] = []
    if not MONITOR_DATA_DIR.exists():
        return servers
    for json_file in sorted(MONITOR_DATA_DIR.glob("*_status.json")):
        if json_file.name in _NON_SERVER_STATUS:
            continue
        data = _load_json_safe(json_file)
        if data:
            server_id = json_file.stem.replace("_status", "")
            servers.append({
                "id": data.get("id", server_id),
                "name": data.get("name", server_id),
                "status": data.get("status", "unknown"),
                "players": data.get("players", 0),
                "max_players": data.get("max_players", 0),
                "uptime": data.get("uptime", "N/A"),
                "type": data.get("type", "unknown"),
                "cpu_percent": data.get("cpu_percent", 0),
                "memory_mb": data.get("memory_mb", 0),
            })
    return servers


def collect_system_stats() -> dict:
    """System-Performance (CPU/RAM/Disk) via psutil — Fallback-0 ohne psutil."""
    # cpu_cores gehoert dazu, auch wenn es sich nie aendert: das Template liest
    # system.cpu_cores, und ohne den Schluessel setzt der 5-Sekunden-Push die
    # Kernzahl auf 0 zurueck, die der Server-Render gerade richtig gezeigt hat.
    stats: dict[str, float] = {
        "cpu_percent": 0, "cpu_cores": 0,
        "ram_percent": 0, "ram_used_gb": 0, "ram_total_gb": 0,
        "disk_percent": 0, "disk_used_gb": 0, "disk_total_gb": 0,
    }
    try:
        import psutil

        # interval=None: cached delta seit letztem Call (kein 100ms-Block).
        stats["cpu_percent"] = psutil.cpu_percent(interval=None)
        stats["cpu_cores"] = psutil.cpu_count() or 0
        mem = psutil.virtual_memory()
        stats["ram_percent"] = mem.percent
        stats["ram_used_gb"] = round(mem.used / (1024 ** 3), 1)
        stats["ram_total_gb"] = round(mem.total / (1024 ** 3), 1)
        # M37-Fix: Partition konfigurierbar (Windows-Dev vs Linux-Prod-Pfad).
        disk = psutil.disk_usage(get_env("DASHBOARD_DISK_PATH", "/"))
        stats["disk_percent"] = disk.percent
        stats["disk_used_gb"] = round(disk.used / (1024 ** 3), 1)
        stats["disk_total_gb"] = round(disk.total / (1024 ** 3), 1)
    except ImportError:
        logger.debug("psutil nicht installiert — System-Stats nicht verfügbar")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Fehler beim Lesen der System-Stats: {e}")
    return stats


def collect_bot_status() -> list[dict]:
    """Bot-Status aus data/<bot>/bot_status.json (gameserver/monitor/admin)."""
    bots: list[dict] = []
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


async def collect_recent_events(limit: int = 20) -> list[dict]:
    """Letzte Events aus SQLite (filtert '0 Updates'-Cron-Noise)."""
    try:
        db = await get_read_db()
        cursor = await db.execute(
            "SELECT timestamp, event_type, category, server_id, message, details "
            "FROM events "
            "WHERE NOT (event_type = 'package_check' AND message LIKE '0 Updates%') "
            "ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [{
            "timestamp": row["timestamp"],
            "event_type": row["event_type"],
            "category": row["category"],
            "server_id": row["server_id"],
            "message": row["message"],
            "details": row["details"],
        } for row in rows]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"DB-Abfrage fuer Events fehlgeschlagen: {e}")
        return []


async def gather_dashboard_payload() -> dict:
    """
    Kompletter Dashboard-Echtzeit-Payload (für WebSocket-Broadcast).

    Returns:
        {"servers": [...], "system": {...}, "bots": [...], "events": [...]}
        — IO-Sammler laufen parallel via to_thread (kein Event-Loop-Block).
    """
    servers, system, bots, events = await asyncio.gather(
        asyncio.to_thread(collect_server_status),
        asyncio.to_thread(collect_system_stats),
        asyncio.to_thread(collect_bot_status),
        collect_recent_events(limit=20),
    )
    return {"servers": servers, "system": system, "bots": bots, "events": events}
