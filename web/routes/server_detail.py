"""
Phase 13b: Server-Detailseite — Einzelansicht pro Game-Server

Zeigt detaillierte Informationen zu einem Server:
Spielerliste, RCON-Console, Backups, World-Info, Server-Steuerung.
"""

import json
import os
from pathlib import Path
from datetime import datetime

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from utils.config import PROJECT_ROOT, DATA_DIR, MONITOR_DATA_DIR, get_config
from utils.logger import get_logger
from web.auth import get_current_user

logger = get_logger("web.routes.server_detail")

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter(tags=["Server-Detail"])

# Gueltige Server-IDs und deren Anzeigenamen
VALID_SERVER_IDS = {
    "satisfactory": "Satisfactory",
    "mc_bmc": "Minecraft BMC",
    "mc_vanilla": "Minecraft Vanilla",
    "teamspeak": "TeamSpeak",
}

# Server-Typ-Zuordnung (fuer Feature-Flags wie RCON)
SERVER_TYPES = {
    "satisfactory": "satisfactory",
    "mc_bmc": "minecraft",
    "mc_vanilla": "minecraft",
    "teamspeak": "teamspeak",
}

# Backup-Verzeichnis (unterhalb von data/)
BACKUP_BASE_DIR = DATA_DIR / "backups"


def _load_json_safe(filepath: Path) -> dict:
    """Laedt eine JSON-Datei sicher. Gibt leeres Dict bei Fehler zurueck."""
    try:
        if filepath.exists():
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Konnte {filepath} nicht laden: {e}")
    return {}


def _get_server_display_name(server_id: str) -> str:
    """Gibt den Anzeigenamen fuer eine Server-ID zurueck."""
    return VALID_SERVER_IDS.get(server_id, server_id)


def _get_server_type(server_id: str) -> str:
    """Gibt den Server-Typ zurueck (minecraft, satisfactory, teamspeak)."""
    return SERVER_TYPES.get(server_id, "unknown")


def _list_backups(server_id: str) -> list[dict]:
    """
    Listet Backup-Dateien fuer einen Server mit Metadaten auf.

    Durchsucht das Verzeichnis data/backups/{server_id}/ nach Dateien
    und gibt eine Liste mit Name, Groesse und Datum zurueck.
    """
    backups = []
    backup_dir = BACKUP_BASE_DIR / server_id

    if not backup_dir.exists():
        logger.debug(f"Backup-Verzeichnis nicht gefunden: {backup_dir}")
        return backups

    try:
        for entry in sorted(backup_dir.iterdir(), reverse=True):
            if entry.is_file():
                stat = entry.stat()
                size_mb = round(stat.st_size / (1024 * 1024), 2)
                modified = datetime.fromtimestamp(stat.st_mtime)

                backups.append({
                    "filename": entry.name,
                    "size_mb": size_mb,
                    "size_display": f"{size_mb} MB" if size_mb < 1024 else f"{round(size_mb / 1024, 2)} GB",
                    "date": modified.strftime("%Y-%m-%d %H:%M:%S"),
                    "timestamp": stat.st_mtime,
                })
    except OSError as e:
        logger.warning(f"Fehler beim Lesen der Backups fuer {server_id}: {e}")

    return backups


