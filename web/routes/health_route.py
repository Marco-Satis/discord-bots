"""
F34: Health Route — Oeffentlicher Health-Check Endpunkt.

Stellt GET /api/health und GET /api/health/selftest bereit.
Kein Auth erforderlich (Rate-Limiting kommt in spaeterer Phase).
Liest Status-JSON-Dateien aus data/monitor/ und data/gameserver/.

NOTE (3.4 Refactor 2026-05-01 evening): KEIN Depends(require_auth) — alle
Health-Endpunkte sind bewusst PUBLIC (allow_anon) fuer externe Monitoring-
Systeme (Uptime Robot, Healthcheck.io etc.). Geben nur Status-Daten aus,
keine Writes. Rate-Limiting + IP-Allowlist in Plan v1.3 / v1.4.
"""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from utils.logger import get_logger
from utils.config import get_config, get_env, PROJECT_ROOT
from utils.config import MONITOR_DATA_DIR, GAMESERVER_DATA_DIR, ADMIN_DATA_DIR

logger = get_logger("web.routes.health")

router = APIRouter(tags=["Health"])
# allow_anon: bewusst public — Monitoring-Endpunkte fuer externe Health-Checks

# Bekannte Server-Status-Dateien die geprueft werden
SERVER_STATUS_FILES: list[dict] = [
    {
        "id": "satisfactory",
        "name": "Satisfactory",
        "file": MONITOR_DATA_DIR / "satisfactory_status.json",
    },
    {
        "id": "mc_bmc",
        "name": "Minecraft Better MC",
        "file": MONITOR_DATA_DIR / "mc_bmc_status.json",
    },
    {
        "id": "mc_vanilla",
        "name": "Minecraft Vanilla",
        "file": MONITOR_DATA_DIR / "mc_vanilla_status.json",
    },
]

# Maximales Alter einer Status-Datei in Sekunden bevor sie als veraltet gilt
MAX_STATUS_AGE_SECONDS: int = 300  # 5 Minuten


def _read_status_file(filepath: Path) -> Optional[dict]:
    """
    Liest eine JSON-Status-Datei sicher ein.

    Args:
        filepath: Pfad zur JSON-Datei

    Returns:
        Dict mit dem Inhalt oder None bei Fehler
    """
    try:
        if not filepath.exists():
            return None
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        return None
    except (json.JSONDecodeError, OSError, PermissionError) as e:
        logger.debug("Status-Datei nicht lesbar (%s): %s", filepath.name, e)
        return None


def _is_status_stale(data: dict) -> bool:
    """
    Prueft ob eine Status-Datei veraltet ist (aelter als MAX_STATUS_AGE_SECONDS).

    Args:
        data: Dict aus der Status-Datei (erwartet "last_update" Key)

    Returns:
        True wenn veraltet oder kein Zeitstempel vorhanden
    """
    last_update = data.get("last_update")
    if not last_update:
        return True

    try:
        # ISO-Format parsen
        update_time = datetime.fromisoformat(last_update.replace("Z", "+00:00"))
        if update_time.tzinfo is None:
            update_time = update_time.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - update_time).total_seconds()
        return age > MAX_STATUS_AGE_SECONDS
    except (ValueError, TypeError):
        return True


def _build_server_entry(config: dict, data: Optional[dict]) -> dict:
    """
    Erstellt einen Server-Eintrag fuer die Health-Response.

    Args:
        config: Konfiguration des Servers (id, name, file)
        data: Gelesene Status-Daten oder None

    Returns:
        Dict mit Server-Informationen
    """
    entry: dict = {
        "id": config["id"],
        "name": config["name"],
        "status": "unknown",
        "players": 0,
        "max_players": 0,
        "uptime": "N/A",
        "last_update": None,
        "stale": True,
    }

    if data is None:
        entry["status"] = "unknown"
        return entry

    # Status uebernehmen
    raw_status = data.get("status", "unknown")
    entry["status"] = raw_status
    entry["players"] = data.get("players", 0)
    entry["max_players"] = data.get("max_players", 0)
    entry["uptime"] = data.get("uptime", "N/A")
    entry["last_update"] = data.get("last_update")
    entry["stale"] = _is_status_stale(data)

    return entry


def _determine_overall_status(servers: list[dict]) -> str:
    """
    Bestimmt den Gesamtstatus basierend auf allen Server-Status.

    Args:
        servers: Liste der Server-Eintraege

    Returns:
        "ok" wenn alle online/unknown, "degraded" wenn teilweise offline,
        "error" wenn alle offline
    """
    if not servers:
        return "ok"

    statuses = [s.get("status", "unknown") for s in servers]

    # Alle offline oder error = error
    offline_states = {"offline", "error", "crashed"}
    if all(s in offline_states for s in statuses):
        return "error"

    # Mindestens ein Server offline = degraded
    if any(s in offline_states for s in statuses):
        return "degraded"

    # Alle veraltet = degraded
    if all(s.get("stale", True) for s in servers):
        return "degraded"

    return "ok"


