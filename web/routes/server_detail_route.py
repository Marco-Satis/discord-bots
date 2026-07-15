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

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from utils.config import PROJECT_ROOT, DATA_DIR, MONITOR_DATA_DIR, get_config, get_env
from utils.logger import get_logger
from web.auth import require_auth, require_auth_api, require_perm
from modules.minecraft.rcon import MinecraftRCON
from modules.database.db_manager import get_db
# SatisfactoryAPI: KickPlayer funktioniert nicht ueber die API,
# daher Kick/Ban per UFW IP-Block + conntrack flush

logger = get_logger("web.routes.server_detail")

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter(tags=["Server-Detail"])

# Gueltige Server-IDs und deren Anzeigenamen
VALID_SERVER_IDS = {
    "satisfactory": "Satisfactory",
    "mc_bmc": "Minecraft BMC",
    "mc_vanilla": "Minecraft Vanilla",
}

# Server-Typ-Zuordnung (fuer Feature-Flags wie RCON)
SERVER_TYPES = {
    "satisfactory": "satisfactory",
    "mc_bmc": "minecraft",
    "mc_vanilla": "minecraft",
}

# Backup-Verzeichnisse pro Server (aus ENV oder Standard-Pfade)
BACKUP_DIRS = {
    "satisfactory": Path(get_env("BACKUP_PATH", "/home/satisfactory/backups")),
    "mc_bmc": Path(get_env("MC_BMC_BACKUP_PATH", "/home/minecraft/backups/bmc")),
    "mc_vanilla": Path(get_env("MC_VANILLA_BACKUP_PATH", "/home/minecraft/backups/vanilla")),
}

# Server-ID → systemd Service-Name Zuordnung
SERVICE_NAMES = {
    "satisfactory": "satisfactory.service",
    "mc_bmc": "minecraft-bmc.service",
    "mc_vanilla": "minecraft-vanilla.service",
}


def _load_json_safe(filepath: Path) -> dict:
    """Lädt eine JSON-Datei sicher. Gibt leeres Dict bei Fehler zurück."""
    try:
        if filepath.exists():
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Konnte {filepath} nicht laden: {e}")
    return {}


def _get_server_display_name(server_id: str) -> str:
    """Gibt den Anzeigenamen für eine Server-ID zurück."""
    return VALID_SERVER_IDS.get(server_id, server_id)


def _get_server_type(server_id: str) -> str:
    """Gibt den Server-Typ zurück (minecraft, satisfactory)."""
    return SERVER_TYPES.get(server_id, "unknown")


def _list_backups(server_id: str) -> list[dict]:
    """
    Listet Backup-Dateien für einen Server mit Metadaten auf.

    Durchsucht das Verzeichnis data/backups/{server_id}/ nach Dateien
    und gibt eine Liste mit Name, Größe und Datum zurück.
    """
    backups = []
    backup_dir = BACKUP_DIRS.get(server_id, DATA_DIR / "backups" / server_id)

    if not backup_dir.exists():
        logger.debug(f"Backup-Verzeichnis nicht gefunden: {backup_dir}")
        return backups

    try:
        for entry in sorted(backup_dir.iterdir(), reverse=True):
            try:
                stat = entry.stat()
                if entry.is_file():
                    size_mb = round(stat.st_size / (1024 * 1024), 2)
                elif entry.is_dir():
                    # Verzeichnis-Groesse berechnen (rekursiv)
                    total = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
                    size_mb = round(total / (1024 * 1024), 2)
                else:
                    continue
                modified = datetime.fromtimestamp(stat.st_mtime)

                backups.append({
                    "filename": entry.name,
                    "size_mb": size_mb,
                    "size_display": f"{size_mb} MB" if size_mb < 1024 else f"{round(size_mb / 1024, 2)} GB",
                    "date": modified.strftime("%d.%m.%Y %H:%M:%S"),
                    "timestamp": stat.st_mtime,
                    "is_dir": entry.is_dir(),
                })
            except OSError:
                pass
    except OSError as e:
        logger.warning(f"Fehler beim Lesen der Backups fuer {server_id}: {e}")

    return backups