def _get_server_info(server_id: str) -> dict:
    """
    Sammelt alle verfuegbaren Informationen zu einem Server.

    Liest Status- und Spielerdaten aus den Monitor-JSON-Dateien,
    listet Backups auf und fuegt Konfigurationsdaten hinzu.
    """
    display_name = _get_server_display_name(server_id)
    server_type = _get_server_type(server_id)

    # Status-Daten aus data/monitor/{server_id}_status.json laden
    status_file = MONITOR_DATA_DIR / f"{server_id}_status.json"
    status_data = _load_json_safe(status_file)

    # Spieler-Daten aus data/monitor/{server_id}_players.json laden
    players_file = MONITOR_DATA_DIR / f"{server_id}_players.json"
    players_data = _load_json_safe(players_file)
    player_list = players_data.get("players", [])

    # Backup-Liste erstellen
    backups = _list_backups(server_id)

    # Konfiguration laden
    config = get_config()

    # Server-spezifische Konfigurationseintraege zusammenstellen
    server_config = {}
    if server_type == "minecraft":
        server_config = {
            "Auto-Backup": config.get("features", {}).get("auto_backup", False),
            "Auto-Update": config.get("features", {}).get("auto_update", False),
            "Taglicher Neustart": config.get("restart", {}).get("daily_time", "N/A"),
            "Health-Check Intervall": f"{config.get('intervals', {}).get('health_check_seconds', 120)}s",
            "Backup-Intervall": f"{config.get('intervals', {}).get('auto_backup_seconds', 21600)}s",
            "Max. lokale Backups": config.get("backup", {}).get("max_local", 20),
        }
    elif server_type == "satisfactory":
        server_config = {
            "Auto-Backup": config.get("features", {}).get("auto_backup", False),
            "Auto-Update": config.get("features", {}).get("auto_update", False),
            "Taglicher Neustart": config.get("restart", {}).get("daily_time", "N/A"),
            "Health-Check Intervall": f"{config.get('intervals', {}).get('health_check_seconds', 120)}s",
        }
    elif server_type == "teamspeak":
        server_config = {
            "Health-Check Intervall": f"{config.get('intervals', {}).get('health_check_seconds', 120)}s",
        }

    # World/Savegame-Info aus Status-Daten extrahieren
    world_info = status_data.get("world_info", {})
    if not world_info:
        # Fallback: Standardwerte basierend auf Server-Typ
        if server_type == "minecraft":
            world_info = {
                "Seed": status_data.get("seed", "N/A"),
                "Weltalter": status_data.get("world_age", "N/A"),
                "Weltgroesse": status_data.get("world_size", "N/A"),
                "Schwierigkeit": status_data.get("difficulty", "N/A"),
                "Spielmodus": status_data.get("gamemode", "N/A"),
            }
        elif server_type == "satisfactory":
            world_info = {
                "Session-Name": status_data.get("session_name", "N/A"),
                "Spielstand": status_data.get("save_name", "N/A"),
                "Spielzeit": status_data.get("play_time", "N/A"),
                "Tier": status_data.get("tier", "N/A"),
            }

    # Update-Informationen
    update_info = {
        "current_version": status_data.get("version", "N/A"),
        "available_version": status_data.get("available_version", "N/A"),
        "update_available": status_data.get("update_available", False),
    }

    return {
        "server_id": server_id,
        "name": display_name,
        "type": server_type,
        "status": status_data.get("status", "unknown"),
        "players_online": status_data.get("players", 0),
        "max_players": status_data.get("max_players", 0),
        "uptime": status_data.get("uptime", "N/A"),
        "address": status_data.get("address", "N/A"),
        "port": status_data.get("port", "N/A"),
        "player_list": player_list,
        "backups": backups,
        "server_config": server_config,
        "world_info": world_info,
        "update_info": update_info,
        "has_rcon": server_type == "minecraft",
    }


# --- Routen ---


@router.get("/server/{server_id}", response_class=HTMLResponse)
async def server_detail_page(request: Request, server_id: str):
    """
    Hauptseite der Server-Detailansicht.

    Zeigt alle Informationen zu einem bestimmten Game-Server an,
    inklusive Tabs fuer Spieler, Backups, RCON, World-Info und Config.
    """
    user = get_current_user(request)
    if user is None:
        return RedirectResponse(url="/auth/login", status_code=302)

    # Ungueltige Server-ID abfangen
    if server_id not in VALID_SERVER_IDS:
        logger.warning(f"Unbekannte Server-ID angefragt: {server_id}")
        return RedirectResponse(url="/", status_code=302)

    server = _get_server_info(server_id)

    return templates.TemplateResponse("server_detail.html", {
        "request": request,
        "user": user,
        "server": server,
    })


