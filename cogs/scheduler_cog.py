"""
Scheduler Cog - Scheduled Background Tasks
Handles: Auto-Backup, Daily Restart, Update Checks, Weekly Report, Scheduled Messages
"""

import os
import asyncio
import json
import re
import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from zoneinfo import ZoneInfo

import aiofiles

from utils.logger import get_logger
from utils.config import get_config, get_env, DATA_DIR
from utils.permissions import is_admin, admin_only
from modules.notifications.discord_notifier import NotifyLevel

BERLIN_TZ = ZoneInfo("Europe/Berlin")
SCHEDULED_MESSAGES_FILE = DATA_DIR / "scheduled_messages.json"
MAX_ACTIVE_SCHEDULES = 20

logger = get_logger("scheduler_cog")


class SchedulerCog(commands.Cog):
    """Manages all scheduled/periodic background tasks for the Monitor Bot"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config = get_config()

        # Scheduler config
        sched = self.config.get("scheduler", {})
        self._daily_restart_hour = sched.get("daily_restart_hour", 4)
        self._daily_restart_minute = sched.get("daily_restart_minute", 0)
        self._daily_restart_enabled = sched.get("daily_restart_enabled", True)
        self._auto_backup_interval_hours = sched.get("auto_backup_interval_hours", 6)
        self._auto_backup_enabled = sched.get("auto_backup_enabled", True)
        self._backup_before_restart = sched.get("backup_before_restart", True)
        self._max_local_backups = sched.get("max_local_backups", 20)
        self._update_check_interval_hours = sched.get("update_check_interval_hours", 6)
        self._update_check_enabled = sched.get("update_check_enabled", True)
        self._auto_update_enabled = sched.get("auto_update_enabled", False)
        self._auto_update_hour = sched.get("auto_update_hour", 5)  # Install at 5 AM
        self._auto_update_require_empty = sched.get("auto_update_require_empty", True)
        self._weekly_report_day = sched.get("weekly_report_day", 0)  # 0=Monday
        self._weekly_report_hour = sched.get("weekly_report_hour", 8)
        self._daily_report_enabled = sched.get("daily_report_enabled", False)
        self._daily_report_hour = sched.get("daily_report_hour", 7)
        self._min_uptime_for_restart_hours = sched.get("min_uptime_for_restart_hours", 12)
        self._config_backup_hour = sched.get("config_backup_hour", 3)
        self._config_backup_enabled = sched.get("config_backup_enabled", True)

        # Minecraft Scheduler Config
        mc_sched = sched.get("minecraft", {})
        self._mc_daily_restart_hour = mc_sched.get("daily_restart_hour", 5)
        self._mc_daily_restart_minute = mc_sched.get("daily_restart_minute", 0)
        self._mc_daily_restart_enabled = mc_sched.get("daily_restart_enabled", True)
        self._mc_auto_backup_enabled = mc_sched.get("auto_backup_enabled", True)
        self._mc_auto_backup_interval_hours = mc_sched.get("auto_backup_interval_hours", 6)
        self._mc_update_check_enabled = mc_sched.get("update_check_enabled", True)
        self._mc_update_check_interval_hours = mc_sched.get("update_check_interval_hours", 6)
        self._mc_config_backup_enabled = mc_sched.get("config_backup_enabled", True)
        self._mc_config_backup_hour = mc_sched.get("config_backup_hour", 3)
        self._mc_backup_before_restart = mc_sched.get("backup_before_restart", True)

        # State
        self._last_auto_backup: Optional[datetime] = None
        self._last_config_backup: Optional[datetime] = None
        self._last_update_check: Optional[datetime] = None
        self._last_daily_restart: Optional[datetime] = None
        self._last_auto_update: Optional[datetime] = None
        self._last_daily_report: Optional[datetime] = None
        self._pending_update: bool = False

        # Minecraft State (pro Server)
        self._mc_last_restart: dict[str, Optional[datetime]] = {}
        self._mc_last_backup: dict[str, Optional[datetime]] = {}
        self._mc_last_update_check: dict[str, Optional[datetime]] = {}
        self._mc_last_config_backup: dict[str, Optional[datetime]] = {}

        # Modpack-Update-Check (Phase 8h)
        self._modpack_check_interval_hours = sched.get("modpack_check_interval_hours", 12)
        self._last_modpack_check: Optional[datetime] = None

        # Scheduled Messages (Phase 8f)
        self._scheduled_messages: List[Dict[str, Any]] = []
        self._next_schedule_id = 1
        self._schedule_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Properties for accessing bot-level services
    # ------------------------------------------------------------------

    @property
    def sat_server(self):
        return getattr(self.bot, "sat_server", None)

    @property
    def sat_api(self):
        return getattr(self.bot, "sat_api", None)

    @property
    def backup_manager(self):
        return getattr(self.bot, "backup_manager", None)

    @property
    def onedrive(self):
        return getattr(self.bot, "onedrive_backup", None)

    @property
    def notifier(self):
        return getattr(self.bot, "notifier", None)

    @property
    def email_notifier(self):
        return getattr(self.bot, "email_notifier", None)

    @property
    def update_checker(self):
        return getattr(self.bot, "update_checker", None)

    @property
    def config_backup(self):
        return getattr(self.bot, "config_backup", None)

    @property
    def player_tracker(self):
        return getattr(self.bot, "player_tracker", None)

    @property
    def health_checker(self):
        return getattr(self.bot, "health_checker", None)

    @property
    def restart_timer(self):
        return getattr(self.bot, "restart_timer_manager", None)

    @property
    def mc_servers(self):
        return getattr(self.bot, "mc_servers", {})

    @property
    def mc_backup_mgrs(self):
        return getattr(self.bot, "mc_backup_mgrs", {})

    @property
    def mc_settings_mgrs(self):
        return getattr(self.bot, "mc_settings_mgrs", {})

    @property
    def mc_update_checkers(self):
        return getattr(self.bot, "mc_update_checkers", {})

    @property
    def modpack_updater(self):
        return getattr(self.bot, "modpack_updater", None)

    # ------------------------------------------------------------------
    # Cog lifecycle
    # ------------------------------------------------------------------

    async def cog_load(self):
        """Start all scheduled tasks when cog loads"""
        await self._load_scheduled_messages()
        self.scheduler_tick.start()
        logger.info("Scheduler started")

    async def cog_unload(self):
        """Stop all tasks when cog unloads"""
        self.scheduler_tick.cancel()
        logger.info("Scheduler stopped")

    # ------------------------------------------------------------------
    # Main scheduler loop (runs every minute)
    # ------------------------------------------------------------------

    @tasks.loop(seconds=60)
    async def scheduler_tick(self):
        """Main scheduler - checks every minute what needs to run"""
        now = datetime.now()

        try:
            # Auto-backup check
            if self._auto_backup_enabled:
                await self._check_auto_backup(now)

            # Daily restart check
            if self._daily_restart_enabled:
                await self._check_daily_restart(now)

            # Update check
            if self._update_check_enabled:
                await self._check_update(now)

            # Auto-update install (separate from check)
            if self._auto_update_enabled and self._pending_update:
                await self._check_auto_update_install(now)

            # Daily report
            if self._daily_report_enabled:
                await self._check_daily_report(now)

            # Config backup check
            if self._config_backup_enabled:
                await self._check_config_backup(now)

            # Weekly report check
            await self._check_weekly_report(now)

            # Modpack-Update-Check (Phase 8h)
            await self._check_modpack_update(now)

            # Scheduled Messages (Phase 8f)
            await self._check_scheduled_messages(now)

            # Minecraft Server Checks
            for mc_sid in self.mc_servers:
                if self._mc_daily_restart_enabled:
                    await self._check_mc_daily_restart(now, mc_sid)
                if self._mc_auto_backup_enabled:
                    await self._check_mc_auto_backup(now, mc_sid)
                if self._mc_update_check_enabled:
                    await self._check_mc_update(now, mc_sid)
                if self._mc_config_backup_enabled:
                    await self._check_mc_config_backup(now, mc_sid)

        except Exception as e:
            logger.error(f"Scheduler tick error: {e}", exc_info=True)

    @scheduler_tick.before_loop
    async def before_scheduler(self):
        await self.bot.wait_until_ready()
        # Wait a bit before starting scheduled tasks
        await asyncio.sleep(30)

    # ------------------------------------------------------------------
    # Auto-Backup
    # ------------------------------------------------------------------

    async def _check_auto_backup(self, now: datetime):
        """Check if auto-backup should run"""
        if not self.backup_manager or not self.sat_server:
            return

        interval = timedelta(hours=self._auto_backup_interval_hours)

        if self._last_auto_backup and (now - self._last_auto_backup) < interval:
            return

        # Only backup if server is running
        running = await self.sat_server.is_running()
        if not running:
            return

        logger.info("Auto-Backup starting...")
        self._last_auto_backup = now

        try:
            # Save game first
            try:
                await self.sat_api.save_game()
                await asyncio.sleep(5)  # Wait for save to complete
            except Exception as e:
                logger.warning(f"save_game() vor Auto-Backup fehlgeschlagen: {e}")

            # Create backup
            success, msg, backup_path = await self.backup_manager.create_backup(
                name="auto-backup", created_by="scheduler"
            )

            if success and backup_path:
                # Rotation happens automatically inside create_backup()

                # OneDrive upload
                if self.onedrive and self.onedrive.enabled:
                    cloud_ok, cloud_msg = await self.onedrive.upload(str(backup_path))
                    if cloud_ok:
                        await self.onedrive.rotate()
                        logger.info(f"Cloud backup: {cloud_msg}")
                    else:
                        logger.warning(f"Cloud backup failed: {cloud_msg}")

                # Notify
                if self.notifier:
                    size_mb = round(backup_path.stat().st_size / (1024 * 1024), 1) if backup_path.exists() else 0
                    await self.notifier.notify_backup_success(
                        filename=backup_path.name,
                        size_mb=size_mb,
                        backup_type="Auto",
                    )

                logger.info(f"Auto-Backup erfolgreich: {backup_path.name}")
            else:
                if self.notifier:
                    await self.notifier.notify_backup_failed(
                        msg, backup_type="Auto"
                    )
                logger.error(f"Auto-Backup fehlgeschlagen: {msg}")

        except Exception as e:
            logger.error(f"Auto-Backup error: {e}", exc_info=True)

    # ------------------------------------------------------------------
    # Daily Restart
    # ------------------------------------------------------------------

    async def _check_daily_restart(self, now: datetime):
        """Check if daily restart should run"""
        if not self.sat_server:
            return

        # Check if it's the right time
        target = now.replace(
            hour=self._daily_restart_hour,
            minute=self._daily_restart_minute,
            second=0, microsecond=0
        )

        # Within 1-minute window of target time
        if abs((now - target).total_seconds()) > 60:
            return

        # Already restarted today?
        if self._last_daily_restart and self._last_daily_restart.date() == now.date():
            return

        # Server running?
        running = await self.sat_server.is_running()
        if not running:
            self._last_daily_restart = now
            return

        # Check minimum uptime
        if self.health_checker and self.health_checker.status.uptime > 0:
            uptime_hours = self.health_checker.status.uptime / 3600
            if uptime_hours < self._min_uptime_for_restart_hours:
                logger.info(
                    f"Skipping daily restart: uptime {uptime_hours:.1f}h "
                    f"< minimum {self._min_uptime_for_restart_hours}h"
                )
                self._last_daily_restart = now
                return

        # Check if players are online - delay if so
        if self.health_checker and self.health_checker.status.players_online > 0:
            logger.info(
                f"Delaying daily restart: {self.health_checker.status.players_online} "
                f"players online"
            )
            return  # Will try again next minute

        logger.info("Daily restart starting...")
        self._last_daily_restart = now

        try:
            # Backup before restart
            if self._backup_before_restart and self.backup_manager:
                try:
                    await self.sat_api.save_game()
                    await asyncio.sleep(5)
                except Exception as e:
                    logger.warning(f"save_game() vor Daily Restart fehlgeschlagen: {e}")

                success, msg, backup_path = await self.backup_manager.create_backup(
                    name="pre-restart", created_by="scheduler"
                )
                if success:
                    logger.info(f"Pre-restart backup: {backup_path.name if backup_path else msg}")

            # Use restart timer for in-game warnings before restart
            from modules.restart_timer import RestartTimer, TimerResult

            # Admin-Channel fuer Restart-Warnungen
            game_ch = None
            admin_ch_id = int(os.environ.get("ADMIN_LOG_CHANNEL_ID", 0))
            if admin_ch_id:
                game_ch = self.bot.get_channel(admin_ch_id)

            timer = RestartTimer(api=self.sat_api, channel=game_ch)
            result = await timer.countdown(
                duration_minutes=15,
                action_name="Neustart",
                warnings=[15, 5, 1],
            )

            if result == TimerResult.COMPLETED:
                success, msg = await self.sat_server.restart()
            elif result == TimerResult.CANCELLED:
                logger.info("Daily restart timer cancelled")
                return
            else:
                logger.warning(f"Daily restart timer error: {result}")
                # Try direct restart as fallback
                success, msg = await self.sat_server.restart()

            if self.notifier:
                time_str = f"{self._daily_restart_hour:02d}:{self._daily_restart_minute:02d}"
                await self.notifier.notify_scheduled_restart(time_str)

        except Exception as e:
            logger.error(f"Daily restart error: {e}", exc_info=True)

    async def _restart_warning_callback(self, minutes_left: int, message: str):
        """Callback for restart timer warnings"""
        if self.notifier and self.notifier.admin_channel:
            await self.notifier.admin_channel.send(
                f"⏰ **Server Restart in {minutes_left} Minuten** — {message}"
            )

    # ------------------------------------------------------------------
    # Update Check
    # ------------------------------------------------------------------

    async def _check_update(self, now: datetime):
        """Periodically check for server updates"""
        if not self.update_checker:
            return

        interval = timedelta(hours=self._update_check_interval_hours)
        if self._last_update_check and (now - self._last_update_check) < interval:
            return

        self._last_update_check = now

        try:
            available, info = await self.update_checker.check()

            if available:
                self._pending_update = True

                if self.notifier:
                    auto_hint = ""
                    if self._auto_update_enabled:
                        auto_hint = (
                            "\n\nAuto-Update ist aktiv und wird beim naechsten "
                            "'Server leer'-Moment installiert."
                        )
                    await self.notifier.notify_update_available(
                        info.get("installed_buildid", "?"),
                        info.get("available_buildid", "?"),
                    )

                if self.email_notifier:
                    await self.email_notifier.send_update_available(
                        info.get("installed_buildid", "?"),
                        info.get("available_buildid", "?"),
                    )
            else:
                self._pending_update = False
        except Exception as e:
            logger.debug(f"Update check error: {e}")

    # ------------------------------------------------------------------
    # Auto-Update Install
    # ------------------------------------------------------------------

    async def _check_auto_update_install(self, now: datetime):
        """Auto-Update sofort ausfuehren wenn Server leer oder offline.

        Phase 10e (F20): Uhrzeitbedingung entfernt — Update wird bei
        naechstem "Server leer"-Moment oder Offline-Zustand installiert.
        Auto-Rollback bei fehlgeschlagenem Update (3 Min Health-Check).
        Spieler-Benachrichtigung nach erfolgreichem Update.
        """
        if not self.update_checker or not self.sat_server:
            return

        running = await self.sat_server.is_running()

        # Server laeuft + Spieler online → Update verschieben
        if running:
            if self.health_checker and self.health_checker.status.players_online > 0:
                return  # Warten bis Server leer

        # Cooldown: Nicht oefter als alle 30 Minuten versuchen
        if self._last_auto_update and (now - self._last_auto_update).total_seconds() < 1800:
            return

        logger.info("Auto-Update startet (Server leer oder offline)...")
        self._last_auto_update = now

        # Alte Build-ID merken (fuer Spieler-Benachrichtigung)
        old_build = "?"
        try:
            _, info = await self.update_checker.check()
            old_build = info.get("installed_buildid", "?")
        except Exception:
            pass

        pre_update_backup_path = None

        try:
            if self.notifier:
                await self.notifier.send_admin(
                    "Auto-Update gestartet",
                    "Server wird fuer Update gestoppt...",
                    level=NotifyLevel.WARNING if running else NotifyLevel.INFO,
                )

            # 1. Backup vor Update erstellen
            was_running = running
            if running and self.backup_manager:
                try:
                    await self.sat_api.save_game()
                    await asyncio.sleep(5)
                except Exception as e:
                    logger.warning(f"save_game() vor Auto-Update fehlgeschlagen: {e}")

                success, msg, backup_path = await self.backup_manager.create_backup(
                    name="pre-update", created_by="auto-update"
                )
                if success and backup_path:
                    pre_update_backup_path = backup_path
                    logger.info(f"Pre-Update Backup: {backup_path.name}")

            # 2. Server stoppen falls laufend
            if running:
                try:
                    await self.sat_api.run_command(
                        "ServerChat Server wird in 2 Minuten fuer ein Update neu gestartet!"
                    )
                except Exception:
                    pass
                await asyncio.sleep(120)  # 2 Min Warnung

                stop_ok, stop_msg = await self.sat_server.stop()
                if not stop_ok:
                    logger.error(f"Auto-Update: Server Stop fehlgeschlagen: {stop_msg}")
                    if self.notifier:
                        await self.notifier.send_admin(
                            "Auto-Update FEHLGESCHLAGEN",
                            f"Server konnte nicht gestoppt werden: {stop_msg}",
                            level=NotifyLevel.ERROR,
                            ping_role=True,
                        )
                    return
                await asyncio.sleep(10)

            # 3. Update durchfuehren
            update_ok, update_msg = await self.update_checker.perform_update(self.sat_server)

            if update_ok:
                self._pending_update = False
                logger.info(f"Auto-Update: {update_msg}")

                # 4. Server starten falls er lief
                if was_running:
                    start_ok, start_msg = await self.sat_server.start()
                    if not start_ok:
                        logger.error(f"Server Start nach Update fehlgeschlagen: {start_msg}")

                    # 5. Health-Check (3 Minuten warten, dann pruefen)
                    if start_ok:
                        rollback_needed = await self._health_check_after_update()
                        if rollback_needed and pre_update_backup_path:
                            await self._perform_rollback(
                                pre_update_backup_path, update_msg
                            )
                            return

                # 6. Erfolgsbenachrichtigung
                new_build = "?"
                try:
                    _, new_info = await self.update_checker.check()
                    new_build = new_info.get("installed_buildid", "?")
                except Exception:
                    pass

                server_state = "gestartet" if was_running else "war offline"
                if self.notifier:
                    await self.notifier.send_admin(
                        "Auto-Update erfolgreich",
                        f"{update_msg}\n\nServer {server_state}.",
                        NotifyLevel.SUCCESS,
                    )
                    # Spieler-Channel Benachrichtigung
                    player_channel_id = get_env(
                        "PUBLIC_STATUS_CHANNEL_ID", cast=int
                    )
                    await self.notifier.notify_player_update(
                        old_build, new_build, player_channel_id
                    )

                if self.email_notifier:
                    await self.email_notifier._send_email(
                        "Auto-Update erfolgreich",
                        f"{update_msg}\nServer {server_state}.",
                        "auto_update_success",
                    )
            else:
                logger.error(f"Auto-Update fehlgeschlagen: {update_msg}")

                # Rollback versuchen wenn Backup vorhanden
                if was_running and pre_update_backup_path:
                    await self._perform_rollback(pre_update_backup_path, update_msg)
                elif was_running:
                    # Kein Backup — trotzdem versuchen zu starten
                    await self.sat_server.start()

                if self.notifier and not pre_update_backup_path:
                    await self.notifier.send_admin(
                        "Auto-Update FEHLGESCHLAGEN",
                        update_msg,
                        level=NotifyLevel.ERROR,
                        ping_role=True,
                    )

        except Exception as e:
            logger.error(f"Auto-Update Fehler: {e}", exc_info=True)
            try:
                await self.sat_server.start()
            except Exception:
                pass

    async def _health_check_after_update(self) -> bool:
        """Prueft ob der Server nach einem Update innerhalb von 3 Minuten
        wieder erreichbar ist.

        Returns:
            True wenn Rollback noetig (Server nicht erreichbar),
            False wenn Server OK
        """
        logger.info("Post-Update Health-Check: Warte 3 Minuten...")
        # 6 Pruefungen alle 30 Sekunden = 3 Minuten
        for i in range(6):
            await asyncio.sleep(30)
            try:
                if await self.sat_server.is_running():
                    # API-Check fuer tiefere Pruefung
                    if self.sat_api:
                        try:
                            await self.sat_api.query_server_state()
                            logger.info(
                                f"Post-Update Health-Check OK (nach {(i+1)*30}s)"
                            )
                            return False  # Kein Rollback noetig
                        except Exception:
                            continue  # API noch nicht bereit
                    else:
                        logger.info(
                            f"Post-Update Health-Check OK (nach {(i+1)*30}s)"
                        )
                        return False
            except Exception:
                continue

        logger.error("Post-Update Health-Check FEHLGESCHLAGEN nach 3 Minuten")
        return True  # Rollback noetig

    async def _perform_rollback(self, backup_path, update_msg: str) -> None:
        """Fuehrt einen automatischen Rollback nach fehlgeschlagenem Update durch.

        1. Server stoppen (falls haengend)
        2. Pre-Update-Backup wiederherstellen
        3. Server neu starten
        4. Admin-Benachrichtigung
        """
        logger.warning("Auto-Rollback: Stelle Pre-Update-Backup wieder her...")

        try:
            # Server stoppen falls er haengt
            try:
                await self.sat_server.stop()
                await asyncio.sleep(5)
            except Exception:
                pass

            # Backup wiederherstellen
            restore_ok = False
            if self.backup_manager:
                restore_ok, restore_msg = await self.backup_manager.restore(
                    backup_path.name if hasattr(backup_path, 'name') else str(backup_path)
                )
                if restore_ok:
                    logger.info(f"Rollback: Backup wiederhergestellt — {restore_msg}")
                else:
                    logger.error(f"Rollback: Restore fehlgeschlagen — {restore_msg}")

            # Server neu starten
            start_ok, start_msg = await self.sat_server.start()

            if self.notifier:
                if restore_ok and start_ok:
                    await self.notifier.notify_update_rollback(
                        update_msg, "Server nach Update nicht erreichbar"
                    )
                elif not start_ok:
                    await self.notifier.send_admin(
                        "KRITISCH: Auto-Update + Rollback fehlgeschlagen",
                        f"Update fehlgeschlagen: {update_msg}\n"
                        f"Rollback-Restore: {'OK' if restore_ok else 'FEHLER'}\n"
                        f"Server-Start: FEHLER — {start_msg}\n\n"
                        f"Manueller Eingriff noetig!",
                        level=NotifyLevel.CRITICAL,
                        ping_role=True,
                    )

        except Exception as e:
            logger.error(f"Rollback-Fehler: {e}", exc_info=True)
            if self.notifier:
                await self.notifier.send_admin(
                    "KRITISCH: Rollback fehlgeschlagen",
                    f"Fehler beim Rollback: {e}\nManueller Eingriff noetig!",
                    level=NotifyLevel.CRITICAL,
                    ping_role=True,
                )

    # ------------------------------------------------------------------
    # Minecraft: Daily Restart
    # ------------------------------------------------------------------

    async def _check_mc_daily_restart(self, now: datetime, server_id: str):
        """Taeglicher Restart fuer einen Minecraft-Server"""
        srv = self.mc_servers.get(server_id)
        if not srv:
            return

        target = now.replace(
            hour=self._mc_daily_restart_hour,
            minute=self._mc_daily_restart_minute,
            second=0, microsecond=0
        )

        # Innerhalb 1-Minuten-Fenster?
        if abs((now - target).total_seconds()) > 60:
            return

        # Heute schon neugestartet?
        last = self._mc_last_restart.get(server_id)
        if last and last.date() == now.date():
            return

        # Server laeuft?
        running = await srv.is_running()
        if not running:
            self._mc_last_restart[server_id] = now
            return

        # Spieler online? Verzoegern
        try:
            online, _ = await srv.get_player_count()
            if online > 0:
                logger.info(
                    f"[{server_id}] MC Daily Restart verzoegert: "
                    f"{online} Spieler online"
                )
                return
        except Exception:
            pass

        logger.info(f"[{server_id}] MC Daily Restart startet...")
        self._mc_last_restart[server_id] = now

        try:
            # Backup vor Restart
            if self._mc_backup_before_restart:
                mgr = self.mc_backup_mgrs.get(server_id)
                if mgr:
                    try:
                        await srv.rcon_command("save-all")
                        await asyncio.sleep(5)
                    except Exception:
                        pass
                    success, msg, _ = await mgr.create_backup(
                        name="pre-restart", created_by="scheduler"
                    )
                    if success:
                        logger.info(f"[{server_id}] Pre-Restart Backup: {msg}")

            # In-Game Warnung
            try:
                await srv.rcon_command("say Server wird in 2 Minuten neugestartet!")
                await asyncio.sleep(60)
                await srv.rcon_command("say Server wird in 1 Minute neugestartet!")
                await asyncio.sleep(50)
                await srv.rcon_command("say Server startet JETZT neu!")
                await asyncio.sleep(10)
            except Exception as e:
                logger.debug(f"[{server_id}] MC Restart-Warnung fehlgeschlagen: {e}")

            success, msg = await srv.restart()

            if self.notifier:
                if success:
                    await self.notifier.send_admin(
                        f"MC {srv.display_name} neugestartet",
                        f"Taeglicher Restart um {self._mc_daily_restart_hour:02d}:"
                        f"{self._mc_daily_restart_minute:02d}",
                        NotifyLevel.SUCCESS,
                    )
                else:
                    await self.notifier.send_admin(
                        f"MC {srv.display_name} Restart fehlgeschlagen",
                        msg,
                        NotifyLevel.ERROR,
                    )

        except Exception as e:
            logger.error(f"[{server_id}] MC Daily Restart Fehler: {e}")

    # ------------------------------------------------------------------
    # Minecraft: Auto-Backup
    # ------------------------------------------------------------------

    async def _check_mc_auto_backup(self, now: datetime, server_id: str):
        """Auto-Backup fuer einen Minecraft-Server"""
        srv = self.mc_servers.get(server_id)
        mgr = self.mc_backup_mgrs.get(server_id)
        if not srv or not mgr:
            return

        interval = timedelta(hours=self._mc_auto_backup_interval_hours)
        last = self._mc_last_backup.get(server_id)
        if last and (now - last) < interval:
            return

        # Nur wenn Server laeuft
        running = await srv.is_running()
        if not running:
            return

        logger.info(f"[{server_id}] MC Auto-Backup startet...")
        self._mc_last_backup[server_id] = now

        try:
            # Auto-Save ausloesen via RCON
            try:
                await srv.rcon_command("save-all")
                await asyncio.sleep(5)
            except Exception as e:
                logger.debug(f"[{server_id}] save-all vor Backup fehlgeschlagen: {e}")

            success, msg, backup_path = await mgr.create_backup(
                name="auto-backup", created_by="scheduler"
            )

            if success and backup_path:
                logger.info(f"[{server_id}] MC Auto-Backup erfolgreich: {msg}")

                # OneDrive Upload (optional)
                if self.onedrive and self.onedrive.enabled:
                    try:
                        cloud_ok, cloud_msg = await self.onedrive.upload(
                            str(backup_path),
                            remote_subdir=f"MinecraftBackups/{server_id}"
                        )
                        if cloud_ok:
                            logger.info(f"[{server_id}] MC Cloud-Backup: {cloud_msg}")
                    except Exception as e:
                        logger.debug(f"[{server_id}] MC Cloud-Backup Fehler: {e}")
            else:
                logger.error(f"[{server_id}] MC Auto-Backup fehlgeschlagen: {msg}")

        except Exception as e:
            logger.error(f"[{server_id}] MC Auto-Backup Fehler: {e}")

    # ------------------------------------------------------------------
    # Minecraft: Update Check
    # ------------------------------------------------------------------

    async def _check_mc_update(self, now: datetime, server_id: str):
        """Periodischer Update-Check fuer einen Minecraft-Server"""
        checker = self.mc_update_checkers.get(server_id)
        if not checker:
            return

        interval = timedelta(hours=self._mc_update_check_interval_hours)
        last = self._mc_last_update_check.get(server_id)
        if last and (now - last) < interval:
            return

        self._mc_last_update_check[server_id] = now

        try:
            available, info = await checker.check()

            if available:
                srv = self.mc_servers.get(server_id)
                label = srv.display_name if srv else f"MC {server_id}"

                if self.notifier:
                    await self.notifier.send_admin(
                        f"MC {label} — Paper-Update verfuegbar",
                        f"Aktueller Build: {info.get('current_build', '?')}\n"
                        f"Neuester Build: {info.get('latest_build', '?')}\n"
                        f"Verwende `/mc config update` zum Installieren.",
                        NotifyLevel.WARNING,
                    )

                if self.email_notifier:
                    await self.email_notifier.send_update_available(
                        str(info.get("current_build", "?")),
                        str(info.get("latest_build", "?")),
                        server_label=f"MC {label}",
                    )

        except Exception as e:
            logger.debug(f"[{server_id}] MC Update-Check Fehler: {e}")

    # ------------------------------------------------------------------
    # Minecraft: Config Backup
    # ------------------------------------------------------------------

    async def _check_mc_config_backup(self, now: datetime, server_id: str):
        """Taegliches Backup der server.properties"""
        mgr = self.mc_settings_mgrs.get(server_id)
        if not mgr:
            return

        # Nur zur konfigurierten Stunde
        if now.hour != self._mc_config_backup_hour or now.minute > 1:
            return

        # Heute schon gesichert?
        last = self._mc_last_config_backup.get(server_id)
        if last and last.date() == now.date():
            return

        self._mc_last_config_backup[server_id] = now
        logger.info(f"[{server_id}] MC Config-Backup startet...")

        try:
            success, msg, _ = await mgr.save_settings(
                name="auto", created_by="scheduler"
            )

            if success:
                logger.info(f"[{server_id}] MC Config-Backup: {msg}")
            else:
                logger.error(f"[{server_id}] MC Config-Backup fehlgeschlagen: {msg}")

        except Exception as e:
            logger.error(f"[{server_id}] MC Config-Backup Fehler: {e}")

    # ------------------------------------------------------------------
    # Daily Report
    # ------------------------------------------------------------------

    async def _check_daily_report(self, now: datetime):
        """Send daily status report at configured hour"""
        if now.hour != self._daily_report_hour or now.minute != 0:
            return

        if self._last_daily_report and self._last_daily_report.date() == now.date():
            return

        self._last_daily_report = now

        try:
            report_parts = []

            # Server status
            if self.sat_server:
                running = await self.sat_server.is_running()
                server_dot = "🟢 Online" if running else "🔴 Offline"
                report_parts.append(f"**Server:** {server_dot}")

            # Health summary (last 24h)
            if self.health_checker:
                summary = self.health_checker.get_summary()
                report_parts.append(
                    f"**Crashes (24h):** {summary.get('crashes_last_hour', 0)} | "
                    f"**Gesamt:** {summary.get('crashes_total', 0)}"
                )

            # Performance averages
            perf = getattr(self.bot, 'perf_monitor', None)
            if perf:
                avg = perf.get_averages(minutes=1440)  # 24h
                peak = perf.get_peak(minutes=1440)
                if avg.get('samples', 0) > 0:
                    report_parts.append(
                        f"**Performance (24h Avg):** CPU {avg['cpu']:.1f}% | "
                        f"RAM {avg['ram']:.1f}% | Disk {avg['disk']:.1f}%"
                    )
                    report_parts.append(
                        f"**Peak:** CPU {peak['cpu']:.1f}% | "
                        f"RAM {peak['ram']:.1f}% | Disk {peak['disk']:.1f}%"
                    )

            # Player activity
            if self.player_tracker:
                online = self.player_tracker.get_online_players()
                all_stats = self.player_tracker.get_all_stats()
                report_parts.append(
                    f"**Spieler:** {len(online)} online | "
                    f"{len(all_stats)} registriert"
                )

            # Backup status
            if self.backup_manager:
                backups = self.backup_manager.list_backups()
                report_parts.append(f"**Backups:** {len(backups)} lokal")

            # Update status
            if self.update_checker:
                if self._pending_update:
                    report_parts.append("**Update:** \u26a0\ufe0f Verfuegbar!")
                else:
                    report_parts.append("**Update:** \u2705 Aktuell")

            report_text = "\n".join(report_parts)

            if self.notifier:
                await self.notifier.notify_daily_report(report_text)
                logger.info("Daily report sent")

        except Exception as e:
            logger.error(f"Daily report error: {e}", exc_info=True)

    # ------------------------------------------------------------------
    # Config Backup
    # ------------------------------------------------------------------

    async def _check_config_backup(self, now: datetime):
        """Daily backup of server-specific config files to OneDrive"""
        if not self.config_backup:
            return

        # Run at configured hour
        if now.hour != self._config_backup_hour or now.minute > 1:
            return

        # Already backed up today?
        if self._last_config_backup and self._last_config_backup.date() == now.date():
            return

        self._last_config_backup = now
        logger.info("Config backup starting...")

        try:
            success, msg = await self.config_backup.create_and_upload()

            if success:
                logger.info(f"Config backup erfolgreich: {msg}")
                if self.notifier:
                    await self.notifier.send_admin(
                        "Server-Config Backup erstellt",
                        msg,
                        NotifyLevel.SUCCESS,
                    )
            else:
                logger.error(f"Config backup fehlgeschlagen: {msg}")
                if self.notifier:
                    await self.notifier.send_admin(
                        "Config Backup FEHLGESCHLAGEN",
                        msg,
                        NotifyLevel.ERROR,
                    )
        except Exception as e:
            logger.error(f"Config backup error: {e}", exc_info=True)

    # ------------------------------------------------------------------
    # Weekly Report
    # ------------------------------------------------------------------

    async def _check_weekly_report(self, now: datetime):
        """Send weekly report on configured day/time"""
        if not self.player_tracker or not self.notifier:
            return

        # Check day and time
        if now.weekday() != self._weekly_report_day:
            return
        if now.hour != self._weekly_report_hour or now.minute != 0:
            return

        try:
            report_text = self.player_tracker.format_weekly_report()
            await self.notifier.notify_weekly_report(report_text)
            logger.info("Weekly report sent")
        except Exception as e:
            logger.error(f"Weekly report error: {e}")

    # ------------------------------------------------------------------
    # Slash Commands
    # ------------------------------------------------------------------

    @app_commands.command(name="scheduler",
                          description="Scheduler-Status und Konfiguration")
    @app_commands.check(is_admin)
    async def scheduler_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer()

        embed = discord.Embed(
            title="⏰ Scheduler Status",
            color=0x5865F2,
            timestamp=datetime.now(),
        )

        # Auto-Backup
        ab_status = "✅ Aktiv" if self._auto_backup_enabled else "❌ Deaktiviert"
        last_ab = (self._last_auto_backup.strftime("%d.%m. %H:%M")
                    if self._last_auto_backup else "Noch nicht")
        embed.add_field(
            name="Auto-Backup",
            value=(
                f"Status: {ab_status}\n"
                f"Intervall: Alle {self._auto_backup_interval_hours}h\n"
                f"Letztes: {last_ab}\n"
                f"Max lokal: {self._max_local_backups}"
            ),
            inline=True,
        )

        # Daily Restart
        dr_status = "✅ Aktiv" if self._daily_restart_enabled else "❌ Deaktiviert"
        dr_time = f"{self._daily_restart_hour:02d}:{self._daily_restart_minute:02d}"
        last_dr = (self._last_daily_restart.strftime("%d.%m. %H:%M")
                    if self._last_daily_restart else "Noch nicht")
        embed.add_field(
            name="Daily Restart",
            value=(
                f"Status: {dr_status}\n"
                f"Zeit: {dr_time} Uhr\n"
                f"Letzter: {last_dr}\n"
                f"Min Uptime: {self._min_uptime_for_restart_hours}h"
            ),
            inline=True,
        )

        # Update Check
        uc_status = "✅ Aktiv" if self._update_check_enabled else "❌ Deaktiviert"
        last_uc = (self._last_update_check.strftime("%d.%m. %H:%M")
                    if self._last_update_check else "Noch nicht")
        update_avail = ""
        if self.update_checker and self.update_checker.update_available:
            update_avail = "\n⚠️ **Update verfuegbar!**"
        embed.add_field(
            name="Update-Check",
            value=(
                f"Status: {uc_status}\n"
                f"Intervall: Alle {self._update_check_interval_hours}h\n"
                f"Letzter: {last_uc}{update_avail}"
            ),
            inline=True,
        )

        # Auto-Update
        au_status = "✅ Aktiv" if self._auto_update_enabled else "❌ Deaktiviert"
        pending = "⚠️ Update verfuegbar!" if self._pending_update else "Keins"
        last_au = (self._last_auto_update.strftime("%d.%m. %H:%M")
                    if self._last_auto_update else "Noch nicht")
        empty_req = "Ja" if self._auto_update_require_empty else "Nein"
        embed.add_field(
            name="Auto-Update",
            value=(
                f"Status: {au_status}\n"
                f"Install-Zeit: {self._auto_update_hour:02d}:00 Uhr\n"
                f"Nur wenn leer: {empty_req}\n"
                f"Pending: {pending}\n"
                f"Letztes: {last_au}"
            ),
            inline=True,
        )

        # OneDrive
        if self.onedrive:
            od = self.onedrive.get_status()
            od_status = "✅ Aktiv" if od["enabled"] else "❌ Deaktiviert"
            last_od = od["last_file"] or "Keins"
            embed.add_field(
                name="OneDrive Backup",
                value=(
                    f"Status: {od_status}\n"
                    f"Remote: {od['remote']}\n"
                    f"Max: {od['max_backups']}\n"
                    f"Letztes: {last_od}"
                ),
                inline=True,
            )

        # Minecraft Scheduler
        if self.mc_servers:
            mc_lines = []
            mc_restart = "✅" if self._mc_daily_restart_enabled else "❌"
            mc_backup = "✅" if self._mc_auto_backup_enabled else "❌"
            mc_update = "✅" if self._mc_update_check_enabled else "❌"
            mc_config = "✅" if self._mc_config_backup_enabled else "❌"
            mc_lines.append(
                f"Restart: {mc_restart} "
                f"{self._mc_daily_restart_hour:02d}:{self._mc_daily_restart_minute:02d}"
            )
            mc_lines.append(
                f"Backup: {mc_backup} "
                f"alle {self._mc_auto_backup_interval_hours}h"
            )
            mc_lines.append(f"Update-Check: {mc_update}")
            mc_lines.append(f"Config-Backup: {mc_config}")
            mc_lines.append(f"Server: {', '.join(self.mc_servers.keys())}")
            embed.add_field(
                name="Minecraft",
                value="\n".join(mc_lines),
                inline=True,
            )

        # Reports
        days = ["Montag", "Dienstag", "Mittwoch", "Donnerstag",
                "Freitag", "Samstag", "Sonntag"]
        daily_icon = "✅" if self._daily_report_enabled else "❌"
        daily_str = (
            f"Taeglich: {daily_icon} "
            f"({self._daily_report_hour:02d}:00)"
        )
        embed.add_field(
            name="Reports",
            value=(
                f"{daily_str}\n"
                f"Woche: {days[self._weekly_report_day]} {self._weekly_report_hour:02d}:00"
            ),
            inline=True,
        )

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="update_check",
                          description="Manuell nach Server-Updates suchen")
    @app_commands.check(is_admin)
    async def update_check_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer()

        if not self.update_checker:
            await interaction.followup.send("❌ Update Checker nicht konfiguriert.")
            return

        available, info = await self.update_checker.check()

        if available:
            embed = discord.Embed(
                title="📦 Update verfuegbar!",
                description=(
                    f"**Installiert:** Build {info.get('installed_buildid', '?')}\n"
                    f"**Verfuegbar:** Build {info.get('available_buildid', '?')}\n\n"
                    f"Verwende `/sat update` um zu aktualisieren."
                ),
                color=0xffa500,
            )
        else:
            embed = discord.Embed(
                title="✅ Server ist aktuell",
                description=(
                    f"Build: {info.get('installed_buildid', '?')}\n"
                    f"Geprueft: {info.get('checked_at', '?')[:16]}"
                ),
                color=0x00ff00,
            )

        await interaction.followup.send(embed=embed)

    # ------------------------------------------------------------------
    # Modpack-Update-Check (Phase 8h)
    # ------------------------------------------------------------------

    async def _check_modpack_update(self, now: datetime):
        """Periodischer Modpack-Update-Check via Modrinth/CurseForge"""
        updater = self.modpack_updater
        if not updater or not updater.enabled:
            return

        interval = timedelta(hours=self._modpack_check_interval_hours)
        if self._last_modpack_check and (now - self._last_modpack_check) < interval:
            return

        self._last_modpack_check = now

        try:
            available, info = await updater.check()

            if available and self.notifier:
                await self.notifier.send_admin(
                    "BMC Modpack-Update verfuegbar!",
                    f"**Aktuell:** {info.get('current', '?')}\n"
                    f"**Neu:** {info.get('latest', '?')}\n"
                    f"**Name:** {info.get('name', '?')}\n"
                    f"**Quelle:** {info.get('source', '?')}\n"
                    f"**Changelog:** {info.get('changelog_url', '?')}",
                    NotifyLevel.WARNING,
                )
                logger.info(
                    f"Modpack-Update: {info.get('current')} -> {info.get('latest')}"
                )
        except Exception as e:
            logger.debug(f"Modpack-Check Fehler: {e}")

    # ------------------------------------------------------------------
    # Scheduled Messages (Phase 8f)
    # ------------------------------------------------------------------

    async def _load_scheduled_messages(self) -> None:
        """Scheduled Messages aus JSON laden, vergangene einmalige loeschen"""
        try:
            if SCHEDULED_MESSAGES_FILE.exists():
                async with aiofiles.open(SCHEDULED_MESSAGES_FILE, "r", encoding="utf-8") as f:
                    data = json.loads(await f.read())
                self._scheduled_messages = data.get("messages", [])
                self._next_schedule_id = data.get("next_id", 1)

                # Vergangene einmalige Messages entfernen
                now = datetime.now(BERLIN_TZ)
                before = len(self._scheduled_messages)
                self._scheduled_messages = [
                    m for m in self._scheduled_messages
                    if m.get("repeat", "einmalig") != "einmalig"
                    or datetime.fromisoformat(m["next_run"]) > now
                ]
                removed = before - len(self._scheduled_messages)
                if removed > 0:
                    await self._save_scheduled_messages()
                    logger.info(f"Scheduled Messages: {removed} vergangene einmalige entfernt")

                logger.info(f"Scheduled Messages geladen: {len(self._scheduled_messages)} aktiv")
            else:
                self._scheduled_messages = []
                self._next_schedule_id = 1
        except Exception as e:
            logger.error(f"Scheduled Messages laden fehlgeschlagen: {e}")
            self._scheduled_messages = []

    async def _save_scheduled_messages(self) -> None:
        """Scheduled Messages in JSON speichern"""
        try:
            SCHEDULED_MESSAGES_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "messages": self._scheduled_messages,
                "next_id": self._next_schedule_id,
            }
            async with aiofiles.open(SCHEDULED_MESSAGES_FILE, "w", encoding="utf-8") as f:
                await f.write(json.dumps(data, indent=2, ensure_ascii=False))
        except Exception as e:
            logger.error(f"Scheduled Messages speichern fehlgeschlagen: {e}")

    def _parse_time(self, zeit: str) -> Optional[datetime]:
        """Parst relative ('2h', '30m') oder absolute ('20:00', '2026-02-21 18:00') Zeitangaben.

        Gibt eine timezone-aware datetime (Europe/Berlin) zurueck.
        """
        now = datetime.now(BERLIN_TZ)

        # Relative Zeit: 2h, 30m, 1h30m, etc.
        rel_match = re.match(r'^(?:(\d+)h)?(?:(\d+)m)?$', zeit.strip())
        if rel_match and (rel_match.group(1) or rel_match.group(2)):
            hours = int(rel_match.group(1) or 0)
            minutes = int(rel_match.group(2) or 0)
            if hours == 0 and minutes == 0:
                return None
            return now + timedelta(hours=hours, minutes=minutes)

        # Absolute Zeit: HH:MM (heute oder morgen)
        time_match = re.match(r'^(\d{1,2}):(\d{2})$', zeit.strip())
        if time_match:
            hour = int(time_match.group(1))
            minute = int(time_match.group(2))
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
                return target
            return None

        # Absolute Zeit: YYYY-MM-DD HH:MM
        try:
            dt = datetime.strptime(zeit.strip(), "%Y-%m-%d %H:%M")
            return dt.replace(tzinfo=BERLIN_TZ)
        except ValueError:
            pass

        return None

    async def _check_scheduled_messages(self, now: datetime) -> None:
        """Prueft ob Scheduled Messages gesendet werden muessen"""
        if not self._scheduled_messages:
            return

        now_tz = datetime.now(BERLIN_TZ)
        to_remove = []
        changed = False

        for msg in self._scheduled_messages:
            try:
                next_run = datetime.fromisoformat(msg["next_run"])
                if next_run > now_tz:
                    continue

                # Nachricht senden
                channel = self.bot.get_channel(msg["channel_id"])
                if channel:
                    await channel.send(msg["message"])
                    logger.info(f"Scheduled Message #{msg['id']} gesendet in #{channel.name}")
                else:
                    logger.warning(f"Scheduled Message #{msg['id']}: Channel {msg['channel_id']} nicht gefunden")

                # Wiederholung oder entfernen
                repeat = msg.get("repeat", "einmalig")
                if repeat == "taeglich":
                    next_dt = next_run + timedelta(days=1)
                    msg["next_run"] = next_dt.isoformat()
                    changed = True
                elif repeat == "woechentlich":
                    next_dt = next_run + timedelta(weeks=1)
                    msg["next_run"] = next_dt.isoformat()
                    changed = True
                else:
                    to_remove.append(msg["id"])

            except Exception as e:
                logger.error(f"Scheduled Message #{msg.get('id', '?')} Fehler: {e}")

        if to_remove:
            self._scheduled_messages = [
                m for m in self._scheduled_messages if m["id"] not in to_remove
            ]
            changed = True

        if changed:
            await self._save_scheduled_messages()

    # ------------------------------------------------------------------
    # Scheduled Messages Commands
    # ------------------------------------------------------------------

    schedule_grp = app_commands.Group(
        name="schedule", description="Geplante Nachrichten verwalten"
    )

    @schedule_grp.command(
        name="add", description="Geplante Nachricht erstellen"
    )
    @app_commands.check(is_admin)
    @app_commands.describe(
        nachricht="Die Nachricht die gesendet werden soll",
        zeit="Zeitpunkt: relativ (2h, 30m) oder absolut (20:00, 2026-02-21 18:00)",
        channel="Ziel-Channel (Standard: aktueller Channel)",
        wiederholung="einmalig, taeglich oder woechentlich",
    )
    @app_commands.choices(wiederholung=[
        app_commands.Choice(name="Einmalig", value="einmalig"),
        app_commands.Choice(name="Taeglich", value="taeglich"),
        app_commands.Choice(name="Woechentlich", value="woechentlich"),
    ])
    async def schedule_add(self, interaction: discord.Interaction,
                           nachricht: str,
                           zeit: str,
                           channel: Optional[discord.TextChannel] = None,
                           wiederholung: str = "einmalig"):
        await interaction.response.defer(ephemeral=True)

        # Rate-Limit pruefen
        active_count = len(self._scheduled_messages)
        if active_count >= MAX_ACTIVE_SCHEDULES:
            await interaction.followup.send(
                f"Maximale Anzahl aktiver Schedules erreicht ({MAX_ACTIVE_SCHEDULES}).",
                ephemeral=True,
            )
            return

        # Nachrichtenlaenge validieren (Discord-Limit: 2000 Zeichen)
        if len(nachricht) > 2000:
            await interaction.followup.send(
                "Nachricht darf maximal 2000 Zeichen lang sein.",
                ephemeral=True,
            )
            return

        # Zeit parsen
        target_time = self._parse_time(zeit)
        if not target_time:
            await interaction.followup.send(
                "Ungueltiges Zeitformat. Beispiele: `2h`, `30m`, `20:00`, `2026-02-21 18:00`",
                ephemeral=True,
            )
            return

        # Ziel-Channel bestimmen
        target_channel = channel or interaction.channel
        if not target_channel:
            await interaction.followup.send(
                "Kein Ziel-Channel gefunden.", ephemeral=True
            )
            return

        # Schedule erstellen
        async with self._schedule_lock:
            schedule_id = self._next_schedule_id
            self._next_schedule_id += 1

            entry = {
                "id": schedule_id,
                "message": nachricht,
                "channel_id": target_channel.id,
                "channel_name": getattr(target_channel, 'name', str(target_channel.id)),
                "next_run": target_time.isoformat(),
                "repeat": wiederholung,
                "created_by": str(interaction.user),
                "created_at": datetime.now(BERLIN_TZ).isoformat(),
            }
            self._scheduled_messages.append(entry)
            await self._save_scheduled_messages()

        time_str = target_time.strftime("%d.%m.%Y %H:%M")
        repeat_str = {"einmalig": "Einmalig", "taeglich": "Taeglich", "woechentlich": "Woechentlich"}
        embed = discord.Embed(
            title=f"Nachricht geplant (#{schedule_id})",
            color=0x00cc66,
        )
        embed.add_field(name="Nachricht", value=nachricht[:1024], inline=False)
        embed.add_field(name="Zeitpunkt", value=time_str, inline=True)
        embed.add_field(name="Channel", value=f"#{target_channel.name}" if hasattr(target_channel, 'name') else str(target_channel.id), inline=True)
        embed.add_field(name="Wiederholung", value=repeat_str.get(wiederholung, wiederholung), inline=True)
        embed.set_footer(text=f"von {interaction.user.display_name}")
        await interaction.followup.send(embed=embed, ephemeral=True)
        logger.info(f"Schedule #{schedule_id} erstellt von {interaction.user}: {zeit}")

    @schedule_grp.command(
        name="list", description="Alle geplanten Nachrichten anzeigen"
    )
    @app_commands.check(is_admin)
    async def schedule_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not self._scheduled_messages:
            await interaction.followup.send(
                "Keine geplanten Nachrichten.", ephemeral=True
            )
            return

        lines = []
        for msg in self._scheduled_messages:
            try:
                next_run = datetime.fromisoformat(msg["next_run"])
                time_str = next_run.strftime("%d.%m. %H:%M")
            except (ValueError, KeyError):
                time_str = "?"
            repeat = msg.get("repeat", "einmalig")
            repeat_icon = {"einmalig": "1x", "taeglich": "🔁T", "woechentlich": "🔁W"}
            ch_name = msg.get("channel_name", "?")
            text_preview = msg.get("message", "")[:50]
            if len(msg.get("message", "")) > 50:
                text_preview += "..."
            lines.append(
                f"**#{msg['id']}** — {time_str} ({repeat_icon.get(repeat, repeat)}) "
                f"→ #{ch_name}\n  {text_preview}"
            )

        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:4000] + "\n..."

        embed = discord.Embed(
            title=f"Geplante Nachrichten ({len(self._scheduled_messages)})",
            description=text,
            color=0x5865F2,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @schedule_grp.command(
        name="cancel", description="Geplante Nachricht loeschen"
    )
    @app_commands.check(is_admin)
    @app_commands.describe(id="Die ID der geplanten Nachricht")
    async def schedule_cancel(self, interaction: discord.Interaction,
                              id: int):
        await interaction.response.defer(ephemeral=True)

        async with self._schedule_lock:
            before = len(self._scheduled_messages)
            self._scheduled_messages = [
                m for m in self._scheduled_messages if m["id"] != id
            ]

            if len(self._scheduled_messages) < before:
                await self._save_scheduled_messages()
                await interaction.followup.send(
                    f"Schedule #{id} geloescht.", ephemeral=True
                )
                logger.info(f"Schedule #{id} geloescht von {interaction.user}")
            else:
                await interaction.followup.send(
                    f"Schedule #{id} nicht gefunden.", ephemeral=True
                )

    # ------------------------------------------------------------------
    # Error handler
    # ------------------------------------------------------------------

    async def cog_app_command_error(self, interaction: discord.Interaction,
                                     error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ Keine Berechtigung!", ephemeral=True
                )
        else:
            logger.error(f"Scheduler command error: {error}", exc_info=True)
            msg = f"❌ Fehler: {str(error)[:200]}"
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(SchedulerCog(bot))