@router.get("/api/health")
async def health_check() -> JSONResponse:
    """
    Oeffentlicher Health-Check Endpunkt.

    Liest Server-Status-Dateien und gibt den aggregierten Status zurueck.
    Kein Auth erforderlich.

    Returns:
        JSON mit Gesamtstatus und Server-Details.
        HTTP 200 wenn alles OK, HTTP 503 wenn Server down.
    """
    # Alle File-Reads parallel via to_thread (kein Event-Loop-Block).
    # Public-Health-Endpoint, hohe Hit-Rate von externen Monitoring-Tools.
    server_files = [config["file"] for config in SERVER_STATUS_FILES]
    bot_files = [GAMESERVER_DATA_DIR / "bot_status.json", MONITOR_DATA_DIR / "bot_status.json"]
    all_results = await asyncio.gather(
        *(asyncio.to_thread(_read_status_file, f) for f in server_files + bot_files),
    )
    server_data_list = all_results[: len(server_files)]
    bot_data_list = all_results[len(server_files):]

    servers: list[dict] = [
        _build_server_entry(config, data)
        for config, data in zip(SERVER_STATUS_FILES, server_data_list)
    ]
    gameserver_bot_status, monitor_bot_status = bot_data_list

    # Gesamtstatus bestimmen
    overall_status = _determine_overall_status(servers)

    response: dict = {
        "status": overall_status,
        "servers": servers,
        "bots": {
            "gameserver": gameserver_bot_status.get("status", "unknown") if gameserver_bot_status else "unknown",
            "monitor": monitor_bot_status.get("status", "unknown") if monitor_bot_status else "unknown",
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # HTTP-Status: 200 wenn OK, 503 wenn error
    status_code = 200 if overall_status != "error" else 503

    logger.debug("Health-Check: %s (%d Server)", overall_status, len(servers))

    return JSONResponse(content=response, status_code=status_code)


@router.get("/api/health/selftest")
async def health_selftest() -> JSONResponse:
    """
    Selftest-Endpunkt — fuehrt den Startup-Selftest aus und gibt Ergebnisse zurueck.

    Kein Auth erforderlich. Nuetzlich fuer Monitoring-Tools und Uptime-Checks.

    Returns:
        JSON mit Selftest-Ergebnissen.
    """
    try:
        from utils.selftest import get_selftest_json

        # Selftest fuer den Web-Service ausfuehren
        result = get_selftest_json(bot_name="web-dashboard")

        # HTTP-Status basierend auf kritischen Fehlern
        has_critical = result.get("critical", 0) > 0
        status_code = 503 if has_critical else 200

        logger.debug(
            "Selftest ausgefuehrt: %d/%d bestanden, %d kritisch",
            result.get("passed", 0), result.get("total", 0), result.get("critical", 0)
        )

        return JSONResponse(content=result, status_code=status_code)

    except ImportError as e:
        logger.error("Selftest-Modul nicht verfuegbar: %s", e)
        return JSONResponse(
            content={
                "error": "Selftest-Modul nicht verfuegbar",
                "detail": str(e),
            },
            status_code=500,
        )
    except Exception as e:
        logger.error("Selftest fehlgeschlagen: %s", e)
        return JSONResponse(
            content={
                "error": "Selftest fehlgeschlagen",
                "detail": str(e),
            },
            status_code=500,
        )


# ------------------------------------------------------------------
# F27: Health Auto-Restart Status
# ------------------------------------------------------------------

@router.get("/api/health/auto-restart")
async def health_auto_restart() -> JSONResponse:
    """F27: Gibt den Status der Health-Auto-Restart-Ueberwachung zurueck."""
    data = await asyncio.to_thread(_read_status_file, MONITOR_DATA_DIR / "health_auto_restart.json")
    if data is None:
        return JSONResponse(content={"status": "unknown", "message": "Keine Daten"}, status_code=200)
    return JSONResponse(content=data)


# ------------------------------------------------------------------
# F49: Disk Guard Status
# ------------------------------------------------------------------

@router.get("/api/health/disk")
async def health_disk() -> JSONResponse:
    """F49: Gibt den aktuellen Festplatten-Status zurueck."""
    data = await asyncio.to_thread(_read_status_file, MONITOR_DATA_DIR / "disk_guard.json")
    if data is None:
        return JSONResponse(content={"status": "unknown", "message": "Keine Daten"}, status_code=200)
    return JSONResponse(content=data)


# ------------------------------------------------------------------
# F50: Service Watchdog Status
# ------------------------------------------------------------------

@router.get("/api/health/services")
async def health_services() -> JSONResponse:
    """F50: Gibt den Status der ueberwachten Services zurueck."""
    data = await asyncio.to_thread(_read_status_file, MONITOR_DATA_DIR / "service_watchdog.json")
    if data is None:
        return JSONResponse(content={"status": "unknown", "message": "Keine Daten"}, status_code=200)
    return JSONResponse(content=data)


# ------------------------------------------------------------------
# F51: DuckDNS Monitor Status
# ------------------------------------------------------------------

@router.get("/api/health/dns")
async def health_dns() -> JSONResponse:
    """F51: Gibt den DuckDNS DNS-Check-Status zurueck."""
    data = await asyncio.to_thread(_read_status_file, MONITOR_DATA_DIR / "duckdns_monitor.json")
    if data is None:
        return JSONResponse(content={"status": "unknown", "message": "Keine Daten"}, status_code=200)
    return JSONResponse(content=data)


# ------------------------------------------------------------------
# F52: Port Monitor Status
# ------------------------------------------------------------------

@router.get("/api/health/ports")
async def health_ports() -> JSONResponse:
    """F52: Gibt den Port-Monitor-Status zurueck."""
    data = await asyncio.to_thread(_read_status_file, MONITOR_DATA_DIR / "port_monitor.json")
    if data is None:
        return JSONResponse(content={"status": "unknown", "message": "Keine Daten"}, status_code=200)
    return JSONResponse(content=data)