@router.get("/api/server/{server_id}/players", response_class=HTMLResponse)
async def server_players_partial(request: Request, server_id: str):
    """
    HTMX-Partial: Gibt die aktuelle Spielerliste als HTML-Fragment zurueck.

    Wird automatisch alle 10 Sekunden per hx-trigger aktualisiert.
    """
    user = get_current_user(request)
    if user is None:
        return HTMLResponse(content="<p>Nicht angemeldet</p>", status_code=401)

    if server_id not in VALID_SERVER_IDS:
        return HTMLResponse(content="<p>Unbekannter Server</p>", status_code=404)

    # Spieler-Daten laden
    players_file = MONITOR_DATA_DIR / f"{server_id}_players.json"
    players_data = _load_json_safe(players_file)
    player_list = players_data.get("players", [])

    return templates.TemplateResponse("partials/server_players.html", {
        "request": request,
        "players": player_list,
        "server_id": server_id,
    })


@router.get("/api/server/{server_id}/backups", response_class=HTMLResponse)
async def server_backups_partial(request: Request, server_id: str):
    """
    HTMX-Partial: Gibt die Backup-Liste als HTML-Fragment zurueck.
    """
    user = get_current_user(request)
    if user is None:
        return HTMLResponse(content="<p>Nicht angemeldet</p>", status_code=401)

    if server_id not in VALID_SERVER_IDS:
        return HTMLResponse(content="<p>Unbekannter Server</p>", status_code=404)

    backups = _list_backups(server_id)

    return templates.TemplateResponse("partials/server_backups.html", {
        "request": request,
        "backups": backups,
        "server_id": server_id,
    })


@router.post("/api/server/{server_id}/action", response_class=HTMLResponse)
async def server_action(request: Request, server_id: str, action: str = Form("")):
    """
    Server-Steuerung: Start, Stop, Restart.

    Aktuell ein Platzhalter — die eigentliche Integration erfolgt
    wenn die Bots neben dem Dashboard laufen. Gibt eine
    Statusmeldung als HTMX-Fragment zurueck.
    """
    user = get_current_user(request)
    if user is None:
        return HTMLResponse(content="<p>Nicht angemeldet</p>", status_code=401)

    if server_id not in VALID_SERVER_IDS:
        return HTMLResponse(content="<p>Unbekannter Server</p>", status_code=404)

    valid_actions = ("start", "stop", "restart", "maintenance")
    if action not in valid_actions:
        return HTMLResponse(
            content='<div class="alert alert-danger">Ungueltige Aktion</div>',
            status_code=400,
        )

    display_name = _get_server_display_name(server_id)
    action_names = {
        "start": "Starten",
        "stop": "Stoppen",
        "restart": "Neustart",
        "maintenance": "Wartungsmodus",
    }
    action_display = action_names.get(action, action)

    logger.info(f"Server-Aktion angefragt: {action} fuer {display_name} von Benutzer {user.get('username', 'Unbekannt')}")

    # Platzhalter-Antwort — tatsaechliche Integration folgt spaeter
    html = f"""
    <div class="alert alert-warning">
        <strong>Feature in Entwicklung:</strong>
        Die Aktion &laquo;{action_display}&raquo; fuer <strong>{display_name}</strong>
        wurde empfangen, aber die Server-Steuerung ist noch nicht mit dem Dashboard verbunden.
        Diese Funktion wird aktiviert, sobald die Bots neben dem Dashboard laufen.
    </div>
    """
    return HTMLResponse(content=html)


