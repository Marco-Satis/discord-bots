"""
Phase 13b: Server-Detailseite — Einzelansicht pro Game-Server

Zeigt detaillierte Informationen zu einem Server:
Spielerliste, RCON-Console, Backups, World-Info, Server-Steuerung.
"""

import asyncio
import html as html_escape_module
import json
from pathlib import Path
from datetime import datetime

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from utils.config import PROJECT_ROOT, DATA_DIR, MONITOR_DATA_DIR, get_config, get_env
from utils.logger import get_logger
from web.auth import get_current_user
from modules.minecraft.rcon import MinecraftRCON

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

# Server-ID → systemd Service-Name Zuordnung
SERVICE_NAMES = {
    "satisfactory": "satisfactory.service",
    "mc_bmc": "minecraft-bmc.service",
    "mc_vanilla": "minecraft-vanilla.service",
    "teamspeak": "teamspeak.service",
}


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
    features = config.get("features", {})
    scheduler = config.get("scheduler", {})
    thresholds = config.get("thresholds", {})

    restart_str = "Deaktiviert"
    if scheduler.get("daily_restart_enabled"):
        restart_str = f"{scheduler.get('daily_restart_hour', 4)}:{scheduler.get('daily_restart_minute', 0):02d} Uhr"

    server_config = {}
    if server_type == "minecraft":
        server_config = {
            "Service-Name": status_data.get("service_name", "N/A"),
            "Server-Version": status_data.get("version", "N/A"),
            "Port": status_data.get("port", "N/A"),
            "Max. Spieler": status_data.get("max_players", 20),
            "Auto-Backup": features.get("auto_backup", False),
            "Auto-Update": features.get("auto_update", False),
            "Taeglicher Neustart": restart_str,
            "Backup-Intervall": f"{scheduler.get('auto_backup_interval_hours', 6)} Stunden",
            "Max. lokale Backups": scheduler.get("max_local_backups", 20),
            "Max. Cloud-Backups": scheduler.get("max_cloud_backups", 10),
            "Spieler-Tracking": features.get("player_tracking", False),
            "Chat-Bridge": features.get("chat_bridge", False),
            "Savegame-Schutz": features.get("savegame_protection", False),
            "Status-Embed": features.get("status_embed", False),
        }
    elif server_type == "satisfactory":
        server_config = {
            "API erreichbar": status_data.get("api_reachable", "N/A"),
            "Prozess laeuft": status_data.get("process_running", "N/A"),
            "PID": status_data.get("pid", "N/A"),
            "Max. Spieler": status_data.get("max_players", 0),
            "Auto-Backup": features.get("auto_backup", False),
            "Auto-Update": features.get("auto_update", False),
            "Taeglicher Neustart": restart_str,
            "Backup-Intervall": f"{scheduler.get('auto_backup_interval_hours', 6)} Stunden",
            "Max. lokale Backups": scheduler.get("max_local_backups", 20),
            "CPU-Warnung bei": f"{thresholds.get('cpu_warning', 80)}%",
            "RAM-Warnung bei": f"{thresholds.get('ram_warning', 85)}%",
            "Disk-Warnung bei": f"{thresholds.get('disk_warning', 90)}%",
            "Tick-Rate-Warnung": f"< {thresholds.get('tick_rate_warning', 20)}",
            "Spieler-Tracking": features.get("player_tracking", False),
            "OneDrive-Backup": features.get("onedrive_backup", False),
            "E-Mail-Benachrichtigungen": features.get("email_notifications", False),
            "Steam-Changelog": features.get("steam_changelog", False),
            "Graceful Degradation": features.get("graceful_degradation", False),
        }
    elif server_type == "teamspeak":
        server_config = {
            "Service": "teamspeak.service",
            "Port": "9987 (Voice) / 30033 (FileTransfer)",
        }

    # World/Savegame-Info aus Status-Daten
    world_info = {}
    if server_type == "minecraft":
        world_info = {
            "Server-Version": status_data.get("version", "N/A"),
            "Status": status_data.get("status", "unknown").capitalize(),
            "Spieler Online": f"{status_data.get('players', 0)}/{status_data.get('max_players', 20)}",
            "Service-Name": status_data.get("service_name", "N/A"),
            "Adresse": f"{status_data.get('address', 'N/A')}:{status_data.get('port', 'N/A')}",
            "Offline-Checks": status_data.get("offline_checks", 0),
            "Letztes Update": status_data.get("last_update", "N/A"),
        }
    elif server_type == "satisfactory":
        tick = status_data.get("tick_rate")
        world_info = {
            "Session-Name": status_data.get("session_name", "N/A"),
            "Status": status_data.get("status", "unknown").capitalize(),
            "Spieler Online": f"{status_data.get('players', 0)}/{status_data.get('max_players', 0)}",
            "Tick-Rate": f"{tick} FPS" if tick else "N/A",
            "CPU-Auslastung": f"{status_data.get('cpu_percent', 0)}%",
            "RAM-Verbrauch": f"{status_data.get('memory_mb', 0)} MB",
            "Uptime": status_data.get("uptime", "N/A"),
            "Adresse": f"{status_data.get('address', 'N/A')}:{status_data.get('port', 'N/A')}",
            "PID": status_data.get("pid", "N/A"),
            "API erreichbar": status_data.get("api_reachable", "N/A"),
            "Letztes Update": status_data.get("last_update", "N/A"),
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

    # systemd Service-Name ermitteln
    service_name = SERVICE_NAMES.get(server_id)
    if not service_name:
        return HTMLResponse(
            content=f'<div class="alert alert-danger">Kein Service fuer {display_name} konfiguriert.</div>',
            status_code=400,
        )

    # systemctl-Aktion ausfuehren
    systemctl_action = action
    if action == "maintenance":
        systemctl_action = "stop"

    try:
        proc = await asyncio.create_subprocess_exec(
            "sudo", "systemctl", systemctl_action, service_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

        if proc.returncode == 0:
            html = f"""
            <div class="alert alert-success">
                <strong>Erfolgreich:</strong>
                {action_display} fuer <strong>{display_name}</strong> wurde ausgefuehrt.
            </div>
            """
            logger.info(f"Server-Aktion erfolgreich: {action} {service_name}")
        else:
            error_text = stderr.decode("utf-8", errors="replace").strip()
            html = f"""
            <div class="alert alert-danger">
                <strong>Fehler:</strong>
                {action_display} fuer <strong>{display_name}</strong> fehlgeschlagen.<br>
                <code>{html_escape_module.escape(error_text[:500])}</code>
            </div>
            """
            logger.error(f"Server-Aktion fehlgeschlagen: {action} {service_name} — {error_text}")

    except asyncio.TimeoutError:
        html = f"""
        <div class="alert alert-warning">
            <strong>Timeout:</strong>
            {action_display} fuer <strong>{display_name}</strong> hat zu lange gedauert (30s Timeout).
        </div>
        """
        logger.warning(f"Server-Aktion Timeout: {action} {service_name}")
    except Exception as e:
        html = f"""
        <div class="alert alert-danger">
            <strong>Fehler:</strong>
            {action_display} konnte nicht ausgefuehrt werden: {html_escape_module.escape(str(e)[:300])}
        </div>
        """
        logger.error(f"Server-Aktion Exception: {action} {service_name} — {e}")

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
        "server_type": server_type,
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
    Mod-Suche — Platzhalter-Endpunkt.

    Unterstuetzt je nach Server-Typ verschiedene Quellen:
    - Minecraft: Modrinth / CurseForge
    - Satisfactory: ficsit.app (Satisfactory Mod Repository)
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

    safe_query = html_escape_module.escape(query)
    safe_source = html_escape_module.escape(source)
    resp_html = f"""
    <div class="alert alert-warning">
        <strong>Feature in Entwicklung:</strong>
        Suche nach &laquo;{safe_query}&raquo; auf {safe_source} empfangen.
        Die Mod-Suche wird aktiviert, sobald die Bots neben dem Dashboard laufen
        und der ModManager direkt angesprochen werden kann.
    </div>
    """
    return HTMLResponse(content=resp_html)


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
    safe_mod_name = html_escape_module.escape(mod_name)
    return HTMLResponse(content=f"""
    <div class="alert alert-warning">
        <strong>Feature in Entwicklung:</strong>
        Update fuer &laquo;{safe_mod_name}&raquo; empfangen.
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
    safe_mod_name = html_escape_module.escape(mod_name)
    return HTMLResponse(content=f"""
    <div class="alert alert-warning">
        <strong>Feature in Entwicklung:</strong>
        Deinstallation von &laquo;{safe_mod_name}&raquo; empfangen.
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

    command = command.strip()[:500]  # Laengenbeschraenkung gegen DoS
    if not command:
        return HTMLResponse(
            content='<div class="rcon-line rcon-error">&gt; Bitte einen Befehl eingeben.</div>',
        )

    display_name = _get_server_display_name(server_id)
    logger.info(f"RCON-Befehl fuer {display_name}: {command} (von {user.get('username', 'Unbekannt')})")

    timestamp = datetime.now().strftime("%H:%M:%S")
    safe_command = html_escape_module.escape(command)

    # RCON-Konfiguration aus ENV laden (MC_BMC_ oder MC_VANILLA_ Prefix)
    rcon_prefixes = {
        "mc_bmc": "MC_BMC_",
        "mc_vanilla": "MC_VANILLA_",
    }
    prefix = rcon_prefixes.get(server_id, "")
    rcon_host = get_env(f"{prefix}RCON_HOST", "127.0.0.1")
    rcon_port = int(get_env(f"{prefix}RCON_PORT", "25575"))
    rcon_password = get_env(f"{prefix}RCON_PASSWORD", "")

    if not rcon_password:
        resp_html = f"""
        <div class="rcon-line">
            <span class="rcon-timestamp">[{timestamp}]</span>
            <span class="rcon-input">&gt; {safe_command}</span>
        </div>
        <div class="rcon-line rcon-error">
            <span class="rcon-timestamp">[{timestamp}]</span>
            <span class="rcon-output">Fehler: RCON-Passwort nicht konfiguriert ({prefix}RCON_PASSWORD).</span>
        </div>
        """
        return HTMLResponse(content=resp_html)

    try:
        async with MinecraftRCON(rcon_host, rcon_port, rcon_password, timeout=5.0) as rcon:
            response = await rcon.command(command)

        safe_response = html_escape_module.escape(response[:2000]) if response else "(keine Antwort)"
        resp_html = f"""
        <div class="rcon-line">
            <span class="rcon-timestamp">[{timestamp}]</span>
            <span class="rcon-input">&gt; {safe_command}</span>
        </div>
        <div class="rcon-line rcon-response">
            <span class="rcon-timestamp">[{timestamp}]</span>
            <span class="rcon-output">{safe_response}</span>
        </div>
        """
    except Exception as e:
        error_msg = html_escape_module.escape(str(e)[:300])
        resp_html = f"""
        <div class="rcon-line">
            <span class="rcon-timestamp">[{timestamp}]</span>
            <span class="rcon-input">&gt; {safe_command}</span>
        </div>
        <div class="rcon-line rcon-error">
            <span class="rcon-timestamp">[{timestamp}]</span>
            <span class="rcon-output">RCON-Fehler: {error_msg}</span>
        </div>
        """
        logger.error(f"RCON-Fehler fuer {display_name}: {e}")

    return HTMLResponse(content=resp_html)
