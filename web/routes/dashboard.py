"""
Phase 13a: Dashboard-Uebersicht — Hauptseite des Web-Dashboards

Zeigt Server-Status, Bot-Status, System-Performance und
aktuelle Ereignisse in einer Kacheluebersicht an.
"""

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from web.templates_setup import erstelle_templates

from utils.config import PROJECT_ROOT, DATA_DIR, MONITOR_DATA_DIR, get_env
from utils.logger import get_logger
from web.auth import require_auth, require_auth_api, require_perm
from web.dashboard_feed import BOT_ANZEIGE, bot_eintrag
from modules.database.db_manager import get_db, get_read_db

EVENTS_FILE = MONITOR_DATA_DIR / "events.json"

logger = get_logger("web.routes.dashboard")

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
templates = erstelle_templates(TEMPLATE_DIR)

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
    Sammelt Server-Status-Daten aus data/monitor/*_status.json Dateien.
    Gibt eine Liste von Server-Status-Dicts zurueck.

    Liest NUR Dateien die auf _status.json enden — ignoriert
    stats_history.json, events.json, *_players.json usw.
    """
    servers = []
    monitor_dir = MONITOR_DATA_DIR

    if not monitor_dir.exists():
        logger.debug(f"Monitor-Verzeichnis nicht gefunden: {monitor_dir}")
        return servers

    # Nur *_status.json Dateien lesen (vom StatusWriter erzeugt).
    # Nicht-Server-Monitore ausschliessen, sonst tauchen sie faelschlich als
    # Server-Karte mit totem /server/<id>-Details-Link auf:
    #   bot_status.json -> wird unter Bot-Status angezeigt
    #   ssl_status.json -> SSL-Zertifikats-Monitor, gehoert auf /security
    _non_server = {"bot_status.json", "ssl_status.json"}

    # Nur Server, die die Registry kennt. Ein stillgelegter Server hinterlaesst
    # seine letzte Statusdatei (die Daten bleiben bewusst liegen) — ohne diesen
    # Abgleich stand er weiter als Kachel im Dashboard, mit eingefrorenen Werten
    # und einem Detail-Link, der 404 liefert. Genau so war es am 2026-08-15 mit
    # mc_vanilla_status.json vom Vortag.
    try:
        from modules.server_registry import alle as _alle_server
        _bekannt = {srv.kennung for srv in _alle_server()}
    except Exception as e:  # noqa: BLE001 — lieber alle zeigen als keine
        logger.debug(f"Registry nicht lesbar, zeige alle Statusdateien: {e}")
        _bekannt = None

    for json_file in sorted(monitor_dir.glob("*_status.json")):
        if json_file.name in _non_server:
            continue
        if _bekannt is not None and json_file.stem.replace("_status", "") not in _bekannt:
            logger.debug(f"Statusdatei ohne Server in der Registry uebersprungen: {json_file.name}")
            continue
        data = _load_json_safe(json_file)
        if data:
            # Server-ID aus Dateiname: satisfactory_status.json → satisfactory
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
        "cpu_cores": 0,
        "ram_percent": 0,
        "ram_used_gb": 0,
        "ram_total_gb": 0,
        "disk_percent": 0,
        "disk_used_gb": 0,
        "disk_total_gb": 0,
    }
    try:
        import psutil
        # interval=None gibt den CPU%-Delta seit letztem Call zurueck (kein Block).
        # Erster Call liefert 0.0; spaetere Calls liefern den realen Wert.
        # Vermeidet 500ms Event-Loop-Block in async Hot-Path.
        stats["cpu_percent"] = psutil.cpu_percent(interval=None)
        stats["cpu_cores"] = psutil.cpu_count() or 0

        mem = psutil.virtual_memory()
        stats["ram_percent"] = mem.percent
        stats["ram_used_gb"] = round(mem.used / (1024 ** 3), 1)
        stats["ram_total_gb"] = round(mem.total / (1024 ** 3), 1)

        # Gleiche Partition wie der Live-Sammler (dashboard_feed), sonst zeigt
        # der erste Render eine andere Platte als der Push fuenf Sekunden spaeter.
        disk = psutil.disk_usage(get_env("DASHBOARD_DISK_PATH", "/"))
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

    Namen und Frische-Pruefung kommen aus web/dashboard_feed.py — dieselbe
    Kachel wird beim Seitenaufbau hier und danach per WebSocket dort erzeugt.
    Zwei Kopien der Liste haben schon einmal auseinandergedriftet: hier standen
    noch die Bot-Namen von vor der Umbenennung, und pipeline-bot fehlte.
    """
    return [
        bot_eintrag(bot_id, name, _load_json_safe(DATA_DIR / bot_id / "bot_status.json"))
        for bot_id, name in BOT_ANZEIGE.items()
    ]


def _collect_recent_events_json() -> list[dict]:
    """
    Fallback: Sammelt die letzten Ereignisse aus Event-Log-Dateien (JSON).
    """
    events = []
    event_file = DATA_DIR / "monitor" / "events.json"
    data = _load_json_safe(event_file)

    if isinstance(data, list):
        events = data[-20:]  # Letzte 20 Ereignisse
    elif isinstance(data, dict) and "events" in data:
        events = data["events"][-20:]

    return events


async def _collect_recent_events_db() -> list[dict]:
    """
    Sammelt die letzten 20 Ereignisse aus der SQLite-Datenbank.
    Faellt auf JSON-Datei zurueck wenn die DB nicht erreichbar ist.
    """
    try:
        db = await get_read_db()
        # Filter '0 Updates verfuegbar'-Noise vom Daily-Cron (sind keine echten Events).
        cursor = await db.execute(
            "SELECT timestamp, event_type, category, server_id, message, details "
            "FROM events "
            "WHERE NOT (event_type = 'package_check' AND message LIKE '0 Updates%') "
            "ORDER BY timestamp DESC LIMIT 20"
        )
        rows = await cursor.fetchall()
        events = []
        for row in rows:
            events.append({
                "timestamp": row["timestamp"],
                "event_type": row["event_type"],
                "category": row["category"],
                "server_id": row["server_id"],
                "message": row["message"],
                "details": row["details"],
            })
        return events
    except Exception as e:
        logger.warning(f"DB-Abfrage fuer Events fehlgeschlagen, Fallback auf JSON: {e}")
        return _collect_recent_events_json()


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_overview(request: Request, current_user: dict = Depends(require_auth)):
    """
    Server/System-Übersicht (D9: von `/` nach `/dashboard` verschoben — `/` ist
    jetzt die öffentliche Landing-Page). Zeigt alle Server, Bots und System-Stats.
    """
    # File-I/O-Collector parallel via to_thread → kein Event-Loop-Block.
    # Bei 3-5 _status.json-Files spart das ~10-50ms pro Request gegenueber sequentiell-sync.
    servers, system_stats, bot_status, recent_events = await asyncio.gather(
        asyncio.to_thread(_collect_server_status),
        asyncio.to_thread(_collect_system_stats),
        asyncio.to_thread(_collect_bot_status),
        _collect_recent_events_db(),
    )

    return templates.TemplateResponse(request, "dashboard.html", {
        "request": request,
        "user": current_user,
        "servers": servers,
        "system": system_stats,
        "bots": bot_status,
        "events": recent_events,
    })


@router.post("/api/events/clear", response_class=HTMLResponse)
async def clear_events(
    # Wie /api/errors/clear: destruktiv, deshalb Systemrecht statt "angemeldet".
    current_user: dict = Depends(require_perm("system", "control")),
):
    """Loescht alle Ereignisse aus dem Event-Log."""
    user = current_user
    try:
        # SQLite: Alle Events loeschen
        try:
            db = await get_db()
            await db.execute("DELETE FROM events")
            await db.commit()
            logger.info("Events aus SQLite geloescht")
        except Exception as db_err:
            logger.warning(f"SQLite DELETE fehlgeschlagen: {db_err}")

        # JSON: Ebenfalls leeren (Rueckwaertskompatibilitaet).
        # M49-Fix: blockierendes File-IO in einen Thread auslagern (nicht im Event-Loop).
        def _write_empty_events() -> None:
            EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(EVENTS_FILE, "w", encoding="utf-8") as f:
                json.dump({"events": [], "last_update": ""}, f)

        await asyncio.to_thread(_write_empty_events)

        logger.info(f"Ereignis-Log geleert von {user.get('username', 'Unbekannt')}")
        return HTMLResponse(
            '<div class="alert alert-success" style="margin-bottom: 0.5rem;">Ereignis-Log geleert.</div>'
            '<p class="empty-text">Keine Ereignisse vorhanden.</p>'
        )
    except Exception as e:
        logger.error(f"Fehler beim Leeren des Event-Logs: {e}")
        return HTMLResponse('<div class="alert alert-danger">Fehler beim Leeren des Event-Logs.</div>')