@router.get("/api/server/{server_id}/mods", response_class=HTMLResponse)
async def server_mods_partial(request: Request, server_id: str):
    """
    HTMX-Partial: Gibt die Mod-Liste als HTML-Fragment zurueck.

    Liest die installierte Mod-Liste aus der jeweiligen JSON-Datei
    des ModManagers und stellt sie als Tabelle dar.
    """
    user = get_current_user(request)
    if user is None:
        return HTMLResponse(content="<p>Nicht angemeldet</p>", status_code=401)

    if server_id not in VALID_SERVER_IDS:
        return HTMLResponse(content="<p>Unbekannter Server</p>", status_code=404)

    # Mod-Liste aus der JSON-Datei laden
    server_type = _get_server_type(server_id)
    mods: list[dict] = []

    # Mod-Dateien nach Server-Typ suchen
    mod_files = {
        "minecraft": ["minecraft_mods.json"],
        "satisfactory": ["satisfactory_mods.json"],
    }
    for mod_file_name in mod_files.get(server_type, []):
        mod_file = DATA_DIR / mod_file_name
        if mod_file.exists():
            try:
                with open(mod_file, "r", encoding="utf-8") as f:
                    mod_data = json.load(f)
                if isinstance(mod_data, list):
                    mods = mod_data
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Konnte Mod-Datei nicht laden: {e}")

    return templates.TemplateResponse("partials/server_mods.html", {
        "request": request,
        "mods": mods,
        "server_id": server_id,
    })


@router.get("/api/server/{server_id}/mods/export")
async def server_mods_export(request: Request, server_id: str):
    """
    Exportiert die installierte Mod-Liste als JSON-Download.
    """
    from fastapi.responses import JSONResponse

    user = get_current_user(request)
    if user is None:
        return JSONResponse(content={"error": "Nicht angemeldet"}, status_code=401)

    if server_id not in VALID_SERVER_IDS:
        return JSONResponse(content={"error": "Unbekannter Server"}, status_code=404)

    server_type = _get_server_type(server_id)
    mods: list[dict] = []

    mod_files = {
        "minecraft": ["minecraft_mods.json"],
        "satisfactory": ["satisfactory_mods.json"],
    }
    for mod_file_name in mod_files.get(server_type, []):
        mod_file = DATA_DIR / mod_file_name
        if mod_file.exists():
            try:
                with open(mod_file, "r", encoding="utf-8") as f:
                    mods = json.load(f)
            except (json.JSONDecodeError, IOError):
                pass

    return JSONResponse(
        content={"server_id": server_id, "server_type": server_type, "mods": mods},
        headers={"Content-Disposition": f'attachment; filename="mods_{server_id}.json"'},
    )


@router.post("/api/server/{server_id}/mods/search", response_class=HTMLResponse)
async def server_mods_search(request: Request, server_id: str):
    """
    Mod-Suche — Platzhalter-Endpunkt fuer Mod-Suche ueber Modrinth/CurseForge.

    Die tatsaechliche API-Integration erfolgt wenn die Bots neben dem
    Dashboard laufen und der ModManager direkt angesprochen werden kann.
    """
    user = get_current_user(request)
    if user is None:
        return HTMLResponse(content="<p>Nicht angemeldet</p>", status_code=401)

    form = await request.form()
    query = form.get("query", "").strip()
    source = form.get("source", "modrinth")

    if not query:
        return HTMLResponse(content='<div class="alert alert-danger">Bitte Suchbegriff eingeben.</div>')

    logger.info(f"Mod-Suche: '{query}' auf {source} (von {user.get('username', 'Unbekannt')})")

    html = f"""
    <div class="alert alert-warning">
        <strong>Feature in Entwicklung:</strong>
        Suche nach &laquo;{query}&raquo; auf {source} empfangen.
        Die Mod-Suche wird aktiviert, sobald die Bots neben dem Dashboard laufen
        und der ModManager direkt angesprochen werden kann.
    </div>
    """
    return HTMLResponse(content=html)


