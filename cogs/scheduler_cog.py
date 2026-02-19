"""
Scheduler Cog - Scheduled Background Tasks
Handles: Auto-Backup, Daily Restart, Update Checks, Weekly Report
"""

import os
import asyncio
import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, timedelta
from typing import Optional

from utils.logger import get_logger
from utils.config import get_config
from utils.permissions import is_admin
from modules.notifications.discord_notifier import NotifyLevel

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

    # ------------------------------------------------------------------
    # Cog lifecycle
    # ------------------------------------------------------------------

    async def cog_load(self):
        """Start all scheduled tasks when cog loads"""
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

            # Hol den Game-Chat oder Admin-Channel für Warnungen
            game_ch = None
            game_ch_id = int(os.environ.get("GAME_CHAT_CHANNEL_ID", 0))
            admin_ch_id = int(os.environ.get("ADMIN_LOG_CHANNEL_ID", 0))
            if game_ch_id:
                game_ch = self.bot.get_channel(game_ch_id)
            if not game_ch and admin_ch_id:
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
        if self.notifier and self.notifier.game_channel:
            await self.notifier.game_channel.send(
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
                            f"\n\nAuto-Update ist aktiv und wird um "
                            f"{self._auto_update_hour:02d}:00 installiert."
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
        """Install pending update at configured hour if conditions are met"""
        if not self.update_checker or not self.sat_server:
            return

        # Only at configured hour
        if now.hour != self._auto_update_hour or now.minute > 5:
            return

        # Already updated today?
        if self._last_auto_update and self._last_auto_update.date() == now.date():
            return

        # Check if server is running
        running = await self.sat_server.is_running()

        # If require_empty, check players
        if running and self._auto_update_require_empty:
            if self.health_checker and self.health_checker.status.players_online > 0:
                logger.info(
                    f"Auto-Update delayed: "
                    f"{self.health_checker.status.players_online} players online"
                )
                return

        logger.info("Auto-Update starting...")
        self._last_auto_update = now

        try:
            if self.notifier:
                await self.notifier.send_admin(
                    "Auto-Update gestartet",
                    "Server wird fuer Update gestoppt...",
                    level=NotifyLevel.WARNING if running else NotifyLevel.INFO,
                )

            # 1. Backup before update
            if running and self.backup_manager:
                try:
                    await self.sat_api.save_game()
                    await asyncio.sleep(5)
                except Exception as e:
                    logger.warning(f"save_game() vor Auto-Update fehlgeschlagen: {e}")

                success, msg, backup_path = await self.backup_manager.create_backup(
                    name="pre-update", created_by="auto-update"
                )
                if success:
                    logger.info(f"Pre-update backup: {backup_path.name if backup_path else msg}")

            # 2. Stop server if running
            was_running = running
            if running:
                # Send in-game warning via console command
                try:
                    await self.sat_api.run_command(
                        "ServerChat Server wird in 2 Minuten fuer ein Update neu gestartet!"
                    )
                except Exception:
                    pass  # Chat command might not be supported
                await asyncio.sleep(120)  # 2 min warning

                stop_ok, stop_msg = await self.sat_server.stop()
                if not stop_ok:
                    logger.error(f"Auto-Update: Server stop failed: {stop_msg}")
                    if self.notifier:
                        await self.notifier.send_admin(
                            "Auto-Update FEHLGESCHLAGEN",
                            f"Server konnte nicht gestoppt werden: {stop_msg}",
                            level=NotifyLevel.ERROR,
                            ping_role=True,
                        )
                    return
                await asyncio.sleep(10)

            # 3. Perform update
            update_ok, update_msg = await self.update_checker.perform_update(self.sat_server)

            if update_ok:
                self._pending_update = False
                logger.info(f"Auto-Update: {update_msg}")

                # 4. Restart server if it was running
                if was_running:
                    start_ok, start_msg = await self.sat_server.start()
                    if start_ok:
                        logger.info("Server nach Update gestartet")
                    else:
                        logger.error(f"Server start nach Update fehlgeschlagen: {start_msg}")

                server_state = "gestartet" if was_running else "war offline"
                if self.notifier:
                    await self.notifier.send_admin(
                        "Auto-Update erfolgreich",
                        f"{update_msg}\n\nServer {server_state}.",
                        NotifyLevel.SUCCESS,
                    )

                if self.email_notifier:
                    await self.email_notifier._send_email(
                        "✅ Auto-Update erfolgreich",
                        f"{update_msg}\nServer {server_state}.",
                        "auto_update_success",
                    )
            else:
                logger.error(f"Auto-Update fehlgeschlagen: {update_msg}")

                # Try to restart anyway if it was running
                if was_running:
                    await self.sat_server.start()

                if self.notifier:
                    await self.notifier.send_admin(
                        "Auto-Update FEHLGESCHLAGEN",
                        update_msg,
                        level=NotifyLevel.ERROR,
                        ping_role=True,
                    )

        except Exception as e:
            logger.error(f"Auto-Update error: {e}", exc_info=True)
            # Try to start server in case it was stopped
            try:
                await self.sat_server.start()
            except Exception:
                pass

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