def _get_server_info(server_id: str) -> dict:
    """
    Sammelt alle verfügbaren Informationen zu einem Server.

    Liest Status- und Spielerdaten aus den Monitor-JSON-Dateien,
    listet Backups auf und fügt Konfigurationsdaten hinzu.
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
        _api_r = status_data.get("api_reachable")
        _proc_r = status_data.get("process_running")
        server_config = {
            "API erreichbar": "Ja" if _api_r is True else ("Nein" if _api_r is False else "N/A"),
            "Prozess laeuft": "Ja" if _proc_r is True else ("Nein" if _proc_r is False else "N/A"),
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
        memory_mb = status_data.get("memory_mb", 0)
        cpu_pct = status_data.get("cpu_percent", 0)
        api_reachable = status_data.get("api_reachable")
        last_update_raw = status_data.get("last_update", "N/A")

        # ISO-Timestamp formatieren
        last_update_str = "N/A"
        if last_update_raw and last_update_raw != "N/A":
            try:
                dt = datetime.fromisoformat(last_update_raw.replace("Z", "+00:00"))
                last_update_str = dt.strftime("%d.%m.%Y %H:%M:%S")
            except (ValueError, TypeError):
                last_update_str = str(last_update_raw)

        world_info = {
            "Session-Name": status_data.get("session_name", "N/A"),
            "Status": status_data.get("status", "unknown").capitalize(),
            "Spieler Online": f"{status_data.get('players', 0)}/{status_data.get('max_players', 0)}",
            "Tick-Rate": f"{round(tick, 1)} FPS" if tick is not None else "N/A",
            "CPU-Auslastung": f"{round(cpu_pct, 1)}%" if cpu_pct is not None else "N/A",
            "RAM-Verbrauch": f"{memory_mb} MB" if memory_mb is not None else "N/A",
            "Uptime": status_data.get("uptime", "N/A"),
            "Adresse": f"{status_data.get('address', 'N/A')}:{status_data.get('port', 'N/A')}",
            "PID": status_data.get("pid", "N/A"),
            "API erreichbar": "Ja" if api_reachable is True else ("Nein" if api_reachable is False else "N/A"),
            "Letztes Update": last_update_str,
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
async def server_detail_page(request: Request, server_id: str, current_user: dict = Depends(require_auth)):
    """
    Hauptseite der Server-Detailansicht.

    Zeigt alle Informationen zu einem bestimmten Game-Server an,
    inklusive Tabs fuer Spieler, Backups, RCON, World-Info und Config.
    """
    # Ungueltige Server-ID abfangen
    if server_id not in VALID_SERVER_IDS:
        logger.warning(f"Unbekannte Server-ID angefragt: {server_id}")
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/", status_code=302)

    server = _get_server_info(server_id)

    return templates.TemplateResponse("server_detail.html", {
        "request": request,
        "user": current_user,
        "server": server,
    })


@router.get("/api/server/{server_id}/players", response_class=HTMLResponse)
async def server_players_partial(request: Request, server_id: str, current_user: dict = Depends(require_auth_api)):
    """
    HTMX-Partial: Gibt die aktuelle Spielerliste als HTML-Fragment zurueck.

    Wird automatisch alle 10 Sekunden per hx-trigger aktualisiert.
    """
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
async def server_backups_partial(request: Request, server_id: str, current_user: dict = Depends(require_auth_api)):
    """
    HTMX-Partial: Gibt die Backup-Liste als HTML-Fragment zurueck.
    """
    if server_id not in VALID_SERVER_IDS:
        return HTMLResponse(content="<p>Unbekannter Server</p>", status_code=404)

    backups = _list_backups(server_id)

    return templates.TemplateResponse("partials/server_backups.html", {
        "request": request,
        "backups": backups,
        "server_id": server_id,
    })


@router.post("/api/server/{server_id}/action", response_class=HTMLResponse)
async def server_action(request: Request, server_id: str, current_user: dict = Depends(require_perm("system", "control"))):
    """
    Server-Steuerung: Start, Stop, Restart, Kick, Ban.

    Liest action und target aus Form-Daten (HTMX hx-vals).
    Gibt eine Statusmeldung als HTMX-Fragment zurueck.
    """
    user = current_user
    if server_id not in VALID_SERVER_IDS:
        return HTMLResponse(content="<p>Unbekannter Server</p>", status_code=404)

    # Form-Daten vollstaendig lesen (HTMX sendet hx-vals als Form-encoded)
    try:
        form = await request.form()
        action = str(form.get("action", "")).strip()
        target = str(form.get("target", "")).strip()
    except Exception as e:
        logger.error(f"Form-Daten lesen fehlgeschlagen: {e}")
        return HTMLResponse(
            content=f'<div class="alert alert-danger">Fehler beim Lesen der Formulardaten: {html_escape_module.escape(str(e)[:200])}</div>',
            status_code=400,
        )

    valid_actions = ("start", "stop", "restart", "maintenance", "kick", "ban")
    if action not in valid_actions:
        return HTMLResponse(
            content='<div class="alert alert-danger">Ungueltige Aktion</div>',
            status_code=400,
        )

    display_name = _get_server_display_name(server_id)
    server_type = _get_server_type(server_id)
    action_names = {
        "start": "Starten",
        "stop": "Stoppen",
        "restart": "Neustart",
        "maintenance": "Wartungsmodus",
        "kick": "Kick",
        "ban": "Ban",
    }
    action_display = action_names.get(action, action)

    logger.info(f"Server-Aktion angefragt: {action} fuer {display_name} von Benutzer {user.get('username', 'Unbekannt')}"
                + (f" (Ziel: {target})" if target else ""))

    # ------------------------------------------------------------------
    # Kick / Ban — per RCON (Minecraft) oder Platzhalter (SAT)
    # ------------------------------------------------------------------
    if action in ("kick", "ban"):
        if not target:
            return HTMLResponse(
                content='<div class="alert alert-danger">Kein Spielername angegeben.</div>',
            )

        safe_target = html_escape_module.escape(target)

        if server_type == "minecraft":
            # RCON-Konfiguration laden
            rcon_prefixes = {"mc_bmc": "MC_BMC_", "mc_vanilla": "MC_VANILLA_"}
            prefix = rcon_prefixes.get(server_id, "")
            rcon_host = get_env(f"{prefix}RCON_HOST", "127.0.0.1")
            rcon_port = int(get_env(f"{prefix}RCON_PORT", "25575"))
            rcon_password = get_env(f"{prefix}RCON_PASSWORD", "")

            if not rcon_password:
                return HTMLResponse(
                    content=f'<div class="alert alert-danger">RCON-Passwort nicht konfiguriert ({prefix}RCON_PASSWORD).</div>',
                )

            rcon_cmd = f"{action} {target}"
            if action == "ban":
                rcon_cmd = f"ban {target} Gebannt via Dashboard"

            try:
                async with MinecraftRCON(rcon_host, rcon_port, rcon_password, timeout=5.0) as rcon:
                    response = await rcon.command(rcon_cmd)

                logger.info(f"RCON {action} erfolgreich: {target} auf {display_name}")
                return HTMLResponse(content=f"""
                    <div class="alert alert-success">
                        <strong>{action_display}:</strong> {safe_target} wurde auf {display_name} {action_display.lower()}t.
                        {f'<br><small>RCON: {html_escape_module.escape(response[:200])}</small>' if response else ''}
                    </div>
                """)
            except Exception as e:
                logger.error(f"RCON {action} fehlgeschlagen: {target} auf {display_name} — {e}")
                return HTMLResponse(content=f"""
                    <div class="alert alert-danger">
                        <strong>Fehler:</strong> {action_display} fuer {safe_target} fehlgeschlagen: {html_escape_module.escape(str(e)[:200])}
                    </div>
                """)

        elif server_type == "satisfactory":
            # SAT: Kick/Ban per UFW IP-Block + conntrack flush
            # (KickPlayer funktioniert nicht ueber die Satisfactory API)

            # IP des Spielers aus SQLite lesen
            # Schema: players(id, name) → player_ips(player_id, ip_address)
            player_ip = None
            try:
                db = await get_db()
                cursor = await db.execute(
                    "SELECT pi.ip_address FROM player_ips pi "
                    "JOIN players p ON p.id = pi.player_id "
                    "WHERE LOWER(p.name) = ? AND pi.server_type = 'satisfactory' "
                    "ORDER BY pi.last_seen DESC LIMIT 1",
                    (target.lower(),),
                )
                row = await cursor.fetchone()
                if row:
                    player_ip = row[0] if isinstance(row, (tuple, list)) else row["ip_address"]
                    logger.debug(f"IP fuer {target} aus DB geladen: {player_ip}")
            except Exception as e:
                logger.warning(f"DB-Abfrage fuer Spieler-IP fehlgeschlagen: {e}")

            if not player_ip:
                return HTMLResponse(content=f"""
                    <div class="alert alert-warning">
                        <strong>Keine IP bekannt:</strong> Fuer {safe_target} ist keine IP-Adresse hinterlegt.
                        Der Spieler muss mindestens einmal verbunden gewesen sein.
                    </div>
                """)

            # Defense-in-Depth: IP-Format-Re-Check (CWE-20).
            # DB-Inhalt koennte korrupt sein (manueller Eingriff, Migration), und
            # iptables-Argument muss strikt IPv4 oder IPv6 sein, sonst command-injection.
            try:
                import ipaddress
                _validated = ipaddress.ip_address(player_ip)
                player_ip = str(_validated)  # normalisierte Form
            except (ValueError, TypeError):
                logger.error(f"DB-IP fuer {target} ist kein valides IP-Format: {player_ip!r}")
                return HTMLResponse(content=f"""
                    <div class="alert alert-danger">
                        <strong>IP-Validierung fehlgeschlagen:</strong> Die in der DB gespeicherte
                        IP-Adresse fuer {safe_target} ist kein gueltiges IPv4/IPv6-Format.
                        Aktion abgebrochen (Defense-in-Depth).
                    </div>
                """, status_code=400)

            # --- Hilfsfunktionen: iptables REJECT (sofortige Trennung) ---
            async def _run_cmd(*args: str) -> tuple[int, str]:
                """Fuehrt einen Shell-Befehl aus. Returns (returncode, stderr)."""
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *args,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
                    return proc.returncode, stderr.decode("utf-8", errors="replace").strip()
                except Exception as e:
                    return -1, str(e)

            async def _iptables_block_ip(ip: str) -> tuple[bool, str]:
                """Blockiert IP per iptables REJECT (beide Richtungen).
                REJECT sendet ICMP unreachable → Client disconnected sofort."""
                # 1. Eingehend: alle Pakete von dieser IP ablehnen
                rc1, err1 = await _run_cmd(
                    "sudo", "iptables", "-I", "INPUT", "-s", ip, "-j", "REJECT")
                if rc1 != 0:
                    return False, f"iptables INPUT fehlgeschlagen (rc={rc1}): {err1}"

                # 2. Ausgehend: alle Pakete zu dieser IP ablehnen
                rc2, err2 = await _run_cmd(
                    "sudo", "iptables", "-I", "OUTPUT", "-d", ip, "-j", "REJECT")
                if rc2 != 0:
                    logger.warning(f"iptables OUTPUT fuer {ip} fehlgeschlagen (rc={rc2}): {err2}")

                return True, "OK"

            async def _iptables_unblock_ip(ip: str) -> None:
                """Entfernt iptables REJECT-Regeln (beide Richtungen)."""
                await _run_cmd("sudo", "iptables", "-D", "INPUT", "-s", ip, "-j", "REJECT")
                await _run_cmd("sudo", "iptables", "-D", "OUTPUT", "-d", ip, "-j", "REJECT")

            if action == "ban":
                # --- Ban: iptables REJECT + Blacklist ---
                block_ok, block_err = await _iptables_block_ip(player_ip)
                if not block_ok:
                    logger.error(f"iptables-Ban fehlgeschlagen: {block_err}")
                    return HTMLResponse(content=f"""
                        <div class="alert alert-danger">
                            <strong>Fehler:</strong> IP-Block fuer {safe_target} ({player_ip}) konnte nicht gesetzt werden.
                            <br><small>{html_escape_module.escape(block_err[:300])}</small>
                        </div>
                    """)

                # Ban in SQLite speichern (bans + blacklist Tabellen)
                ban_time = datetime.now().isoformat()
                banned_by = user.get("username", "Dashboard")
                try:
                    db = await get_db()
                    await db.execute(
                        "INSERT INTO bans (ip_address, player_name, server_type, reason, banned_by, banned_at, active, source) "
                        "VALUES (?, ?, 'satisfactory', 'Gebannt via Dashboard', ?, ?, TRUE, 'dashboard')",
                        (player_ip, target, banned_by, ban_time),
                    )
                    await db.execute(
                        "INSERT INTO blacklist (player_name, ip_address, server_type, reason, added_by, added_at) "
                        "VALUES (?, ?, 'satisfactory', 'Gebannt via Dashboard', ?, ?)",
                        (target, player_ip, banned_by, ban_time),
                    )
                    await db.commit()
                    logger.info(f"Ban fuer {target} ({player_ip}) in SQLite gespeichert")
                except Exception as e:
                    logger.warning(f"SQLite Ban-Eintrag fehlgeschlagen: {e}")

                logger.info(f"SAT Ban erfolgreich: {target} (IP: {player_ip}) via Dashboard")
                return HTMLResponse(content=f"""
                    <div class="alert alert-success">
                        <strong>Gebannt:</strong> {safe_target} (IP: {player_ip}) wurde gebannt.
                        <br><small>IP per UFW blockiert + Blacklist-Eintrag erstellt.</small>
                    </div>
                """)

            else:
                # --- Kick: iptables REJECT + 30s warten + aufheben ---
                block_ok, block_err = await _iptables_block_ip(player_ip)
                if not block_ok:
                    logger.error(f"iptables-Kick fehlgeschlagen: {block_err}")
                    return HTMLResponse(content=f"""
                        <div class="alert alert-danger">
                            <strong>Kick fehlgeschlagen:</strong> {safe_target} — {html_escape_module.escape(block_err[:300])}
                        </div>
                    """)

                logger.info(f"SAT Kick: iptables REJECT fuer {target} (IP: {player_ip})")

                # Im Hintergrund: 30 Sekunden warten, dann Regeln entfernen
                async def _delayed_unblock():
                    await asyncio.sleep(30)
                    await _iptables_unblock_ip(player_ip)
                    logger.info(f"SAT Kick: iptables-Regeln fuer {player_ip} entfernt (30s abgelaufen)")

                asyncio.create_task(_delayed_unblock())

                return HTMLResponse(content=f"""
                    <div class="alert alert-success">
                        <strong>Gekickt:</strong> {safe_target} (IP: {player_ip}) — Verbindung wird getrennt.
                        <br><small>IP fuer 30 Sekunden per iptables REJECT blockiert.</small>
                    </div>
                """)

        else:
            # Unbekannter Server-Typ
            return HTMLResponse(content=f"""
                <div class="alert alert-warning">
                    <strong>Nicht unterstuetzt:</strong> {action_display} ist fuer {display_name} aktuell nicht ueber das Dashboard verfuegbar.
                </div>
            """)

    # ------------------------------------------------------------------
    # Start / Stop / Restart / Maintenance — per systemctl
    # ------------------------------------------------------------------
    service_name = SERVICE_NAMES.get(server_id)
    if not service_name:
        return HTMLResponse(
            content=f'<div class="alert alert-danger">Kein Service fuer {display_name} konfiguriert.</div>',
            status_code=400,
        )

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
            # Manual-Stop-State pflegen damit Watchdog/Daily-Restart den User-Wunsch respektieren
            try:
                from modules.monitoring import manual_stop_state
                if action in ("stop", "maintenance"):
                    await manual_stop_state.mark_stopped(server_id)
                elif action in ("start", "restart"):
                    await manual_stop_state.mark_started(server_id)
            except Exception as e:
                logger.warning(f"manual_stop_state Update fehlgeschlagen ({action} {server_id}): {e}")

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
async def server_mods_partial(request: Request, server_id: str, current_user: dict = Depends(require_auth_api)):
    """
    HTMX-Partial: Gibt die Mod-Liste als HTML-Fragment zurueck.

    Liest die installierte Mod-Liste aus der jeweiligen JSON-Datei
    des ModManagers und stellt sie als Tabelle dar.
    """
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
async def server_mods_export(request: Request, server_id: str, current_user: dict = Depends(require_auth_api)):
    """
    Exportiert die installierte Mod-Liste als JSON-Download.
    """
    from fastapi.responses import JSONResponse

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
async def server_mods_search(request: Request, server_id: str, current_user: dict = Depends(require_perm("system", "view"))):
    """
    Mod-Suche — Platzhalter-Endpunkt.

    Unterstuetzt je nach Server-Typ verschiedene Quellen:
    - Minecraft: Modrinth / CurseForge
    - Satisfactory: ficsit.app (Satisfactory Mod Repository)
    """
    user = current_user
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
async def server_mods_check_updates(server_id: str, current_user: dict = Depends(require_perm("system", "view"))):
    """Prueft auf verfuegbare Mod-Updates — Platzhalter."""
    logger.info(f"Mod-Update-Check fuer {server_id} (von {current_user.get('username', 'Unbekannt')})")
    return HTMLResponse(content="""
    <div class="alert alert-warning">
        <strong>Feature in Entwicklung:</strong>
        Der Update-Check wird aktiviert, sobald die Bots neben dem Dashboard laufen.
    </div>
    """)


@router.post("/api/server/{server_id}/mods/update", response_class=HTMLResponse)
async def server_mod_update(request: Request, server_id: str, current_user: dict = Depends(require_perm("system", "control"))):
    """Aktualisiert einen einzelnen Mod — Platzhalter."""
    form = await request.form()
    mod_name = form.get("mod_name", "")
    logger.info(f"Mod-Update: '{mod_name}' fuer {server_id} (von {current_user.get('username', 'Unbekannt')})")
    safe_mod_name = html_escape_module.escape(mod_name)
    return HTMLResponse(content=f"""
    <div class="alert alert-warning">
        <strong>Feature in Entwicklung:</strong>
        Update fuer &laquo;{safe_mod_name}&raquo; empfangen.
    </div>
    """)


@router.post("/api/server/{server_id}/mods/uninstall", response_class=HTMLResponse)
async def server_mod_uninstall(request: Request, server_id: str, current_user: dict = Depends(require_perm("system", "control"))):
    """Deinstalliert einen Mod — Platzhalter."""
    form = await request.form()
    mod_name = form.get("mod_name", "")
    logger.info(f"Mod-Deinstallation: '{mod_name}' fuer {server_id} (von {current_user.get('username', 'Unbekannt')})")
    safe_mod_name = html_escape_module.escape(mod_name)
    return HTMLResponse(content=f"""
    <div class="alert alert-warning">
        <strong>Feature in Entwicklung:</strong>
        Deinstallation von &laquo;{safe_mod_name}&raquo; empfangen.
    </div>
    """)


@router.post("/api/server/{server_id}/rcon", response_class=HTMLResponse)
async def server_rcon(request: Request, server_id: str, command: str = Form(""), current_user: dict = Depends(require_perm("system", "control"))):
    """
    RCON-Befehl senden (nur Minecraft-Server).

    Aktuell ein Platzhalter — akzeptiert den Befehl und gibt
    eine simulierte Antwort zurueck.
    """
    user = current_user
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
