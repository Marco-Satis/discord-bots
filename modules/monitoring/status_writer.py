"""
StatusWriter — Schreibt periodisch Server- und Bot-Status als JSON-Dateien.

Wird als Hintergrund-Task im Recon gestartet und erzeugt die
Status-Dateien, die das Web-Dashboard zum Anzeigen der Übersicht liest.

Erzeugte Dateien (JSON-Bridge fuer Dashboard):
  - data/monitor/satisfactory_status.json
  - data/monitor/mc_bmc_status.json
  - data/gameserver/bot_status.json
  - data/monitor/bot_status.json
  - data/admin/bot_status.json

Events werden in SQLite gespeichert (events-Tabelle in botdata.db).
"""

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from modules.database.db_manager import get_db
from utils.config import DATA_DIR, MONITOR_DATA_DIR, GAMESERVER_DATA_DIR, ADMIN_DATA_DIR, get_env
from utils.logger import get_logger

logger = get_logger("status_writer")

# Maximale Anzahl Events im Event-Log
MAX_EVENTS = 100


def _format_uptime(seconds: int) -> str:
    """Formatiert Sekunden als lesbaren Uptime-String."""
    if seconds <= 0:
        return "N/A"
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def _write_json_safe(filepath: Path, data: dict) -> None:
    """Schreibt JSON-Daten atomar (via temp-Datei) um Race Conditions zu vermeiden."""
    tmp = filepath.with_suffix(".tmp")
    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(filepath)
    except (IOError, OSError) as e:
        logger.error(f"Fehler beim Schreiben von {filepath}: {e}")
        # Temp-Datei aufraeumen falls vorhanden
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


