"""
F34: Health Route — Oeffentlicher Health-Check Endpunkt.

Stellt GET /api/health und GET /api/health/selftest bereit.
Kein Auth erforderlich (Rate-Limiting kommt in spaeterer Phase).
Liest Status-JSON-Dateien aus data/monitor/ und data/gameserver/.

Zugriff (Stand 2026-08-14, nach Sicherheits-Review W-11/W-12):
  * `GET /api/health` bleibt oeffentlich — der schlanke Statusendpunkt fuer
    externe Uptime-Pruefer (Uptime Robot, Healthchecks.io).
  * Alle uebrigen Endpunkte verlangen eine Anmeldung. Sie geben RCON-Ports,
    Plattenkapazitaet, Dienstnamen und absolute Pfade aus — das ist eine
    Landkarte des Servers, kein Statussignal.
"""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from web.auth import require_auth_api
from utils.logger import get_logger
from utils.config import get_config, get_env, PROJECT_ROOT
from modules.server_registry import alle as alle_server
from utils.config import MONITOR_DATA_DIR, GAMESERVER_DATA_DIR, ADMIN_DATA_DIR

logger = get_logger("web.routes.health")

router = APIRouter(tags=["Health"])
# allow_anon: bewusst public — Monitoring-Endpunkte fuer externe Health-Checks

# Bekannte Server-Status-Dateien die geprueft werden. Aus der ENV
# (modules.server_registry) — ein stillgelegter Server soll die Gesamtlage nicht
# dauerhaft auf "degraded" ziehen, nur weil seine Datei nicht mehr frisch wird.
SERVER_STATUS_FILES: list[dict] = [
    {
        "id": srv.kennung,
        "name": srv.label,
        "file": MONITOR_DATA_DIR / f"{srv.kennung}_status.json",
    }
    for srv in alle_server()
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


# Zustaende, die als Ausfall zaehlen. `disabled` steht bewusst NICHT drin.
OFFLINE_ZUSTAENDE: frozenset[str] = frozenset({"offline", "error", "crashed"})


def _absichtlich_gestoppt(server_id: str) -> bool:
    """Wurde dieser Server von Hand stillgelegt?

    Quelle ist `manual_stop_state` — dieselbe Datei, an der sich auch der
    Auto-Neustart und der taegliche Neustart orientieren. Faellt sie aus,
    gilt der Server als nicht stillgelegt: lieber eine Warnung zu viel.
    """
    try:
        from modules.monitoring.manual_stop_state import is_manually_stopped
        return bool(is_manually_stopped(server_id))
    except Exception as e:  # noqa: BLE001
        logger.debug("Manuell-gestoppt-Pruefung fehlgeschlagen: %s", e)
        return False


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

    # Ein absichtlich stillgelegter Server ist keine Stoerung (Marco-Entscheid
    # 2026-08-18: BMC laeuft nicht, weil ihn niemand bespielt). Bis dahin zog
    # er die Gesamtanzeige dauerhaft auf `degraded` — eine Warnlampe, die
    # immer leuchtet, wird nicht mehr gelesen.
    if raw_status in OFFLINE_ZUSTAENDE and _absichtlich_gestoppt(config["id"]):
        raw_status = "disabled"
        entry["manually_stopped"] = True

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

    # Stillgelegte Server zaehlen nicht mit — weder als Stoerung noch als
    # Beleg dafuer, dass alles laeuft.
    aktive = [s for s in servers if s.get("status") != "disabled"]
    if not aktive:
        return "ok"

    statuses = [s.get("status", "unknown") for s in aktive]

    # Alle offline oder error = error
    if all(s in OFFLINE_ZUSTAENDE for s in statuses):
        return "error"

    # Mindestens ein Server offline = degraded
    if any(s in OFFLINE_ZUSTAENDE for s in statuses):
        return "degraded"

    # Alle veraltet = degraded
    if all(s.get("stale", True) for s in aktive):
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
    operator_bot_status, recon_bot_status = bot_data_list

    # Gesamtstatus bestimmen
    overall_status = _determine_overall_status(servers)

    response: dict = {
        "status": overall_status,
        "servers": servers,
        "bots": {
            "gameserver": operator_bot_status.get("status", "unknown") if operator_bot_status else "unknown",
            "monitor": recon_bot_status.get("status", "unknown") if recon_bot_status else "unknown",
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # HTTP-Status: 200 wenn OK, 503 wenn error
    status_code = 200 if overall_status != "error" else 503

    logger.debug("Health-Check: %s (%d Server)", overall_status, len(servers))

    return JSONResponse(content=response, status_code=status_code)


@router.get("/api/health/selftest", dependencies=[Depends(require_auth_api)])
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

@router.get("/api/health/auto-restart", dependencies=[Depends(require_auth_api)])
async def health_auto_restart() -> JSONResponse:
    """F27: Gibt den Status der Health-Auto-Restart-Ueberwachung zurueck."""
    data = await asyncio.to_thread(_read_status_file, MONITOR_DATA_DIR / "health_auto_restart.json")
    if data is None:
        return JSONResponse(content={"status": "unknown", "message": "Keine Daten"}, status_code=200)
    return JSONResponse(content=data)


# ------------------------------------------------------------------
# F49: Disk Guard Status
# ------------------------------------------------------------------

@router.get("/api/health/disk", dependencies=[Depends(require_auth_api)])
async def health_disk() -> JSONResponse:
    """F49: Gibt den aktuellen Festplatten-Status zurueck."""
    data = await asyncio.to_thread(_read_status_file, MONITOR_DATA_DIR / "disk_guard.json")
    if data is None:
        return JSONResponse(content={"status": "unknown", "message": "Keine Daten"}, status_code=200)
    return JSONResponse(content=data)


# ------------------------------------------------------------------
# F50: Service Watchdog Status
# ------------------------------------------------------------------

@router.get("/api/health/services", dependencies=[Depends(require_auth_api)])
async def health_services() -> JSONResponse:
    """F50: Gibt den Status der ueberwachten Services zurueck."""
    data = await asyncio.to_thread(_read_status_file, MONITOR_DATA_DIR / "service_watchdog.json")
    if data is None:
        return JSONResponse(content={"status": "unknown", "message": "Keine Daten"}, status_code=200)
    return JSONResponse(content=data)


# ------------------------------------------------------------------
# F51: DuckDNS Monitor Status
# ------------------------------------------------------------------

@router.get("/api/health/dns", dependencies=[Depends(require_auth_api)])
async def health_dns() -> JSONResponse:
    """F51: Gibt den DuckDNS DNS-Check-Status zurueck."""
    data = await asyncio.to_thread(_read_status_file, MONITOR_DATA_DIR / "duckdns_monitor.json")
    if data is None:
        return JSONResponse(content={"status": "unknown", "message": "Keine Daten"}, status_code=200)
    return JSONResponse(content=data)


# ------------------------------------------------------------------
# F52: Port Monitor Status
# ------------------------------------------------------------------

@router.get("/api/health/ports", dependencies=[Depends(require_auth_api)])
async def health_ports() -> JSONResponse:
    """F52: Gibt den Port-Monitor-Status zurueck."""
    data = await asyncio.to_thread(_read_status_file, MONITOR_DATA_DIR / "port_monitor.json")
    if data is None:
        return JSONResponse(content={"status": "unknown", "message": "Keine Daten"}, status_code=200)
    return JSONResponse(content=data)