@router.post("/api/server/{server_id}/mods/check-updates", response_class=HTMLResponse)
async def server_mods_check_updates(request: Request, server_id: str):
    """Prueft auf verfuegbare Mod-Updates — Platzhalter."""
    user = get_current_user(request)
    if user is None:
        return HTMLResponse(content="<p>Nicht angemeldet</p>", status_code=401)

    logger.info(f"Mod-Update-Check fuer {server_id} (von {user.get('username', 'Unbekannt')})")
    return HTMLResponse(content="""
    <div class="alert alert-warning">
        <strong>Feature in Entwicklung:</strong>
        Der Update-Check wird aktiviert, sobald die Bots neben dem Dashboard laufen.
    </div>
    """)


@router.post("/api/server/{server_id}/mods/update", response_class=HTMLResponse)
async def server_mod_update(request: Request, server_id: str):
    """Aktualisiert einen einzelnen Mod — Platzhalter."""
    user = get_current_user(request)
    if user is None:
        return HTMLResponse(content="<p>Nicht angemeldet</p>", status_code=401)

    form = await request.form()
    mod_name = form.get("mod_name", "")
    logger.info(f"Mod-Update: '{mod_name}' fuer {server_id} (von {user.get('username', 'Unbekannt')})")
    return HTMLResponse(content=f"""
    <div class="alert alert-warning">
        <strong>Feature in Entwicklung:</strong>
        Update fuer &laquo;{mod_name}&raquo; empfangen.
    </div>
    """)


@router.post("/api/server/{server_id}/mods/uninstall", response_class=HTMLResponse)
async def server_mod_uninstall(request: Request, server_id: str):
    """Deinstalliert einen Mod — Platzhalter."""
    user = get_current_user(request)
    if user is None:
        return HTMLResponse(content="<p>Nicht angemeldet</p>", status_code=401)

    form = await request.form()
    mod_name = form.get("mod_name", "")
    logger.info(f"Mod-Deinstallation: '{mod_name}' fuer {server_id} (von {user.get('username', 'Unbekannt')})")
    return HTMLResponse(content=f"""
    <div class="alert alert-warning">
        <strong>Feature in Entwicklung:</strong>
        Deinstallation von &laquo;{mod_name}&raquo; empfangen.
    </div>
    """)


@router.post("/api/server/{server_id}/rcon", response_class=HTMLResponse)
async def server_rcon(request: Request, server_id: str, command: str = Form("")):
    """
    RCON-Befehl senden (nur Minecraft-Server).

    Aktuell ein Platzhalter — akzeptiert den Befehl und gibt
    eine simulierte Antwort zurueck.
    """
    user = get_current_user(request)
    if user is None:
        return HTMLResponse(content="<p>Nicht angemeldet</p>", status_code=401)

    if server_id not in VALID_SERVER_IDS:
        return HTMLResponse(content="<p>Unbekannter Server</p>", status_code=404)

    server_type = _get_server_type(server_id)
    if server_type != "minecraft":
        return HTMLResponse(
            content='<div class="alert alert-danger">RCON ist nur fuer Minecraft-Server verfuegbar.</div>',
            status_code=400,
        )

    if not command.strip():
        return HTMLResponse(
            content='<div class="rcon-line rcon-error">&gt; Bitte einen Befehl eingeben.</div>',
        )

    display_name = _get_server_display_name(server_id)
    logger.info(f"RCON-Befehl fuer {display_name}: {command} (von {user.get('username', 'Unbekannt')})")

    # Platzhalter-Antwort — tatsaechliche RCON-Integration folgt spaeter
    timestamp = datetime.now().strftime("%H:%M:%S")
    html = f"""
    <div class="rcon-line">
        <span class="rcon-timestamp">[{timestamp}]</span>
        <span class="rcon-input">&gt; {command}</span>
    </div>
    <div class="rcon-line rcon-response">
        <span class="rcon-timestamp">[{timestamp}]</span>
        <span class="rcon-output">[Feature in Entwicklung] Befehl empfangen: &laquo;{command}&raquo; — RCON-Verbindung wird aktiviert, sobald die Bots neben dem Dashboard laufen.</span>
    </div>
    """
    return HTMLResponse(content=html)