class StatusWriter:
    """
    Schreibt periodisch Server- und Bot-Status als JSON-Dateien
    fuer das Web-Dashboard.
    """

    def __init__(self, bot: Any, interval: int = 30) -> None:
        """
        Args:
            bot: Der Recon mit angehängten Services
            interval: Schreib-Intervall in Sekunden (Standard: 30)
        """
        self.bot = bot
        self.interval = interval
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._events: list[dict] = []
        self._bot_start_time: Optional[float] = None
        # Index bis zu dem self._events bereits nach SQLite geflusht wurden.
        # Neue Events (ab diesem Index) werden im nächsten write_once()-Zyklus
        # in die Datenbank geschrieben.
        self._events_flushed_up_to: int = 0
        # Wird True sobald _load_events_from_db() erfolgreich lief
        self._db_ready: bool = False

        # Verzeichnisse sicherstellen
        MONITOR_DATA_DIR.mkdir(parents=True, exist_ok=True)
        GAMESERVER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        ADMIN_DATA_DIR.mkdir(parents=True, exist_ok=True)

        logger.info(f"StatusWriter initialisiert — Intervall: {interval}s")

    async def _load_events_from_db(self) -> None:
        """
        Lädt Events aus SQLite. Wird beim ersten write_once()-Zyklus aufgerufen.

        Lädt die letzten MAX_EVENTS Events aus der SQLite events-Tabelle.
        """
        try:
            db = await get_db()
            cursor = await db.execute(
                "SELECT id, timestamp, event_type, category, server_id, message, details "
                "FROM events ORDER BY id DESC LIMIT ?",
                (MAX_EVENTS,),
            )
            rows = await cursor.fetchall()

            db_events = []
            for row in reversed(rows):  # Älteste zuerst
                db_events.append({
                    "timestamp": str(row["timestamp"]) if row["timestamp"] else "",
                    "type": row["event_type"] or "",
                    "message": row["message"] or "",
                    "category": row["category"],
                    "server_id": row["server_id"],
                    "details": row["details"],
                })
            self._events = db_events
            self._events_flushed_up_to = len(self._events)
            self._db_ready = True
            logger.info(f"Events aus SQLite geladen: {len(db_events)} Einträge")

        except Exception as e:
            logger.error(f"Fehler beim Laden der Events aus SQLite: {e}", exc_info=True)
            self._db_ready = False

    def add_event(
        self,
        event_type: str,
        message: str,
        *,
        category: Optional[str] = None,
        server_id: Optional[str] = None,
        details: Optional[str] = None,
    ) -> None:
        """
        Fügt ein neues Event zum In-Memory-Ringbuffer hinzu.

        Die tatsächliche Persistierung nach SQLite erfolgt asynchron
        im nächsten write_once()-Zyklus.

        Args:
            event_type: Typ des Events (info, warning, error, success)
            message: Beschreibung des Events
            category: Optionale Kategorie (z.B. 'server', 'bot', 'system')
            server_id: Optionale Server-ID (z.B. 'satisfactory', 'mc_bmc')
            details: Optionale JSON-Details
        """
        event = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "type": event_type,
            "message": message,
            "category": category,
            "server_id": server_id,
            "details": details,
        }
        self._events.append(event)
        # Ringbuffer trimmen
        if len(self._events) > MAX_EVENTS:
            trimmed = len(self._events) - MAX_EVENTS
            self._events = self._events[-MAX_EVENTS:]
            # Flushed-Index anpassen (bereits geflushte Events wurden getrimmt)
            self._events_flushed_up_to = max(0, self._events_flushed_up_to - trimmed)

    def _write_satisfactory_status(self) -> None:
        """
        Schreibt je Satisfactory-Instanz eine Statusdatei.

        Die erste Instanz behaelt den Dateinamen ``satisfactory_status.json`` —
        daran haengen die Dashboard-Kachel, die Health-API und die Detailseite.
        Weitere schreiben ``sat_<id>_status.json``; das Dashboard liest per
        Glob ueber ``*_status.json``, die Kachel erscheint also von selbst.
        """
        health_checkers = getattr(self.bot, "sat_health_checkers", None)
        if not health_checkers:
            # Uebergang: solange nur der Einzelserver verdrahtet ist.
            einzeln = getattr(self.bot, "health_checker", None)
            if einzeln is None:
                return
            health_checkers = {"MAIN": einzeln}

        update_checkers = getattr(self.bot, "sat_update_checkers", None) or {}
        servers = getattr(self.bot, "sat_servers", None) or {}
        erster = next(iter(health_checkers), "MAIN")

        for sid, health_checker in health_checkers.items():
            status = health_checker.status
            state_str = (status.state.value if hasattr(status.state, "value")
                         else str(status.state))

            # Update-Checker Daten (Build-IDs als Versions-Info)
            update_checker = update_checkers.get(sid) or getattr(
                self.bot, "update_checker", None)
            installed_build = "N/A"
            available_build = "N/A"
            update_available = False
            if update_checker:
                installed_build = getattr(update_checker, "installed_buildid", None) or "N/A"
                available_build = getattr(update_checker, "last_known_buildid", None) or "N/A"
                update_available = getattr(update_checker, "update_available", False)

            srv = servers.get(sid)
            kennung = "satisfactory" if sid == erster else f"sat_{sid.lower()}"
            name = srv.display_name if srv else "Satisfactory"
            port = get_env(f"SAT_{sid}_PORT", 7777 if sid == erster else 7778, cast=int)

            data = {
                "id": kennung,
                "name": name,
                "type": "satisfactory",
                "status": state_str,
                "players": status.players_online,
                "max_players": status.player_limit,
                "uptime": _format_uptime(status.uptime),
                "uptime_seconds": status.uptime,
                "cpu_percent": round(status.cpu_percent, 1),
                "memory_mb": status.memory_mb,
                "address": "203.0.113.10",
                "port": port,
                "session_name": status.session_name,
                "tick_rate": status.tick_rate,
                "pid": status.pid,
                "api_reachable": status.api_reachable,
                "process_running": status.process_running,
                "version": installed_build,
                "available_version": available_build,
                "update_available": update_available,
                "last_update": datetime.now(timezone.utc).isoformat(),
            }

            _write_json_safe(MONITOR_DATA_DIR / f"{kennung}_status.json", data)

    async def _write_minecraft_status(self) -> None:
        """Schreibt den Status aller Minecraft-Server (async wegen is_running)."""
        mc_servers = getattr(self.bot, "mc_servers", {})
        mc_player_trackers = getattr(self.bot, "mc_player_trackers", {})
        mc_update_checkers = getattr(self.bot, "mc_update_checkers", {})

        # Offline-Counter aus dem Recon holen (globale Variable)
        mc_consecutive_offline = getattr(self.bot, "_mc_consecutive_offline", {})

        for sid, srv in mc_servers.items():
            # Server-ID fuer Dateiname: BMC → mc_bmc
            file_id = f"mc_{sid.lower()}"

            # Status direkt via systemctl pruefen (async)
            try:
                is_running = await srv.is_running()
            except Exception as e:
                logger.debug(f"is_running() fehlgeschlagen fuer {sid}: {e}")
                is_running = None

            # Spielerdaten aus dem Player-Tracker
            players_online = 0
            player_list = []

            pt = mc_player_trackers.get(sid)
            if pt:
                online_names = getattr(pt, "_online_players", set())
                all_records = getattr(pt, "players", {})
                players_online = len(online_names)
                for name in online_names:
                    entry = {"name": name}
                    record = all_records.get(name)
                    if record:
                        entry["join_time"] = getattr(record, "current_session_start", "") or ""
                    player_list.append(entry)

            # Status bestimmen
            status_str = "unknown"
            if is_running is True:
                status_str = "online"
            elif is_running is False:
                status_str = "offline"

            # Offline-Counter für Info-Anzeige
            offline_count = mc_consecutive_offline.get(sid, 0)

            # Versions-Info aus dem MC Update-Checker (Paper-basierte Server)
            mc_version = "N/A"
            mc_latest_build = "N/A"
            mc_update_available = False
            uc = mc_update_checkers.get(sid)
            if uc:
                if uc.current_version:
                    mc_version = uc.current_version
                    if uc.current_build is not None:
                        mc_version = f"{uc.current_version} (Build {uc.current_build})"
                if uc.latest_build is not None:
                    mc_latest_build = f"Build {uc.latest_build}"
                mc_update_available = getattr(uc, "update_available", False)

            # BMC: Modpack-Version aus dem ModpackUpdater holen
            if sid.upper() == "BMC" and mc_version == "N/A":
                modpack_updater = getattr(self.bot, "modpack_updater", None)
                if modpack_updater:
                    mp_version = getattr(modpack_updater, "current_version", "")
                    mp_id = getattr(modpack_updater, "modpack_id", "")
                    if mp_version:
                        mc_version = f"Modpack {mp_version}"
                    # Verfügbare Version falls geprüft
                    mp_latest = getattr(modpack_updater, "latest_version", None)
                    if mp_latest and mp_latest != mp_version:
                        mc_latest_build = f"Modpack {mp_latest}"
                        mc_update_available = True

            # Fallback: Version aus server.properties / version.json lesen
            if mc_version == "N/A":
                mc_version = await self._read_mc_version_from_properties(srv)

            # Port aus Server-Konfiguration
            mc_port = getattr(srv, "rcon_port", 25565)
            # Game-Port ist typischerweise rcon_port - 10 oder aus ENV
            # Standardmäßig 25565, BMC nutzt 25566
            if sid.upper() == "BMC":
                mc_port = 25566
            else:
                mc_port = 25565

            # Uptime aus dem systemd-Service ermitteln
            mc_uptime = "N/A"
            service_name = getattr(srv, "service_name", "")
            if is_running and service_name:
                mc_uptime = await self._get_service_uptime(service_name)

            data = {
                "id": file_id,
                "name": srv.display_name,
                "type": "minecraft",
                "status": status_str,
                "players": players_online,
                "max_players": getattr(srv, "max_players", 20),
                "uptime": mc_uptime,
                "address": "203.0.113.10",
                "port": mc_port,
                "service_name": getattr(srv, "service_name", ""),
                "version": mc_version,
                "available_version": mc_latest_build,
                "update_available": mc_update_available,
                "offline_checks": offline_count,
                "last_update": datetime.now(timezone.utc).isoformat(),
            }

            _write_json_safe(MONITOR_DATA_DIR / f"{file_id}_status.json", data)

            # Spielerliste separat schreiben (fuer Server-Detail-Seite)
            players_data = {
                "server_id": file_id,
                "players": player_list,
                "last_update": datetime.now(timezone.utc).isoformat(),
            }
            _write_json_safe(MONITOR_DATA_DIR / f"{file_id}_players.json", players_data)

    async def _read_mc_version_from_properties(self, srv: Any) -> str:
        """Liest die MC-Version aus server.properties oder version.json (Fallback)."""
        import re as _re
        server_path = getattr(srv, "server_path", None)
        if not server_path:
            return "N/A"

        # Methode 1: version.json (Paper erstellt diese)
        version_json = Path(server_path) / "version.json"
        try:
            if version_json.exists():
                import json as _json
                data = _json.loads(version_json.read_text(encoding="utf-8"))
                version = data.get("id") or data.get("name")
                if version:
                    return version
        except Exception as e:
            logger.debug(f"Exception swallowed (B110-refactor 3.1): {e}")

        # Methode 2: version_history.json
        vh_path = Path(server_path) / "version_history.json"
        try:
            if vh_path.exists():
                import json as _json
                data = _json.loads(vh_path.read_text(encoding="utf-8"))
                current = data.get("currentVersion", "")
                match = _re.search(r"MC:\s*([\d.]+)", current)
                if match:
                    return match.group(1)
        except Exception as e:
            logger.debug(f"Exception swallowed (B110-refactor 3.1): {e}")

        return "N/A"

    def _write_satisfactory_players(self) -> None:
        """Schreibt die Satisfactory-Spielerliste fuer die Detailseite."""
        player_tracker = getattr(self.bot, "player_tracker", None)
        if player_tracker is None:
            return

        # Aktuelle Online-Spieler aus dem Tracker holen
        # PlayerTracker speichert online Spieler in _online_players (Set)
        # und Details in players (Dict[str, PlayerRecord])
        online_names = getattr(player_tracker, "_online_players", set())
        all_players = getattr(player_tracker, "players", {})
        player_list = []

        for name in online_names:
            entry = {"name": name}
            record = all_players.get(name)
            if record:
                entry["join_time"] = getattr(record, "current_session_start", "") or ""
            player_list.append(entry)

        data = {
            "server_id": "satisfactory",
            "players": player_list,
            "last_update": datetime.now(timezone.utc).isoformat(),
        }
        _write_json_safe(MONITOR_DATA_DIR / "satisfactory_players.json", data)

    async def _write_bot_status(self) -> None:
        """Schreibt den Bot-Status (Ping, Uptime) fuer alle Bots (async fuer systemctl)."""
        now = time.time()

        # Recon Status — direkt vom eigenen Bot-Objekt
        monitor_data = {
            "status": "online" if self.bot.is_ready() else "offline",
            "ping_ms": round(self.bot.latency * 1000) if self.bot.latency and self.bot.latency != float("inf") else 0,
            "uptime": _format_uptime(
                int(now - self._bot_start_time) if self._bot_start_time else 0
            ),
            "last_update": datetime.now(timezone.utc).isoformat(),
        }
        _write_json_safe(MONITOR_DATA_DIR / "bot_status.json", monitor_data)

        # Operator + Marshal: Nur schreiben wenn der Bot nicht selbst schreibt
        # (d.h. die Datei ist älter als 60s oder existiert nicht)
        for svc_name, data_dir, display in [
            ("operator-bot.service", GAMESERVER_DATA_DIR, "GameServer"),
            ("marshal-bot.service", ADMIN_DATA_DIR, "Admin"),
        ]:
            status_file = data_dir / "bot_status.json"
            # Prüfen ob der Bot selbst kürzlich geschrieben hat
            try:
                if status_file.exists():
                    age = time.time() - status_file.stat().st_mtime
                    if age < 60:
                        # Bot schreibt selbst — nicht überschreiben
                        continue
            except OSError:
                pass

            svc_running = await self._check_service_active(svc_name)
            svc_uptime = await self._get_service_uptime(svc_name) if svc_running else "N/A"
            data_dir.mkdir(parents=True, exist_ok=True)
            svc_data = {
                "status": "online" if svc_running else "offline",
                "ping_ms": "N/A",
                "uptime": svc_uptime,
                "last_update": datetime.now(timezone.utc).isoformat(),
            }
            _write_json_safe(status_file, svc_data)

    async def _check_service_active(self, service_name: str) -> bool:
        """Prüft ob ein systemd-Service aktiv ist."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "is-active", "--quiet", service_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=5.0)
            return proc.returncode == 0
        except (asyncio.TimeoutError, OSError, Exception) as e:
            logger.debug(f"Service-Check fuer {service_name} fehlgeschlagen: {e}")
            return False

    async def _get_service_uptime(self, service_name: str) -> str:
        """Ermittelt die Uptime eines systemd-Service über dessen PID."""
        try:
            # MainPID des Service holen
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "show", "--property=MainPID", "--value", service_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            pid_str = stdout.decode("utf-8").strip()
            if not pid_str or pid_str == "0":
                return "N/A"
            pid = int(pid_str)

            # Startzeit des Prozesses ueber /proc/[pid]/stat berechnen
            import os
            stat_path = f"/proc/{pid}/stat"
            if os.path.exists(stat_path):
                with open(stat_path, "r") as f:
                    stat_line = f.read()
                # Feld 22 (0-basiert: 21) = starttime in clock ticks seit Boot
                parts = stat_line.split(")")[-1].split()
                # parts[0] = state, parts[19] = starttime (index in der rest-Liste)
                start_ticks = int(parts[19])
                ticks_per_sec = os.sysconf("SC_CLK_TCK")

                # System-Uptime lesen
                with open("/proc/uptime", "r") as f:
                    system_uptime = float(f.read().split()[0])

                process_uptime_secs = int(system_uptime - (start_ticks / ticks_per_sec))
                if process_uptime_secs >= 0:
                    return _format_uptime(process_uptime_secs)
        except Exception as e:
            logger.debug(f"Uptime-Ermittlung fuer {service_name} fehlgeschlagen: {e}")
        return "N/A"

    async def _flush_events_to_db(self) -> None:
        """
        Flusht neue Events aus dem In-Memory-Ringbuffer nach SQLite.

        Wird in jedem write_once()-Zyklus aufgerufen. Nur Events die seit
        dem letzten Flush hinzugekommen sind werden geschrieben.
        """
        # Beim ersten Aufruf: Events aus DB laden / JSON migrieren
        if not self._db_ready:
            await self._load_events_from_db()

        # Neue Events seit dem letzten Flush ermitteln
        new_events = self._events[self._events_flushed_up_to:]
        if not new_events:
            return

        try:
            db = await get_db()
            for evt in new_events:
                await db.execute(
                    "INSERT INTO events (timestamp, event_type, category, "
                    "server_id, message, details) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        evt.get("timestamp", ""),
                        evt.get("type", "info"),
                        evt.get("category"),
                        evt.get("server_id"),
                        evt.get("message", ""),
                        evt.get("details"),
                    ),
                )
            await db.commit()
            self._events_flushed_up_to = len(self._events)
            logger.debug(f"{len(new_events)} Events nach SQLite geschrieben")
        except Exception as e:
            logger.error(f"Fehler beim Flushen der Events nach SQLite: {e}", exc_info=True)

    async def get_events_from_db(
        self,
        limit: int = 100,
        event_type: Optional[str] = None,
        category: Optional[str] = None,
        server_id: Optional[str] = None,
    ) -> list[dict]:
        """
        Liest Events direkt aus SQLite.

        Args:
            limit: Maximale Anzahl zurueckgegebener Events (neueste zuerst)
            event_type: Optionaler Filter nach Event-Typ
            category: Optionaler Filter nach Kategorie
            server_id: Optionaler Filter nach Server-ID

        Returns:
            Liste von Event-Dicts (neueste zuerst)
        """
        try:
            db = await get_db()

            query = "SELECT id, timestamp, event_type, category, server_id, message, details FROM events"
            conditions = []
            params: list[Any] = []

            if event_type is not None:
                conditions.append("event_type = ?")
                params.append(event_type)
            if category is not None:
                conditions.append("category = ?")
                params.append(category)
            if server_id is not None:
                conditions.append("server_id = ?")
                params.append(server_id)

            if conditions:
                query += " WHERE " + " AND ".join(conditions)

            query += " ORDER BY id DESC LIMIT ?"
            params.append(limit)

            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()

            return [
                {
                    "id": row["id"],
                    "timestamp": str(row["timestamp"]) if row["timestamp"] else "",
                    "type": row["event_type"] or "",
                    "message": row["message"] or "",
                    "category": row["category"],
                    "server_id": row["server_id"],
                    "details": row["details"],
                }
                for row in rows
            ]
        except Exception as e:
            logger.error(f"Fehler beim Lesen der Events aus SQLite: {e}", exc_info=True)
            # Fallback: In-Memory-Events zurückgeben (neueste zuerst)
            return list(reversed(self._events[-limit:]))

    async def write_once(self) -> None:
        """Führt eine einzelne Schreibrunde durch. Jede Schreibung ist unabhängig."""
        # Satisfactory Status
        try:
            await asyncio.to_thread(self._write_satisfactory_status)
        except Exception as e:
            logger.error(f"Fehler beim Schreiben des SAT-Status: {e}", exc_info=True)

        # Minecraft Status (async wegen systemctl)
        try:
            await self._write_minecraft_status()
        except Exception as e:
            logger.error(f"Fehler beim Schreiben des MC-Status: {e}", exc_info=True)

        # Satisfactory Spielerliste
        try:
            await asyncio.to_thread(self._write_satisfactory_players)
        except Exception as e:
            logger.error(f"Fehler beim Schreiben der SAT-Spieler: {e}", exc_info=True)

        # Bot-Status (async wegen systemctl)
        try:
            await self._write_bot_status()
        except Exception as e:
            logger.error(f"Fehler beim Schreiben des Bot-Status: {e}", exc_info=True)

        # Events nach SQLite flushen
        try:
            await self._flush_events_to_db()
        except Exception as e:
            logger.error(f"Fehler beim Flushen der Events: {e}", exc_info=True)

        # F27: Health Auto-Restart Status
        try:
            har = getattr(self.bot, "health_auto_restart", None)
            if har:
                _write_json_safe(MONITOR_DATA_DIR / "health_auto_restart.json", har.get_status())
        except Exception as e:
            logger.debug(f"Fehler beim Schreiben des Health-Auto-Restart-Status: {e}")

        # F49: Disk Guard Status
        try:
            dg = getattr(self.bot, "disk_guard", None)
            if dg:
                status = await dg.check_disk()
                _write_json_safe(MONITOR_DATA_DIR / "disk_guard.json", status)
        except Exception as e:
            logger.debug(f"Fehler beim Schreiben des Disk-Guard-Status: {e}")

        # F50: Service Watchdog Status
        try:
            sw = getattr(self.bot, "service_watchdog", None)
            if sw:
                _write_json_safe(MONITOR_DATA_DIR / "service_watchdog.json", sw.get_summary())
        except Exception as e:
            logger.debug(f"Fehler beim Schreiben des Service-Watchdog-Status: {e}")

        # F51: DuckDNS Monitor Status
        try:
            ddns = getattr(self.bot, "duckdns_monitor", None)
            if ddns and ddns.last_result:
                _write_json_safe(MONITOR_DATA_DIR / "duckdns_monitor.json", ddns.last_result)
        except Exception as e:
            logger.debug(f"Fehler beim Schreiben des DuckDNS-Status: {e}")

        # F52: Port Monitor Status
        try:
            pm = getattr(self.bot, "port_monitor", None)
            if pm:
                _write_json_safe(MONITOR_DATA_DIR / "port_monitor.json", pm.get_summary())
        except Exception as e:
            logger.debug(f"Fehler beim Schreiben des Port-Monitor-Status: {e}")

        logger.debug("Status-Dateien-Runde abgeschlossen")

    async def start(self) -> None:
        """Startet die periodische Statusschreibung als Hintergrund-Task."""
        if self._running:
            logger.warning("StatusWriter laeuft bereits")
            return

        self._running = True
        self._bot_start_time = time.time()
        self._task = asyncio.create_task(self._write_loop())
        logger.info(f"StatusWriter gestartet — Intervall: {self.interval}s")

    async def stop(self) -> None:
        """Stoppt die periodische Statusschreibung."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Abschließend nochmal schreiben
        await self.write_once()
        logger.info("StatusWriter gestoppt")

    async def _write_loop(self) -> None:
        """Interne Schreibschleife — läuft bis stop() aufgerufen wird."""
        logger.info("Status-Schreibschleife gestartet")
        while self._running:
            try:
                await self.write_once()
            except Exception as e:
                logger.error(f"Fehler in der Schreibschleife: {e}", exc_info=True)

            try:
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                break
        logger.info("Status-Schreibschleife beendet")
