"""
Vormals monitor_bot.py / Service monitor-bot. Rolle: Health/Monitoring, Scheduler, Notifications, MC-Server-Watch.
Recon - Health Monitoring, Dashboard & Auto-Tasks
Bot 2 of 2 - Background monitoring and scheduled tasks

Features:
  - Health checks every 2 min (crash detection + auto-restart)
  - System performance monitoring every 5 min
  - Status dashboard embed every 10 min
  - Voice channel stats every 5 min
  - Player join/leave tracking
  - Auto-backup every 6h + OneDrive
  - Daily scheduled restart with countdown
  - Update checking via SteamCMD
  - Discord + Email notifications
  - Weekly player report

Cogs: monitor_cog.py, scheduler_cog.py
"""

import sys
import json
import re
import asyncio
import os
import tracemalloc
import discord
from discord.ext import commands, tasks
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Optional

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.config import load_env, get_env, get_config, server_ids
from utils.logger import get_logger
from utils.formatting import format_uptime, format_bytes, status_emoji
from utils.async_tasks import track_task

from modules.satisfactory.server import SatisfactoryServer
from modules.satisfactory.api_client import SatisfactoryAPI
from modules.monitoring.health_check import HealthChecker, ServerState
from modules.monitoring.performance import PerformanceMonitor, PerformanceThresholds
from modules.monitoring.player_tracker import PlayerTracker
from modules.monitoring.sat_log_players import SatLogPlayerParser
from modules.monitoring.status_panel import ServerLine, build_status_panel
from modules.monitoring.update_checker import UpdateChecker
from modules.notifications.discord_notifier import DiscordNotifier, NotifyLevel
from modules.notifications.email_notifier import EmailNotifier
from modules.backup.backup_manager import BackupManager
from modules.backup.onedrive_backup import OneDriveBackup
from modules.backup.config_backup import ConfigBackup
from modules.monitoring.optimizer import ServerOptimizer
from modules.monitoring.stats_tracker import StatsTracker
from modules.satisfactory.savegame_analyzer import SavegameAnalyzer, WorldStats
from modules.monitoring.crash_replay import CrashReplay
from modules.monitoring.player_ip_tracker import PlayerIPTracker
from modules.monitoring.login_audit import LoginAudit
from modules.monitoring.auto_cleanup import AutoCleanup
from modules.monitoring.selftest import SelfTest
from modules.monitoring.savegame_protection import SavegameProtection
from modules.channel_snapshot import write_snapshot as write_channel_snapshot
from modules.monitoring.graceful_degradation import GracefulDegradation
from modules.monitoring.steam_changelog import SteamChangelog
from modules.config_validator import ConfigValidator
from modules.minecraft.server import MinecraftServer
from modules.minecraft.chat_bridge import MinecraftChatBridge
from modules.minecraft.settings_backup import MinecraftSettingsBackup
from modules.minecraft.update_checker import MinecraftUpdateChecker
from modules.monitoring.web_status import WebStatusGenerator
from modules.monitoring.status_writer import StatusWriter
from modules.minecraft.modpack_updater import ModpackUpdater
from modules.minecraft.update_manager import UpdateManager
from modules.monitoring.stats_collector import StatsCollector
from utils.selftest import execute_selftest
from utils.shutdown import register_cleanup, setup_signal_handlers
from modules.monitoring.health_checker import HealthAutoRestart
from modules.monitoring.service_watchdog import ServiceWatchdog
from modules.system.disk_guard import DiskGuard
from modules.network.duckdns_monitor import DuckDNSMonitor
from modules.network.port_monitor import PortMonitor
from modules.security.ssl_monitor import SSLMonitor
from modules.security.fail2ban import Fail2BanManager
from modules.backup.integrity import BackupIntegrity
from modules.database.db_manager import init_db, close_db, check_integrity, DBHelper
from modules.guild_context import get_primary_guild_id, GuildConfig
from modules.database.json_importer import import_all, check_import_needed
from modules.security.ban_manager import BanManager
from modules.database.maintenance import DatabaseMaintenance
from modules.system.package_checker import PackageChecker
from utils.embeds import (
    COLOR_WARNING,
    error_embed,
    info_embed,
    success_embed,
    warning_embed,
)
from utils.ui_kit import rel_time, status_dot, subtext
from utils import loop_guard

load_env()

# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------

TOKEN = get_env("DISCORD_TOKEN_WATCHDOG")
GUILD_ID = get_primary_guild_id()  # zentralisiert via Multi-Tenant-Resolver (Phase 0.5)
ADMIN_LOG_CHANNEL_ID = get_env("ADMIN_LOG_CHANNEL_ID", cast=int, default=0)
STATUS_EMBED_CHANNEL_ID = get_env("STATUS_EMBED_CHANNEL_ID", cast=int, default=0)
VOICE_STATS_CATEGORY_ID = get_env("VOICE_STATS_CATEGORY_ID", cast=int, default=0)
OWNER_ID = get_env("OWNER_ID", default=0, cast=int)
NOTIFY_ROLE_ID = get_env("NOTIFY_ROLE_ID", cast=int, default=0)

# Email config
SMTP_HOST = get_env("SMTP_HOST", "")
SMTP_PORT = get_env("SMTP_PORT", 587, cast=int)
SMTP_USER = get_env("SMTP_USER", "")
SMTP_PASS = get_env("SMTP_PASS", "")
EMAIL_FROM = get_env("EMAIL_FROM", "")
EMAIL_TO = get_env("EMAIL_TO", "")
EMAIL_ENABLED = get_env("EMAIL_ENABLED", False, cast=bool)

# OneDrive config
ONEDRIVE_REMOTE = get_env("ONEDRIVE_REMOTE", "onedrive")
ONEDRIVE_PATH = get_env("ONEDRIVE_PATH", "SatisfactoryBackups")
ONEDRIVE_ENABLED = get_env("ONEDRIVE_ENABLED", False, cast=bool)

logger = get_logger("recon_bot")
config = get_config()

# ------------------------------------------------------------------
# Bot Setup
# ------------------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix="!", intents=intents, help_command=None,
    allowed_mentions=discord.AllowedMentions.none(),
)

# ------------------------------------------------------------------
# Service Initialization
# ------------------------------------------------------------------

# Game server interfaces
sat_server = SatisfactoryServer(
    service_name=get_env("SATISFACTORY_SERVICE", "satisfactory.service"),
    server_user=get_env("SATISFACTORY_USER", "satisfactory"),
    server_path=get_env("SATISFACTORY_SERVER_PATH",
                        "/home/satisfactory/SatisfactoryDedicatedServer"),
)

sat_api = SatisfactoryAPI(
    host=get_env("API_HOST", "127.0.0.1"),
    port=get_env("API_PORT", 7777, cast=int),
    token=get_env("API_TOKEN"),
    verify_ssl=get_env("API_VERIFY_SSL", False, cast=bool),
)

# Monitoring services
health_checker = HealthChecker(
    server=sat_server, api=sat_api,
    auto_restart=config.get("auto_restart", True),
    restart_delay=config.get("restart_delay", 30),
)

thresholds_cfg = config.get("thresholds", {})
perf_monitor = PerformanceMonitor(
    thresholds=PerformanceThresholds(
        cpu_warning=thresholds_cfg.get("cpu_warning", 80),
        ram_warning=thresholds_cfg.get("ram_warning", 85),
        disk_warning=thresholds_cfg.get("disk_warning", 90),
    )
)

player_tracker = PlayerTracker(data_dir=str(PROJECT_ROOT / "data"))

update_checker = UpdateChecker(
    steamcmd_path=get_env("STEAMCMD_PATH", "/usr/games/steamcmd"),
    install_dir=get_env("SATISFACTORY_SERVER_PATH",
                        "/home/satisfactory/SatisfactoryDedicatedServer"),
    server_user=get_env("SATISFACTORY_USER", "satisfactory"),
)

# Notification services
notifier = DiscordNotifier(bot)

email_notifier = EmailNotifier(
    smtp_host=SMTP_HOST, smtp_port=SMTP_PORT,
    username=SMTP_USER, password=SMTP_PASS,
    from_addr=EMAIL_FROM, to_addr=EMAIL_TO,
    enabled=EMAIL_ENABLED,
)

# Backup services
backup_manager = BackupManager(
    savegame_path=Path(get_env("SATISFACTORY_SAVE_PATH",
                        "/home/satisfactory/.config/Epic/FactoryGame/Saved/SaveGames")),
    backup_path=Path(get_env("BACKUP_PATH", str(PROJECT_ROOT / "backups"))),
)

onedrive_backup = OneDriveBackup(
    remote_name=ONEDRIVE_REMOTE,
    remote_path=ONEDRIVE_PATH,
    max_cloud_backups=config.get("max_cloud_backups", 10),
    enabled=ONEDRIVE_ENABLED,
)

# Config backup (server-specific files to OneDrive)
# Phase 8d: Konfigurierbare Rotation + optionale Verschluesselung
_backup_cfg = config.get("backup", {})
config_backup = ConfigBackup(
    project_root=PROJECT_ROOT,
    onedrive_backup=onedrive_backup,
    remote_path="Backups/ServerConfig",
    max_backups=_backup_cfg.get("config_max_count", 10),
    encrypt=_backup_cfg.get("config_encrypt", False),
)

# Stats tracker (persisted history for reports)
stats_tracker = StatsTracker(server_type="sat")

# Server optimizer
optimizer = ServerOptimizer(config)

# Savegame analyzer for building/power stats
savegame_analyzer = SavegameAnalyzer(
    savegame_path=Path(get_env("SATISFACTORY_SAVE_PATH",
                        "/home/satisfactory/.config/Epic/FactoryGame/Saved/SaveGames")),
    cache_dir=PROJECT_ROOT / "data" / "analyzer_cache",
)

# Crash replay - captures log context on crashes
crash_replay = CrashReplay(
    log_path=sat_server.server_path / "FactoryGame" / "Saved" / "Logs" / "FactoryGame.log",
    replay_dir=PROJECT_ROOT / "data" / "crash_replays",
    context_lines=50,
    max_replays=20,
)

# Player IP tracker - maps player names to IPs for kick/ban
player_ip_tracker = PlayerIPTracker(
    game_type="sat",
    game_ports=[7777, 7778, 8888, 8889],
)

# Login Audit (Phase 4a)
login_audit = LoginAudit(
    known_ips=config.get("login_audit", {}).get("known_ips", []),
)

# Auto-Cleanup (Phase 4c + 5)
auto_cleanup = AutoCleanup(config.get("auto_cleanup", {}))

# Savegame Protection (Phase 11)
savegame_protection = SavegameProtection(config.get("savegame_protection", {}))

# Graceful Degradation (Phase 10)
degradation = GracefulDegradation()
degradation.register("satisfactory_api", retry_interval=120)
degradation.register("onedrive", retry_interval=600)
degradation.register("email", retry_interval=300)
degradation.register("steamcmd", retry_interval=600)

# Steam Changelog (Phase 7)
steam_changelog = SteamChangelog()

# Self-Test (Phase 8b) — initialized after bot is ready
selftest = None  # Created in on_ready with bot reference

# Config Validation (Phase 9b)
validator = ConfigValidator()
valid, validation_errors = validator.validate_all()
if not valid:
    logger.error("Config validation failed!")
    for err in validation_errors:
        logger.error(f"  {err}")
else:
    warnings = [e for e in validation_errors if e.severity == "warning"]
    if warnings:
        for w in warnings:
            logger.warning(f"  {w}")
    else:
        logger.info("Config validation passed")

# Attach services to bot for cog access
bot.sat_server = sat_server
bot.sat_api = sat_api
bot.health_checker = health_checker
bot.perf_monitor = perf_monitor
bot.player_tracker = player_tracker
bot.update_checker = update_checker
bot.notifier = notifier
bot.email_notifier = email_notifier
bot.backup_manager = backup_manager
bot.onedrive_backup = onedrive_backup
bot.config_backup = config_backup
bot.stats_tracker = stats_tracker
bot.optimizer = optimizer
bot.savegame_analyzer = savegame_analyzer
bot.crash_replay = crash_replay
bot.player_ip_tracker = player_ip_tracker
bot.login_audit = login_audit
bot.auto_cleanup = auto_cleanup
bot.savegame_protection = savegame_protection
bot.degradation = degradation
bot.steam_changelog = steam_changelog
bot.config_validator = validator

# ------------------------------------------------------------------
# Phase 1: Neue Monitoring/Security Module (F27, F31-33, F49-52)
# ------------------------------------------------------------------

health_auto_restart = HealthAutoRestart(bot)
bot.health_auto_restart = health_auto_restart

service_watchdog = ServiceWatchdog(bot)
bot.service_watchdog = service_watchdog

disk_guard = DiskGuard(bot)
bot.disk_guard = disk_guard

duckdns_monitor = DuckDNSMonitor(
    bot,
    domain=get_env("WEB_DOMAIN", "marco-satisfactory.duckdns.org"),
)
bot.duckdns_monitor = duckdns_monitor

port_monitor = PortMonitor(bot)
bot.port_monitor = port_monitor

ssl_monitor = SSLMonitor()
bot.ssl_monitor = ssl_monitor

fail2ban_mgr = Fail2BanManager()
bot.fail2ban_mgr = fail2ban_mgr

backup_integrity = BackupIntegrity()
bot.backup_integrity = backup_integrity

# F28-8: Ban-Manager (Boot-Restore + Ban-Expiry)
ban_manager = BanManager()
bot.ban_manager = ban_manager

# F63/F56: Database Maintenance (Backup + Retention)
db_maintenance = DatabaseMaintenance()
bot.db_maintenance = db_maintenance

# F42: Automatischer Paket-Update-Check (woechentlich)
package_checker = PackageChecker(bot)
bot.package_checker = package_checker

# ------------------------------------------------------------------
# Minecraft Multi-Server + Chat-Bridge
# ------------------------------------------------------------------

# Welche Server es gibt, steht in der ENV — siehe utils.config.server_ids.
# Vanilla wurde am 2026-08-14 stillgelegt; zurueck geht es ueber
# MC_SERVER_IDS=BMC,VANILLA plus den MC_VANILLA_*-Block, ohne Code-Aenderung.
MC_SERVER_IDS = server_ids("MC_SERVER_IDS", "BMC")
mc_servers: dict[str, MinecraftServer] = {}
mc_chat_bridges: dict[str, MinecraftChatBridge] = {}

for _mc_sid in MC_SERVER_IDS:
    _mc_srv = MinecraftServer(_mc_sid)
    if _mc_srv.enabled:
        mc_servers[_mc_sid] = _mc_srv
        logger.info(f"Minecraft-Server aktiviert: {_mc_srv.display_name} ({_mc_sid})")

bot.mc_servers = mc_servers

# F52: MC-Server-Ports dynamisch zum PortMonitor hinzufuegen
for _mc_sid, _mc_srv in mc_servers.items():
    # RCON-Port monitoren (bester Indikator ob Server laeuft)
    port_monitor.add_port(
        port=_mc_srv.rcon_port,
        label=f"MC {_mc_srv.display_name} RCON",
        host=_mc_srv.rcon_host,
    )
    logger.info(f"PortMonitor: MC {_mc_srv.display_name} RCON ({_mc_srv.rcon_host}:{_mc_srv.rcon_port}) registriert")

# Minecraft Backup-Manager (fuer Scheduler Auto-Backups)
if mc_servers:
    from modules.minecraft.backup import MinecraftBackupManager
    mc_backup_mgrs: dict[str, MinecraftBackupManager] = {}
    for _mc_sid, _mc_srv in mc_servers.items():
        mc_backup_mgrs[_mc_sid] = MinecraftBackupManager(
            savegame_path=_mc_srv.world_path,
            backup_path=_mc_srv.backup_path,
            max_backups=config.get("backup", {}).get("max_local", 20),
            server_id=_mc_sid,
        )
    bot.mc_backup_mgrs = mc_backup_mgrs

# Minecraft Player-Tracker (separate Instanz pro Server)
mc_player_trackers: dict[str, PlayerTracker] = {}
for _mc_sid in mc_servers:
    mc_player_trackers[_mc_sid] = PlayerTracker(
        data_dir=str(PROJECT_ROOT / "data" / f"mc_{_mc_sid.lower()}")
    )

bot.mc_player_trackers = mc_player_trackers

# Minecraft Settings-Backup Manager (server.properties Sicherung)
mc_settings_mgrs: dict[str, MinecraftSettingsBackup] = {}
for _mc_sid, _mc_srv in mc_servers.items():
    mc_settings_mgrs[_mc_sid] = MinecraftSettingsBackup(
        server_path=Path(_mc_srv.server_path),
        backup_dir=_mc_srv.backup_path / "settings",
        server_id=_mc_sid,
        max_backups=20,
    )
bot.mc_settings_mgrs = mc_settings_mgrs

# Minecraft Update-Checker (nur Paper-basierte Server)
mc_update_checkers: dict[str, MinecraftUpdateChecker] = {}
for _mc_sid, _mc_srv in mc_servers.items():
    # BMC ist NeoForge-basiert — kein Paper-Update-Check
    if _mc_sid.upper() == "BMC":
        continue
    mc_update_checkers[_mc_sid] = MinecraftUpdateChecker(
        server_path=Path(_mc_srv.server_path),
        server_id=_mc_sid,
        jar_name=getattr(_mc_srv, 'jar_name', 'server.jar'),
    )
bot.mc_update_checkers = mc_update_checkers

# Minecraft Stats-Tracker (separate Instanz pro Server)
mc_stats_trackers: dict[str, StatsTracker] = {}
for _mc_sid in mc_servers:
    mc_stats_trackers[_mc_sid] = StatsTracker(
        server_type="mc",
        server_id=_mc_sid.lower(),
    )
bot.mc_stats_trackers = mc_stats_trackers

# Minecraft Crash-Replay (Log-Kontext bei Crashes)
mc_crash_replays: dict[str, CrashReplay] = {}
for _mc_sid, _mc_srv in mc_servers.items():
    mc_crash_replays[_mc_sid] = CrashReplay(
        log_path=_mc_srv.log_path,
        replay_dir=PROJECT_ROOT / "data" / f"crash_replays_mc_{_mc_sid.lower()}",
        context_lines=50,
        max_replays=20,
    )
bot.mc_crash_replays = mc_crash_replays

# Minecraft IP-Tracker (Log-basiertes IP-Mapping + UFW-Bans)
mc_ip_trackers: dict[str, PlayerIPTracker] = {}
for _mc_sid in mc_servers:
    mc_ip_trackers[_mc_sid] = PlayerIPTracker(
        game_type="mc",
        game_ports=[25565],
    )
bot.mc_ip_trackers = mc_ip_trackers

# Chat-Bridge Callbacks (werden in on_ready mit Channels verbunden)


_NO_MENTIONS = discord.AllowedMentions.none()


async def _mc_on_chat(server_id: str, player: str, message: str):
    """MC Chat → Discord Channel"""
    srv = mc_servers.get(server_id)
    if not srv or not srv.game_chat_channel_id:
        return
    channel = bot.get_channel(srv.game_chat_channel_id)
    if channel:
        # Markdown/Mention-Injection verhindern
        safe_player = discord.utils.escape_markdown(player)
        safe_message = discord.utils.escape_markdown(message)
        await channel.send(f"**<{safe_player}>** {safe_message}", allowed_mentions=_NO_MENTIONS)


async def _mc_on_join(server_id: str, player: str):
    """MC Join → Discord Channel + Player-Tracker"""
    srv = mc_servers.get(server_id)
    if not srv or not srv.game_chat_channel_id:
        return
    channel = bot.get_channel(srv.game_chat_channel_id)
    if channel:
        safe_player = discord.utils.escape_markdown(player)
        await channel.send(f"**{safe_player}** ist dem Server beigetreten", allowed_mentions=_NO_MENTIONS)

    # Player-Tracker aktualisieren
    tracker = mc_player_trackers.get(server_id)
    if tracker:
        current = tracker.get_online_players() | {player}
        await tracker.update(current)


async def _mc_on_leave(server_id: str, player: str):
    """MC Leave → Discord Channel + Player-Tracker"""
    srv = mc_servers.get(server_id)
    if not srv or not srv.game_chat_channel_id:
        return
    channel = bot.get_channel(srv.game_chat_channel_id)
    if channel:
        safe_player = discord.utils.escape_markdown(player)
        await channel.send(f"**{safe_player}** hat den Server verlassen", allowed_mentions=_NO_MENTIONS)

    # Player-Tracker aktualisieren
    tracker = mc_player_trackers.get(server_id)
    if tracker:
        current = tracker.get_online_players() - {player}
        await tracker.update(current)


async def _mc_on_advancement(server_id: str, player: str, advancement: str):
    """MC Advancement → Discord Channel"""
    srv = mc_servers.get(server_id)
    if not srv or not srv.game_chat_channel_id:
        return
    channel = bot.get_channel(srv.game_chat_channel_id)
    if channel:
        safe_player = discord.utils.escape_markdown(player)
        safe_advancement = discord.utils.escape_markdown(advancement)
        await channel.send(f"**{safe_player}** hat den Fortschritt **{safe_advancement}** erreicht!", allowed_mentions=_NO_MENTIONS)


async def _mc_on_death(server_id: str, player: str, death_msg: str):
    """MC Death → Discord Channel"""
    srv = mc_servers.get(server_id)
    if not srv or not srv.game_chat_channel_id:
        return
    channel = bot.get_channel(srv.game_chat_channel_id)
    if channel:
        safe_death_msg = discord.utils.escape_markdown(death_msg)
        await channel.send(f"{safe_death_msg}", allowed_mentions=_NO_MENTIONS)


# Chat-Bridge Instanzen erstellen
for _mc_sid, _mc_srv in mc_servers.items():
    bridge = MinecraftChatBridge(
        server_id=_mc_sid,
        log_path=_mc_srv.log_path,
        on_chat=_mc_on_chat,
        on_join=_mc_on_join,
        on_leave=_mc_on_leave,
        on_advancement=_mc_on_advancement,
        on_death=_mc_on_death,
    )
    mc_chat_bridges[_mc_sid] = bridge

bot.mc_chat_bridges = mc_chat_bridges

# ------------------------------------------------------------------
# Web-Status-Seite (Phase 8g)
# ------------------------------------------------------------------

_web_status_enabled = get_env("WEB_STATUS_ENABLED", "false").lower() == "true"
_web_status_path = get_env("WEB_STATUS_PATH", "/var/www/status")

web_status_generator = None
if _web_status_enabled:
    web_status_generator = WebStatusGenerator(
        output_path=_web_status_path,
        sat_server=sat_server,
        mc_servers=mc_servers,
        health_checker=health_checker,
        perf_monitor=perf_monitor,
        player_tracker=player_tracker,
    )
    logger.info(f"Web-Status-Seite aktiviert: {_web_status_path}")
else:
    logger.info("Web-Status-Seite deaktiviert (WEB_STATUS_ENABLED != true)")

bot.web_status_generator = web_status_generator

# ------------------------------------------------------------------
# Modpack-Update-Checker (Phase 8h) — pro MC-Server via from_env()
# ------------------------------------------------------------------

mc_modpack_updaters: dict[str, ModpackUpdater] = {}
for _mc_sid in mc_servers:
    _mpu = ModpackUpdater.from_env(server_id=_mc_sid)
    if _mpu.enabled:
        mc_modpack_updaters[_mc_sid] = _mpu
        logger.info(
            f"Modpack-Updater [{_mc_sid}] aktiviert: CurseForge "
            f"(Projekt: {_mpu.project_id}, File-ID: {_mpu.current_file_id}, "
            f"Version: {_mpu.current_version})"
        )

if not mc_modpack_updaters:
    logger.info("Kein Modpack-Updater aktiviert (ENV-Variablen nicht gesetzt)")

bot.mc_modpack_updaters = mc_modpack_updaters

# Abwaertskompatibilitaet: bot.modpack_updater zeigt auf BMC (falls vorhanden)
bot.modpack_updater = mc_modpack_updaters.get("BMC", ModpackUpdater())

# ------------------------------------------------------------------
# Stats Collector (Phase 13c) — Sammelt System/Server-Metriken
# ------------------------------------------------------------------

stats_collector = StatsCollector(interval=300)  # Alle 5 Minuten
bot.stats_collector = stats_collector

# StatusWriter: Schreibt Server-/Bot-Status als JSON fuer das Web-Dashboard
status_writer = StatusWriter(bot, interval=30)  # Alle 30 Sekunden
bot.status_writer = status_writer

# State - persistent status message ID
_status_message_id = None
_STATUS_MSG_FILE = PROJECT_ROOT / "data" / "status_message.json"

# State - downtime notification tracking (Phase 10b)
_consecutive_offline_checks = 0
_downtime_notified = False


def _load_status_message_id() -> Optional[int]:
    """Load persisted status message ID from disk"""
    try:
        if _STATUS_MSG_FILE.exists():
            with open(_STATUS_MSG_FILE, "r") as f:
                data = json.load(f)
            return data.get("message_id")
    except Exception as e:
        logger.debug(f"Status-Message-ID laden fehlgeschlagen: {e}")
    return None


def _save_status_message_id(msg_id: int):
    """Persist status message ID to disk"""
    try:
        _STATUS_MSG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_STATUS_MSG_FILE, "w") as f:
            json.dump({"message_id": msg_id, "channel_id": STATUS_EMBED_CHANNEL_ID}, f)
    except Exception as e:
        logger.error(f"Failed to save status message ID: {e}")

# ------------------------------------------------------------------
# Wire up callbacks
# ------------------------------------------------------------------


async def _on_crash(crash_event):
    # Capture crash replay FIRST (before log gets rotated)
    replay_file = await crash_replay.capture(crash_event.crash_number)

    await notifier.notify_crash(crash_event.crash_number)
    await email_notifier.send_crash_alert(crash_event.crash_number)
    player_tracker.close_all_sessions()
    stats_tracker.record_crash(crash_event.crash_number)

    # Crash-Loop Protection (Phase 11a)
    loop_result = await savegame_protection.record_crash()
    if loop_result.get("crash_loop"):
        # Disable auto-restart
        health_checker.auto_restart = False
        logger.warning("Crash loop detected — auto-restart disabled!")

    # Check savegame integrity after crash (Phase 11b)
    if hasattr(savegame_protection, 'check_save_integrity'):
        try:
            save_path = Path(get_env("SATISFACTORY_SAVE_PATH",
                "/home/satisfactory/.config/Epic/FactoryGame/Saved/SaveGames"))
            integrity = await savegame_protection.check_save_integrity(save_path)
            if not integrity["ok"]:
                logger.warning(f"Savegame integrity issue: {integrity['issues']}")
                # Send integrity warning to admin channel
                if ADMIN_LOG_CHANNEL_ID:
                    admin_ch = bot.get_channel(ADMIN_LOG_CHANNEL_ID)
                    if admin_ch:
                        embed = warning_embed(
                            title="⚠️ Savegame Integritaetsproblem",
                            description="\n".join(integrity["issues"]),
                        )
                        rollback_info = savegame_protection.get_rollback_info()
                        if rollback_info.get("last_known_good"):
                            info = rollback_info["last_known_good"]
                            embed.add_field(
                                name="Letztes gutes Save",
                                value=f"{info['name']} ({info['size_mb']} MB, vor {info['age_minutes']} Min)",
                                inline=False,
                            )
                        try:
                            await admin_ch.send(embed=embed)
                        except Exception as e:
                            logger.error(f"Failed to send integrity warning: {e}")
        except Exception as e:
            logger.debug(f"Integrity check error: {e}")

    # Post crash replay to admin channel
    if replay_file and ADMIN_LOG_CHANNEL_ID:
        channel = bot.get_channel(ADMIN_LOG_CHANNEL_ID)
        if channel:
            try:
                summary = crash_replay.get_summary()
                embed = error_embed(
                    title=f"🔍 Crash Replay #{crash_event.crash_number}",
                    description=(
                        f"Letzte {crash_replay.context_lines} Log-Zeilen vor dem Crash:\n"
                        f"```\n{summary}\n```"
                    ),
                )
                embed.set_footer(text="Vollständiges Log als Datei angehängt")

                file = discord.File(str(replay_file), filename=replay_file.name)
                await channel.send(embed=embed, file=file)
                logger.info(f"Crash replay posted: {replay_file.name}")
            except Exception as e:
                logger.error(f"Failed to post crash replay: {e}")


async def _on_recovery():
    await notifier.notify_server_online()


async def _on_restart_success(crash_event):
    await notifier.notify_restart_success(crash_event.crash_number)


async def _on_restart_failed(crash_event, reason):
    await notifier.notify_restart_failed(crash_event.crash_number, reason)
    await email_notifier.send_restart_failed(crash_event.crash_number, reason)


health_checker.on_crash = _on_crash
health_checker.on_recovery = _on_recovery
health_checker.on_restart_success = _on_restart_success
health_checker.on_restart_failed = _on_restart_failed
# Crash-Detection waehrend geplantem Update/Restart unterdruecken (nutzt die
# bestehende HAR-Suppression; perform_update ruft har.suppress("sat","main")).
health_checker.suppress_crash_check = lambda: health_auto_restart.is_suppressed("sat", "main")


async def _on_player_join(name):
    # No public join notification - status embed shows who's online
    # Only log to admin channel if: banned player/IP or new player

    if not ADMIN_LOG_CHANNEL_ID:
        return

    ip = player_ip_tracker.get_ip(name)
    ip_str = f"`{ip}`" if ip else "*unbekannt*"
    alerts = []
    is_new = False

    # 1. Check if name is on ban list
    bans = player_ip_tracker.get_all_bans()
    banned_names = [b["name"] for b in bans]
    if name in banned_names:
        alerts.append("\n🚫 **GEBANNTER SPIELER** versucht beizutreten!")

    # 2. Check if IP belongs to a banned player (ban evasion)
    if ip:
        for ban in bans:
            if ban["ip"] == ip and ban["name"] != name:
                alerts.append(
                    f"\n⚠️ **BAN-UMGEHUNG:** IP `{ip}` ist gebannt "
                    f"(Spieler: **{ban['name']}**)!"
                )

    # 3. Check if completely new player
    known_players = player_ip_tracker.get_all_mappings()
    is_new = name not in known_players

    if is_new:
        # Auto-add to whitelist on first join
        wl_mgr = getattr(bot, "whitelist_mgr", None)
        if wl_mgr:
            await wl_mgr.add(name, "Auto (Erster Beitritt)")
            logger.info(f"Auto-whitelisted new player: {name}")

    # Only notify if something noteworthy happened
    if not alerts and not is_new:
        return

    channel = bot.get_channel(ADMIN_LOG_CHANNEL_ID)
    if not channel:
        return

    try:
        if alerts:
            # Suspicious join - red embed + role ping
            desc = f"🚨 **{name}** ist beigetreten\nIP: {ip_str}"
            for alert in alerts:
                desc += alert
            embed = error_embed(
                description=desc,
            )
            await channel.send(
                content=f"<@&{NOTIFY_ROLE_ID}>" if NOTIFY_ROLE_ID else None,
                embed=embed,
            )
        elif is_new:
            # New player - blue embed, no ping
            embed = info_embed(
                description=(
                    f"🆕 Neuer Spieler: **{name}**\n"
                    f"IP: {ip_str}\n"
                    f"Automatisch zur Whitelist hinzugefügt."
                ),
            )
            await channel.send(embed=embed)
    except Exception as e:
        logger.error(f"Join log error: {e}")


async def _on_player_leave(name, duration_min):
    # No public or admin notification for normal leaves
    pass


player_tracker.on_join = _on_player_join
player_tracker.on_leave = _on_player_leave


async def _on_update_available(installed, available):
    await notifier.notify_update_available(installed, available)
    await email_notifier.send_update_available(installed, available)


update_checker.on_update_available = _on_update_available


# -- Graceful Degradation Callbacks (Phase 10) --

async def _on_service_failed(name, error):
    if ADMIN_LOG_CHANNEL_ID:
        channel = bot.get_channel(ADMIN_LOG_CHANNEL_ID)
        if channel:
            try:
                embed = warning_embed(
                    title=f"\u26a0\ufe0f Service ausgefallen: {name}",
                    description=f"Fehler: {error}\n\nBot laeuft weiter mit eingeschraenkter Funktionalitaet.\nAutomatischer Retry aktiv.",
                )
                await channel.send(embed=embed)
            except Exception as e:
                logger.debug(f"Discord-Channel-Send / Stat-Sample swallowed (B110-refactor 3.1): {e}")


async def _on_service_recovered(name):
    if ADMIN_LOG_CHANNEL_ID:
        channel = bot.get_channel(ADMIN_LOG_CHANNEL_ID)
        if channel:
            try:
                embed = success_embed(
                    title=f"\u2705 Service wiederhergestellt: {name}",
                )
                await channel.send(embed=embed)
            except Exception as e:
                logger.debug(f"Discord-Channel-Send / Stat-Sample swallowed (B110-refactor 3.1): {e}")


degradation.on_service_failed = _on_service_failed
degradation.on_service_recovered = _on_service_recovered


# -- Crash-Loop Protection Callback (Phase 11a) --

async def _on_crash_loop(result):
    if ADMIN_LOG_CHANNEL_ID:
        channel = bot.get_channel(ADMIN_LOG_CHANNEL_ID)
        if channel:
            try:
                rollback_info = savegame_protection.get_rollback_info()
                lkg = rollback_info.get("last_known_good")
                lkg_text = f"\n\nLetztes intaktes Save: `{lkg['name']}` ({lkg['size_mb']} MB, vor {lkg['age_minutes']} Min)" if lkg else ""

                embed = error_embed(
                    title="\U0001f6a8 CRASH LOOP ERKANNT!",
                    description=(
                        f"{result['crashes_in_window']} Crashes in {result['window_minutes']} Minuten.\n"
                        f"Auto-Restart wurde **deaktiviert**.\n\n"
                        f"Bitte manuell pruefen und mit `/sat start` neu starten."
                        f"{lkg_text}"
                    ),
                )
                content = f"<@&{NOTIFY_ROLE_ID}>" if NOTIFY_ROLE_ID else None
                await channel.send(content=content, embed=embed)
            except Exception as e:
                logger.error(f"Crash loop notification error: {e}")


savegame_protection.on_crash_loop = _on_crash_loop


# -- F50: Service-Watchdog Callbacks --

async def _on_service_down(service_name: str, label: str):
    if ADMIN_LOG_CHANNEL_ID:
        channel = bot.get_channel(ADMIN_LOG_CHANNEL_ID)
        if channel:
            try:
                embed = error_embed(
                    title=f"Service ausgefallen: {label}",
                    description=(
                        f"**Service:** `{service_name}`\n"
                        f"Status: nicht aktiv\n\n"
                        f"Automatischer Neustart wird versucht..."
                    ),
                )
                await channel.send(embed=embed)
            except Exception as e:
                logger.debug(f"Service-Down Notification Fehler: {e}")


async def _on_sw_restart_success(service_name: str, label: str):
    if ADMIN_LOG_CHANNEL_ID:
        channel = bot.get_channel(ADMIN_LOG_CHANNEL_ID)
        if channel:
            try:
                embed = success_embed(
                    title=f"Service neugestartet: {label}",
                    description=(
                        f"**Service:** `{service_name}`\n"
                        f"Automatischer Neustart war erfolgreich."
                    ),
                )
                await channel.send(embed=embed)
            except Exception as e:
                logger.debug(f"Service-Restart-Success Notification Fehler: {e}")


async def _on_sw_restart_failed(service_name: str, label: str, error_msg: str):
    if ADMIN_LOG_CHANNEL_ID:
        channel = bot.get_channel(ADMIN_LOG_CHANNEL_ID)
        if channel:
            try:
                embed = error_embed(
                    title=f"Service-Neustart FEHLGESCHLAGEN: {label}",
                    description=(
                        f"**Service:** `{service_name}`\n"
                        f"**Fehler:** {error_msg}\n\n"
                        f"Manuelles Eingreifen erforderlich!"
                    ),
                )
                await channel.send(embed=embed)
            except Exception as e:
                logger.debug(f"Service-Restart-Failed Notification Fehler: {e}")


async def _on_sw_cooldown_reached(service_name: str, label: str, restart_count: int):
    if ADMIN_LOG_CHANNEL_ID:
        channel = bot.get_channel(ADMIN_LOG_CHANNEL_ID)
        if channel:
            try:
                embed = warning_embed(
                    title=f"Restart-Cooldown erreicht: {label}",
                    description=(
                        f"**Service:** `{service_name}`\n"
                        f"**Restarts letzte Stunde:** {restart_count}\n\n"
                        f"Maximale Anzahl automatischer Restarts erreicht."
                    ),
                )
                await channel.send(embed=embed)
            except Exception as e:
                logger.debug(f"Cooldown-Reached Notification Fehler: {e}")


service_watchdog.on_service_down = _on_service_down
service_watchdog.on_restart_success = _on_sw_restart_success
service_watchdog.on_restart_failed = _on_sw_restart_failed
service_watchdog.on_cooldown_reached = _on_sw_cooldown_reached


# -- F49: Disk-Guard Callbacks --

async def _on_disk_warning(result: dict):
    if ADMIN_LOG_CHANNEL_ID:
        channel = bot.get_channel(ADMIN_LOG_CHANNEL_ID)
        if channel:
            try:
                embed = discord.Embed(
                    title="Speicherplatz-Warnung",
                    description=disk_guard.format_status(result),
                    color=disk_guard.get_embed_color(result.get("level", "warning")),
                    timestamp=datetime.now(),
                )
                await channel.send(embed=embed)
            except Exception as e:
                logger.debug(f"Disk-Warning Notification Fehler: {e}")


async def _on_disk_cleanup(result: dict):
    if ADMIN_LOG_CHANNEL_ID:
        channel = bot.get_channel(ADMIN_LOG_CHANNEL_ID)
        if channel:
            try:
                embed = discord.Embed(
                    title="Automatische Speicherbereinigung",
                    description=disk_guard.format_status(result),
                    color=disk_guard.get_embed_color(result.get("level", "cleanup")),
                    timestamp=datetime.now(),
                )
                await channel.send(embed=embed)
            except Exception as e:
                logger.debug(f"Disk-Cleanup Notification Fehler: {e}")


async def _on_disk_critical(result: dict):
    if ADMIN_LOG_CHANNEL_ID:
        channel = bot.get_channel(ADMIN_LOG_CHANNEL_ID)
        if channel:
            try:
                embed = error_embed(
                    title="KRITISCH: Speicherplatz fast voll!",
                    description=disk_guard.format_status(result),
                )
                content = f"<@&{NOTIFY_ROLE_ID}>" if NOTIFY_ROLE_ID else None
                await channel.send(content=content, embed=embed)
            except Exception as e:
                logger.debug(f"Disk-Critical Notification Fehler: {e}")


disk_guard.on_warning = _on_disk_warning
disk_guard.on_cleanup = _on_disk_cleanup
disk_guard.on_critical = _on_disk_critical


# -- F51: DuckDNS-Monitor Callbacks --

async def _on_duckdns_mismatch(result: dict):
    if ADMIN_LOG_CHANNEL_ID:
        channel = bot.get_channel(ADMIN_LOG_CHANNEL_ID)
        if channel:
            try:
                desc = duckdns_monitor.format_status(result)
                # Auto-Update versuchen falls Token gesetzt
                token = get_env("DUCKDNS_TOKEN", "")
                if token and result.get("actual_ip"):
                    try:
                        updated = await duckdns_monitor.auto_update(token, result["actual_ip"])
                        if updated:
                            desc += "\n\nDuckDNS wurde automatisch aktualisiert."
                        else:
                            desc += "\n\nAutomatisches DuckDNS-Update fehlgeschlagen!"
                    except Exception as update_err:
                        desc += f"\n\nAuto-Update Fehler: {update_err}"

                embed = error_embed(
                    title="DuckDNS: IP-Abweichung erkannt!",
                    description=desc,
                )
                content = f"<@&{NOTIFY_ROLE_ID}>" if NOTIFY_ROLE_ID else None
                await channel.send(content=content, embed=embed)
            except Exception as e:
                logger.debug(f"DuckDNS-Mismatch Notification Fehler: {e}")


duckdns_monitor.on_mismatch = _on_duckdns_mismatch


# -- F52: Port-Monitor Callbacks --

async def _on_port_closed(result: dict):
    if ADMIN_LOG_CHANNEL_ID:
        channel = bot.get_channel(ADMIN_LOG_CHANNEL_ID)
        if channel:
            try:
                embed = error_embed(
                    title=f"Port nicht erreichbar: {result.get('label', '?')}",
                    description=(
                        f"**Host:Port:** `{result.get('host', '?')}:{result.get('port', '?')}`\n"
                        f"**Fehler:** {result.get('error', 'unbekannt')}"
                    ),
                )
                await channel.send(embed=embed)
            except Exception as e:
                logger.debug(f"Port-Closed Notification Fehler: {e}")


async def _on_port_recovered(result: dict):
    if ADMIN_LOG_CHANNEL_ID:
        channel = bot.get_channel(ADMIN_LOG_CHANNEL_ID)
        if channel:
            try:
                embed = success_embed(
                    title=f"Port wieder erreichbar: {result.get('label', '?')}",
                    description=(
                        f"**Host:Port:** `{result.get('host', '?')}:{result.get('port', '?')}`\n"
                        f"**Antwortzeit:** {result.get('response_ms', '?')}ms"
                    ),
                )
                await channel.send(embed=embed)
            except Exception as e:
                logger.debug(f"Port-Recovered Notification Fehler: {e}")


port_monitor.on_port_closed = _on_port_closed
port_monitor.on_port_recovered = _on_port_recovered


# -- F42: PackageChecker Callbacks --

async def _on_updates_available(result: dict):
    if ADMIN_LOG_CHANNEL_ID:
        channel = bot.get_channel(ADMIN_LOG_CHANNEL_ID)
        if channel:
            try:
                total = result.get("total_updates", 0)
                security = result.get("security_updates", 0)
                packages = result.get("packages", [])
                # Maximal 10 Pakete auflisten
                pkg_list = "\n".join(
                    f"  - {p['name']} ({p['current']} -> {p['available']})"
                    for p in packages[:10]
                )
                if len(packages) > 10:
                    pkg_list += f"\n  ... und {len(packages) - 10} weitere"

                embed = warning_embed(
                    title=f"System-Updates verfuegbar: {total} Pakete",
                    description=(
                        f"**{total}** Paket-Updates verfuegbar"
                        f" ({security} Security)\n\n{pkg_list}"
                    ),
                )
                await channel.send(embed=embed)
            except Exception as e:
                logger.debug(f"Package-Updates Notification Fehler: {e}")


async def _on_security_updates(result: dict):
    if ADMIN_LOG_CHANNEL_ID:
        channel = bot.get_channel(ADMIN_LOG_CHANNEL_ID)
        if channel:
            try:
                security = result.get("security_updates", 0)
                sec_pkgs = [p for p in result.get("packages", []) if p.get("security")]
                pkg_list = "\n".join(
                    f"  - **{p['name']}** ({p['current']} -> {p['available']})"
                    for p in sec_pkgs[:15]
                )
                embed = error_embed(
                    title=f"Security-Updates: {security} Pakete!",
                    description=(
                        f"**{security}** sicherheitsrelevante Updates verfuegbar!\n\n"
                        f"{pkg_list}\n\n"
                        f"Bitte zeitnah aktualisieren."
                    ),
                )
                content = f"<@&{NOTIFY_ROLE_ID}>" if NOTIFY_ROLE_ID else None
                await channel.send(content=content, embed=embed)
            except Exception as e:
                logger.debug(f"Security-Updates Notification Fehler: {e}")


package_checker.on_updates_available = _on_updates_available
package_checker.on_security_updates = _on_security_updates


# -- Login Audit Callbacks (Phase 4a) --

async def _on_unknown_login(alert):
    if ADMIN_LOG_CHANNEL_ID:
        channel = bot.get_channel(ADMIN_LOG_CHANNEL_ID)
        if channel:
            try:
                embed = error_embed(
                    title="\U0001f6a8 Unbekannter SSH-Login!",
                    description=(
                        f"**User:** {alert['user']}\n"
                        f"**IP:** `{alert['ip']}`\n"
                        f"**Zeit:** {alert['timestamp'][:19]}"
                    ),
                )
                content = f"<@&{NOTIFY_ROLE_ID}>" if NOTIFY_ROLE_ID else None
                await channel.send(content=content, embed=embed)
            except Exception as e:
                logger.debug(f"Discord-Channel-Send / Stat-Sample swallowed (B110-refactor 3.1): {e}")

    await email_notifier.send_security_alert(
        f"SSH-Login von unbekannter IP: {alert['user']}@{alert['ip']}"
    )


async def _on_failed_login_burst(alert):
    if ADMIN_LOG_CHANNEL_ID:
        channel = bot.get_channel(ADMIN_LOG_CHANNEL_ID)
        if channel:
            try:
                embed = warning_embed(
                    title="\u26a0\ufe0f SSH Brute-Force erkannt",
                    description=(
                        f"**IP:** `{alert['ip']}`\n"
                        f"**Versuche:** {alert['count']}+\n"
                        f"Fail2Ban sollte diese IP sperren."
                    ),
                )
                await channel.send(embed=embed)
            except Exception as e:
                logger.debug(f"Discord-Channel-Send / Stat-Sample swallowed (B110-refactor 3.1): {e}")


login_audit.on_unknown_login = _on_unknown_login
login_audit.on_failed_login_burst = _on_failed_login_burst


# ------------------------------------------------------------------
# Player Detection via Server Log
# ------------------------------------------------------------------

# Join/Leave kommen aus dem Log-Parser (Verbindungs-ID statt Spielername): die
# Abmelde-Zeile des Servers enthaelt keinen Namen, nur Name: IpConnection_<id>.
# Die frueheren Regexe hier verlangten ein Feld PlayerName= und trafen deshalb
# nie eine Abmeldung — Spieler blieben fuer immer als online gefuehrt.
sat_log_players = SatLogPlayerParser()

_sat_log_path = (
    sat_server.server_path / "FactoryGame" / "Saved" / "Logs" / "FactoryGame.log"
)
_log_last_pos: int = 0
_log_last_size: int = 0
_log_lock = asyncio.Lock()
_status_lock = asyncio.Lock()

# Setzt der API-Abgleich, wenn Zahl und Namensliste auseinanderlaufen. Das Panel
# kennzeichnet die Liste dann als unvollstaendig, statt sich stumm zu
# widersprechen (Zahl 2, ein Name).
_spielerliste_unvollstaendig = False


def _init_log_position():
    """
    Seek to end of log, but scan full log first to recover
    currently online players (joins without matching leaves).
    """
    global _log_last_pos, _log_last_size
    try:
        if _sat_log_path.exists():
            _log_last_size = _sat_log_path.stat().st_size
            _log_last_pos = _log_last_size

            # Scan full log to find players still online
            try:
                with open(_sat_log_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()

                # Der Parser haelt die Zuordnung Verbindung -> Spieler und
                # rechnet Abmeldungen korrekt heraus.
                sat_log_players.reset()
                sat_log_players.feed(content)
                still_online = sat_log_players.online

                if still_online:
                    logger.info(
                        f"Log-Scan: {len(still_online)} Spieler noch online "
                        f"erkannt: {', '.join(sorted(still_online))}"
                    )
                    # Synchron ins Tracker-Set schreiben (update() ist async,
                    # deshalb direkt auf die interne Struktur zugreifen)
                    player_tracker._online_players = still_online
                    now_iso = datetime.now().isoformat()
                    for name in still_online:
                        if name not in player_tracker.players:
                            from modules.monitoring.player_tracker import PlayerRecord
                            player_tracker.players[name] = PlayerRecord(
                                name=name,
                                first_seen=now_iso,
                                last_seen=now_iso,
                            )
                        record = player_tracker.players[name]
                        record.is_online = True
                        if not record.current_session_start:
                            record.current_session_start = now_iso
            except Exception as e:
                logger.debug(f"Log-Scan fuer Online-Spieler fehlgeschlagen: {e}")
    except Exception as e:
        logger.debug(f"Log-Position initialisieren fehlgeschlagen: {e}")


async def _poll_player_events():
    """
    Read new log lines and detect join/leave events.
    Updates player_tracker with current player set.
    """
    global _log_last_pos, _log_last_size

    async with _log_lock:
        try:
            # exists() und stat() sind ebenfalls blockierende Syscalls — sie
            # gehoeren in denselben Executor wie der read(), sonst stimmt der
            # Anspruch "read in executor" nur zur Haelfte.
            def _groesse():
                return _sat_log_path.stat().st_size if _sat_log_path.exists() else None

            current_size = await asyncio.to_thread(_groesse)
            if current_size is None:
                return

            # Log rotated?
            if current_size < _log_last_size:
                _log_last_pos = 0
            _log_last_size = current_size

            if current_size <= _log_last_pos:
                return

            # Read new content in executor to not block
            def _read_new():
                with open(_sat_log_path, 'r', encoding='utf-8', errors='replace') as f:
                    f.seek(_log_last_pos)
                    content = f.read()
                    return content, f.tell()

            new_content, new_pos = await asyncio.get_running_loop().run_in_executor(
                None, _read_new
            )
            _log_last_pos = new_pos

            if not new_content:
                return

            # Feed each line to IP tracker for name<->IP mapping
            for line in new_content.splitlines():
                if line.strip():
                    player_ip_tracker.process_log_line(line)

            # Join/Leave ueber die Verbindungs-ID (siehe sat_log_players)
            joined, left = sat_log_players.feed(new_content)

            # Build current set: previous online + joins - leaves
            current = player_tracker.get_online_players()
            current = (current | joined) - left
            await player_tracker.update(current)

        except Exception as e:
            # Vorher debug — und der Logger steht auf INFO, die Meldung kam also
            # nirgends an. Ein stiller Ausfall des Spieler-Pollings sah damit
            # genauso aus wie "niemand online".
            logger.warning(f"Spieler-Log-Auswertung fehlgeschlagen: {e}", exc_info=True)


# ------------------------------------------------------------------
# Background Tasks
# ------------------------------------------------------------------

@tasks.loop(seconds=10)
async def player_log_task():
    """Poll server log for player join/leave events every 10 seconds"""
    if health_checker.status.state != ServerState.ONLINE:
        return

    try:
        await _poll_player_events()
    except Exception as e:
        logger.warning(f"Spieler-Log-Task fehlgeschlagen: {e}")


@player_log_task.before_loop
async def before_player_log():
    await bot.wait_until_ready()
    await asyncio.sleep(12)
    # Der Startscan liest das komplette Serverlog (aktuell rund 10 MB) und
    # parst es — im Event-Loop waeren das zig Millisekunden Blockade, die mit
    # der Logdatei weiterwachsen.
    await asyncio.to_thread(_init_log_position)


# ------------------------------------------------------------------
# Minecraft Chat-Bridge Task
# ------------------------------------------------------------------

# Fehlerzaehler je Server fuer die Chat-Bridge. Die Schleife laeuft alle fuenf
# Sekunden; ein dauerhaft kaputter Poll wuerde bei jedem Durchlauf melden.
# Deshalb: erste Stoerung sofort sichtbar, danach nur noch alle fuenf Minuten,
# und die Erholung wird gemeldet.
_bridge_fehler: dict[str, int] = {}
_BRIDGE_MELDE_INTERVALL = 60  # 60 × 5 s = alle 5 Minuten


@tasks.loop(seconds=5)
async def mc_chat_bridge_task():
    """Minecraft Log-Polling fuer Chat-Bridge (alle 5 Sekunden)"""
    for sid, bridge in mc_chat_bridges.items():
        srv = mc_servers.get(sid)
        if not srv or not srv.game_chat_channel_id:
            continue
        try:
            await bridge.poll()
        except Exception as e:
            # Frueher logger.debug — damit war eine dauerhaft tote Chat-Bruecke
            # im Normalbetrieb (Log-Level INFO) unsichtbar.
            anzahl = _bridge_fehler.get(sid, 0) + 1
            _bridge_fehler[sid] = anzahl
            if anzahl == 1 or anzahl % _BRIDGE_MELDE_INTERVALL == 0:
                logger.warning(
                    f"[{sid}] Chat-Bridge Poll fehlgeschlagen "
                    f"({anzahl}. Mal in Folge): {e}"
                )
        else:
            if _bridge_fehler.pop(sid, 0):
                logger.info(f"[{sid}] Chat-Bridge liefert wieder")


@mc_chat_bridge_task.before_loop
async def before_mc_chat_bridge():
    await bot.wait_until_ready()
    await asyncio.sleep(15)
    # Chat-Bridges initialisieren (Log-Position auf Ende setzen)
    for bridge in mc_chat_bridges.values():
        await bridge.initialize()
    if mc_chat_bridges:
        logger.info(f"Minecraft Chat-Bridges gestartet: {list(mc_chat_bridges.keys())}")


# ------------------------------------------------------------------
# Minecraft Health-Check Task
# ------------------------------------------------------------------

# Tracking pro Server: Anzahl aufeinanderfolgende Offline-Checks
_mc_consecutive_offline: dict[str, int] = {sid: 0 for sid in mc_servers}
_mc_downtime_notified: dict[str, bool] = {sid: False for sid in mc_servers}


@tasks.loop(seconds=120)
async def mc_health_check_task():
    """Minecraft Server Health-Check alle 2 Minuten"""
    for sid, srv in mc_servers.items():
        try:
            running = await srv.is_running()

            if not running:
                # Manuell gestoppt (z.B. via Dashboard)? -> erwarteter Zustand,
                # kein Offline-Alarm (service_watchdog respektiert das bereits).
                from modules.monitoring import manual_stop_state
                if manual_stop_state.is_manually_stopped(f"mc_{sid.lower()}"):
                    _mc_consecutive_offline[sid] = 0
                    _mc_downtime_notified[sid] = False
                    continue
                _mc_consecutive_offline[sid] = _mc_consecutive_offline.get(sid, 0) + 1
                count = _mc_consecutive_offline[sid]

                # Offline-Stats tracken
                mc_st = mc_stats_trackers.get(sid)
                if mc_st:
                    mc_st.record_uptime_check(False)

                # Nach 3 aufeinanderfolgenden Checks (6 Min) benachrichtigen
                if count >= 3 and not _mc_downtime_notified.get(sid, False):
                    _mc_downtime_notified[sid] = True
                    admin_ch = bot.get_channel(ADMIN_LOG_CHANNEL_ID)
                    if admin_ch:
                        try:
                            embed = error_embed(
                                title=f"Minecraft {srv.display_name} offline",
                                description=(
                                    f"{srv.display_name} ist seit {count * 2} Minuten "
                                    f"nicht erreichbar.\n"
                                    f"Service: `{srv.service_name}`"
                                ),
                            )
                            await admin_ch.send(embed=embed)
                        except Exception as e:
                            logger.debug(f"[{sid}] Downtime-Notification Fehler: {e}")

            else:
                # Server online — Recovery-Notification
                if _mc_consecutive_offline.get(sid, 0) > 0:
                    if _mc_downtime_notified.get(sid, False):
                        _mc_downtime_notified[sid] = False
                        admin_ch = bot.get_channel(ADMIN_LOG_CHANNEL_ID)
                        if admin_ch:
                            try:
                                embed = success_embed(
                                    title=f"Minecraft {srv.display_name} wieder online",
                                    description=(
                                        f"Server ist wieder erreichbar.\n"
                                        f"War {_mc_consecutive_offline[sid] * 2} Minuten offline."
                                    ),
                                )
                                await admin_ch.send(embed=embed)
                            except Exception as e:
                                logger.debug(f"[{sid}] Recovery-Notification Fehler: {e}")

                _mc_consecutive_offline[sid] = 0

                # MC Stats-Tracker: Uptime + Spielerzahl loggen
                mc_st = mc_stats_trackers.get(sid)
                if mc_st:
                    mc_st.record_uptime_check(True)

                try:
                    online, max_p = await srv.get_player_count()
                    logger.debug(f"[{sid}] Spieler: {online}/{max_p}")
                    if mc_st:
                        mc_st.record_player_count(online)
                except Exception as e:
                    logger.debug(f"Health-Check-Loop iteration swallowed (B110-refactor 3.1): {e}")

                # World-Groesse periodisch loggen (alle 5 Checks = ~10 Min)
                if mc_health_check_task.current_loop % 5 == 0:
                    try:
                        world_bytes = await srv.get_world_size()
                        if world_bytes > 0 and mc_st:
                            mc_st.record_world_size(world_bytes / (1024 * 1024))
                    except Exception as e:
                        logger.debug(f"Sub-task swallowed (B110-refactor 3.1): {e}")

                # Crash-Replay Buffer aktualisieren
                mc_cr = mc_crash_replays.get(sid)
                if mc_cr:
                    try:
                        await mc_cr.update_buffer()
                    except Exception as e:
                        logger.debug(f"Sub-task swallowed (B110-refactor 3.1): {e}")

        except Exception as e:
            logger.debug(f"[{sid}] MC Health-Check Fehler: {e}")


@mc_health_check_task.before_loop
async def before_mc_health_check():
    await bot.wait_until_ready()
    await asyncio.sleep(30)  # Nach anderen Tasks starten
    # Crash-Replay Positionen initialisieren
    for sid, cr in mc_crash_replays.items():
        cr.init_position()
    # IP-Bans mit UFW synchronisieren
    for sid, ipt in mc_ip_trackers.items():
        await ipt.sync_bans()


@tasks.loop(seconds=60)
async def login_audit_task():
    """Check auth.log for SSH login events every 60 seconds"""
    if not config.get("features", {}).get("login_audit", True):
        return
    try:
        await login_audit.check()
    except Exception as e:
        logger.warning(f"Login-Audit fehlgeschlagen: {e}")


@login_audit_task.before_loop
async def before_login_audit():
    await bot.wait_until_ready()
    await asyncio.sleep(20)
    login_audit.init_position()


@tasks.loop(seconds=150)
async def health_auto_restart_task():
    """F27: Health-Check mit Auto-Restart alle 2.5 Minuten"""
    try:
        await health_auto_restart.check_all_servers()
    except Exception as e:
        logger.debug(f"Health auto-restart task error: {e}")


@health_auto_restart_task.before_loop
async def before_health_auto_restart():
    await bot.wait_until_ready()
    await asyncio.sleep(45)  # Nach anderen Tasks starten


@tasks.loop(seconds=86400)
async def ssl_check_task():
    """F32: SSL-Zertifikat taeglich pruefen"""
    try:
        result = await ssl_monitor.check_certificate()
        if result.get("status") in ("warning", "critical", "expired"):
            # Warnung an Admin-Channel
            if ADMIN_LOG_CHANNEL_ID:
                channel = bot.get_channel(ADMIN_LOG_CHANNEL_ID)
                if channel:
                    days = result.get("days_remaining", "?")
                    status = result.get("status", "unbekannt")
                    color = COLOR_WARNING if status == "warning" else 0xe74c3c
                    embed = discord.Embed(
                        title=f"SSL-Zertifikat: {status.upper()}",
                        description=(
                            f"Domain: `{result.get('domain', '?')}`\n"
                            f"Verbleibend: **{days} Tage**\n"
                            f"Aussteller: {result.get('issuer', 'unbekannt')}"
                        ),
                        color=color,
                        timestamp=datetime.now(),
                    )
                    try:
                        await channel.send(embed=embed)
                    except Exception as e:
                        logger.debug(f"Sub-task swallowed (B110-refactor 3.1): {e}")

        # SSL-Status als JSON fuer Dashboard schreiben
        import json as _json
        _ssl_dir = PROJECT_ROOT / "data" / "monitor"
        _ssl_dir.mkdir(parents=True, exist_ok=True)
        _ssl_tmp = _ssl_dir / "ssl_status.json.tmp"
        _ssl_target = _ssl_dir / "ssl_status.json"
        try:
            with open(_ssl_tmp, "w") as f:
                _json.dump(result, f)
            _ssl_tmp.replace(_ssl_target)
        except Exception as e:
            logger.debug(f"Status-Write/API-Query swallowed (B110-refactor 3.1): {e}")
    except Exception as e:
        logger.debug(f"SSL check task error: {e}")


@ssl_check_task.before_loop
async def before_ssl_check():
    await bot.wait_until_ready()
    await asyncio.sleep(60)


@tasks.loop(seconds=120)
async def health_check_task():
    """Health check every 2 minutes"""
    global _consecutive_offline_checks, _downtime_notified
    try:
        status = await health_checker.check()

        # -- Downtime Notification (Phase 10b) --
        if status.state != ServerState.ONLINE:
            # Manuell gestoppt (z.B. via Dashboard)? -> erwarteter Zustand, kein Alarm.
            from modules.monitoring import manual_stop_state
            if manual_stop_state.is_manually_stopped("satisfactory"):
                _consecutive_offline_checks = 0
                _downtime_notified = False
            else:
                _consecutive_offline_checks += 1

            # After 3 consecutive checks (6 minutes) offline and not yet notified
            if _consecutive_offline_checks >= 3 and not _downtime_notified and ADMIN_LOG_CHANNEL_ID:
                _downtime_notified = True
                channel = bot.get_channel(ADMIN_LOG_CHANNEL_ID)
                if channel:
                    try:
                        embed = error_embed(
                            title="🔴 Satisfactory Server ist offline",
                            description=(
                                f"Der Satisfactory-Server ist seit "
                                f"{_consecutive_offline_checks * 2} Minuten nicht erreichbar.\n"
                                f"Erkannt um {datetime.now().strftime('%H:%M:%S')}."
                            ),
                        )
                        await channel.send(embed=embed)
                        logger.info("SAT Downtime notification sent to admin channel")
                    except Exception as e:
                        logger.error(f"Failed to send downtime notification: {e}")
        else:
            # Server is online
            if _consecutive_offline_checks > 0:
                # Server was offline, now back online
                if _downtime_notified and ADMIN_LOG_CHANNEL_ID:
                    _downtime_notified = False
                    channel = bot.get_channel(ADMIN_LOG_CHANNEL_ID)
                    if channel:
                        try:
                            embed = success_embed(
                                title="🟢 Satisfactory Server ist wieder online",
                                description=(
                                    f"Server ist um {datetime.now().strftime('%H:%M:%S')} wieder erreichbar.\n"
                                    f"War {_consecutive_offline_checks * 2} Minuten offline."
                                ),
                            )
                            await channel.send(embed=embed)
                            logger.info("SAT Recovery notification sent to admin channel")
                        except Exception as e:
                            logger.error(f"Failed to send recovery notification: {e}")

            _consecutive_offline_checks = 0

        # Record stats for reports
        stats_tracker.record_uptime_check(status.state == ServerState.ONLINE)
        if status.state == ServerState.ONLINE:
            stats_tracker.record_player_count(status.players_online)

        # Record savegame size periodically (every ~10 min = every 5th check)
        if health_check_task.current_loop % 5 == 0:
            try:
                save_path = Path(get_env("SATISFACTORY_SAVE_PATH",
                    "/home/satisfactory/.config/Epic/FactoryGame/Saved/SaveGames"))
                if save_path.exists():
                    total_size = sum(f.stat().st_size for f in save_path.rglob("*.sav") if f.is_file())
                    stats_tracker.record_savegame_size(total_size / (1024 * 1024))
            except Exception as e:
                logger.debug(f"Discord-Channel-Send / Stat-Sample swallowed (B110-refactor 3.1): {e}")

        # Sanity check: log polling runs in player_log_task (10s)
        # Here we only do the API cross-check every 2 min
        if status.state == ServerState.ONLINE:
            # Abgleich gegen die API. Der frühere Check griff nur bei genau 0
            # Spielern — eine Abweichung wie "API 1, Liste 2" fiel durchs Raster
            # und blieb stundenlang im Panel stehen.
            global _spielerliste_unvollstaendig
            api_count = status.players_online
            tracked = player_tracker.get_online_players()
            _spielerliste_unvollstaendig = False
            if api_count == 0 and len(tracked) > 0:
                logger.info(
                    f"API meldet 0 Spieler, Tracker hat {len(tracked)} — "
                    f"Sitzungen werden geschlossen"
                )
                player_tracker.close_all_sessions()
                sat_log_players.reset()
            elif api_count != len(tracked):
                # Der Log-Parser ist die Quelle der Namen; weicht er von der
                # API ab, hat das Log eine Abmeldung verpasst (Rotation,
                # Bot-Neustart mitten in der Sitzung).
                aus_log = sat_log_players.online
                if aus_log != tracked:
                    logger.info(
                        f"Spielerliste folgt dem Log-Parser: {sorted(tracked)} -> "
                        f"{sorted(aus_log)} (API: {api_count})"
                    )
                    await player_tracker.update(aus_log)
                elif len(tracked) > api_count:
                    # Zu viele Namen. Wen das Log nie abmelden kann, sind die
                    # "unbound"-Eintraege: Spieler, deren Verbindungsaufbau der
                    # Parser nie gesehen hat (Log-Rotation, Bot-Start mitten in
                    # der Sitzung). Genau die raeumt drop() weg — ohne diesen
                    # Aufruf gab es den Ausgang zwar, aber niemand nahm ihn,
                    # und der Abgleich meldete alle zwei Minuten dasselbe.
                    ueberschuss = len(tracked) - api_count
                    verwaist = sorted(sat_log_players.unbound)[:ueberschuss]
                    if verwaist:
                        logger.info(
                            f"API meldet {api_count} Spieler, Log-Parser "
                            f"{len(tracked)} — entferne {verwaist} "
                            f"(ohne bekannte Verbindung, per Log nicht abmeldbar)"
                        )
                        sat_log_players.drop(set(verwaist))
                        await player_tracker.update(sat_log_players.online)
                    else:
                        logger.warning(
                            f"API meldet {api_count} Spieler, Log-Parser {len(tracked)}: "
                            f"{sorted(tracked)} — Liste kann veraltete Namen enthalten"
                        )
                        _spielerliste_unvollstaendig = True
                else:
                    # Spiegelbild: der Parser kennt WENIGER Namen als die API
                    # zaehlt — dem Log fehlt ein Join (Rotation, Neustart
                    # mitten in der Sitzung). Ohne diesen Zweig blieb das
                    # stumm, und das Panel zeigte dauerhaft zu wenige Spieler.
                    logger.warning(
                        f"API meldet {api_count} Spieler, Log-Parser nur "
                        f"{len(tracked)}: {sorted(tracked)} — Namensliste ist "
                        f"unvollstaendig, die Zahl im Panel fuehrt"
                    )
                    _spielerliste_unvollstaendig = True
        else:
            # Server offline - close any open sessions
            if player_tracker.get_online_players():
                player_tracker.close_all_sessions()

        # Update crash replay buffer (keep recent log lines in memory)
        await crash_replay.update_buffer()

        # Performance check
        metrics = await perf_monitor.collect(sat_server)
        warnings = perf_monitor.check_thresholds(metrics)
        if warnings:
            await notifier.notify_performance_warning(warnings)
            await email_notifier.send_performance_alert(warnings)

    except Exception as e:
        logger.error(f"Health check task error: {e}", exc_info=True)


@health_check_task.before_loop
async def before_health_check():
    await bot.wait_until_ready()
    await asyncio.sleep(10)  # Wait for bot to fully initialize
    # Log position init moved to player_log_task (10s polling)
    crash_replay.init_position()  # Pre-fill crash replay buffer

    # Sync IP bans with UFW on startup
    await player_ip_tracker.sync_bans()


@tasks.loop(seconds=300)
async def update_status_embed():
    """Update the dashboard status embed every 5 minutes"""
    global _status_message_id
    try:
        await _update_status_embed_impl()
    except Exception as e:  # noqa: BLE001
        # Ohne diesen Fang beendet ein einzelner Renderfehler die Schleife
        # endgueltig. Das Panel bliebe stehen und zeigte den alten Stand als
        # Wahrheit — der teuerste Ausfall ist der, den man nicht sieht.
        logger.error(f"Status-Panel konnte nicht aktualisiert werden: {e}",
                     exc_info=True)


async def _update_status_embed_impl():
    """Implementation of status embed update (callable from command too)"""
    global _status_message_id

    if not STATUS_EMBED_CHANNEL_ID:
        return

    channel = bot.get_channel(STATUS_EMBED_CHANNEL_ID)
    if not channel:
        return

    # Load persisted message ID on first run
    # Review-Fix 2026-06-12 (LOW): File-IO via to_thread statt blocking im Loop
    if _status_message_id is None:
        _status_message_id = await asyncio.to_thread(_load_status_message_id)

    status = health_checker.status
    running = status.process_running
    now = datetime.now()

    # Get API data for extra info
    api_state = None
    if running:
        try:
            api_state = await sat_api.query_server_state()
        except Exception as e:
            logger.debug(f"Status-Write/API-Query swallowed (B110-refactor 3.1): {e}")

    # Get savegame stats (cached, non-blocking)
    world_stats = None
    try:
        world_stats = await savegame_analyzer.get_stats()
    except Exception as e:
        logger.debug(f"Savegame stats unavailable: {e}")

    # -- Panel im HUD-Stil bauen (siehe docs/DESIGN_HUD.md) --
    # Hier nur Daten sammeln; das Rendern macht modules/monitoring/status_panel.py
    # und ist dadurch ohne laufenden Server testbar.
    server_lines: List[ServerLine] = []

    # -- Satisfactory --
    if running:
        sat_count = status.players_online
        sat_limit = status.player_limit
        if api_state and api_state.ok:
            sat_count = api_state.num_players
            if api_state.player_limit > 0:
                sat_limit = api_state.player_limit

        notiz = f"Uptime {format_uptime(status.uptime)}"
        tick_rate = 0.0
        if api_state and api_state.ok:
            tick_rate = api_state.average_tick_rate
        elif status.tick_rate > 0:
            tick_rate = status.tick_rate
        if tick_rate > 0:
            # 30 Ticks sind das Soll; darunter ruckelt es spuerbar.
            tick_zustand = "ok" if tick_rate >= 25 else "warn" if tick_rate >= 15 else "crit"
            notiz += f" · {status_dot(tick_zustand)} {tick_rate:.0f}/30 Ticks"

        details: List[str] = []
        online = player_tracker.get_online_players()
        if online:
            # Spielernamen kommen vom Gameserver, nicht von uns — ohne Escaping
            # koennte ein Name das Panel-Markdown umformatieren.
            details.append("👤 " + ", ".join(
                discord.utils.escape_markdown(n) for n in sorted(online)
            ))

        if world_stats and world_stats.available:
            power_str = f"{world_stats.total_power_mw:,.0f} MW".replace(",", ".")
            details.append(
                f"🏗️ {world_stats.total_buildings:,} Gebäude · "
                f"⚡ {world_stats.generators} Gen ({power_str}) · "
                f"🏭 {world_stats.production_machines} Prod"
            )
        elif world_stats and world_stats.session_name:
            details.append(f"📋 Session {world_stats.session_name}")

        if world_stats:
            save_parts = []
            if world_stats.save_size:
                save_parts.append(f"💾 {world_stats.save_size}")
            if world_stats.play_hours > 0:
                save_parts.append(f"⏱️ {world_stats.play_hours}h Spielzeit")
            if save_parts:
                details.append(" · ".join(save_parts))

        # Naechster Neustart als relative Zeit — laeuft im Client weiter, ohne
        # dass der Bot dafuer editieren muss.
        try:
            scheduler_cog = bot.get_cog("SchedulerCog")
            if scheduler_cog:
                restart_hour = getattr(scheduler_cog, "_daily_restart_hour", 4)
                restart_min = getattr(scheduler_cog, "_daily_restart_minute", 0)
                next_restart = now.replace(
                    hour=restart_hour, minute=restart_min, second=0, microsecond=0
                )
                if next_restart <= now:
                    next_restart += timedelta(days=1)
                details.append(f"🔄 Neustart {rel_time(next_restart)}")
        except Exception as e:
            logger.debug(f"Status-Write/API-Query swallowed (B110-refactor 3.1): {e}")

        if update_checker and update_checker.update_available:
            details.append("📦 Server-Update verfügbar")

        server_lines.append(ServerLine(
            liste_unvollstaendig=_spielerliste_unvollstaendig,
            name="Satisfactory", online=True, players=sat_count,
            limit=sat_limit or None, note=notiz, details=details,
        ))
    else:
        server_lines.append(ServerLine(name="Satisfactory", online=False))

    # -- Minecraft --
    for mc_sid, mc_srv in mc_servers.items():
        try:
            mc_running = await mc_srv.is_running()
            if not mc_running:
                server_lines.append(ServerLine(name=mc_srv.display_name, online=False))
                continue

            mc_count = mc_max = None
            try:
                mc_count, mc_max = await mc_srv.get_player_count()
            except Exception as e:
                logger.debug(f"[{mc_sid}] Spielerzahl nicht abrufbar: {e}")

            notiz = ""
            try:
                world_bytes = await mc_srv.get_world_size()
                if world_bytes > 0:
                    notiz = f"Welt {world_bytes / (1024 * 1024):.0f} MB"
            except Exception as e:
                logger.debug(f"Sub-task swallowed (B110-refactor 3.1): {e}")

            mc_details: List[str] = []
            mc_pt = mc_player_trackers.get(mc_sid)
            if mc_pt:
                mc_online_players = mc_pt.get_online_players()
                if mc_online_players:
                    mc_details.append("👤 " + ", ".join(
                        discord.utils.escape_markdown(n)
                        for n in sorted(mc_online_players)
                    ))

            mc_uc = mc_update_checkers.get(mc_sid)
            if mc_uc and mc_uc.update_available:
                mc_details.append("📦 Paper-Update verfügbar")

            server_lines.append(ServerLine(
                name=mc_srv.display_name, online=True, players=mc_count,
                limit=mc_max, note=notiz, details=mc_details,
            ))
        except Exception as e:
            logger.debug(f"[{mc_sid}] Status-Embed MC Fehler: {e}")

    embed = build_status_panel(server_lines)

    # -- Send or Edit (Lock schuetzt _status_message_id) --
    async with _status_lock:
        try:
            if _status_message_id:
                try:
                    msg = await channel.fetch_message(_status_message_id)
                    await msg.edit(embed=embed)
                    return
                except discord.NotFound:
                    _status_message_id = None

            # Post new message and persist ID
            msg = await channel.send(embed=embed)
            _status_message_id = msg.id
            # Review-Fix 2026-06-12 (LOW): File-IO via to_thread
            await asyncio.to_thread(_save_status_message_id, msg.id)
        except Exception as e:
            logger.error(f"Status embed error: {e}")


# Expose for cog access
bot.update_status_embed_now = _update_status_embed_impl


@update_status_embed.before_loop
async def before_status_embed():
    await bot.wait_until_ready()
    await asyncio.sleep(20)


# Cache fuer automatisch erstellte Voice-Channels {key: channel_id}
_voice_channel_cache: dict[str, int] = {}


async def _get_or_create_voice_channel(
    category: discord.CategoryChannel, key: str, initial_name: str
) -> Optional[discord.VoiceChannel]:
    """Voice-Channel in Kategorie finden oder neu erstellen. Cached die ID."""
    global _voice_channel_cache

    # Aus Cache laden
    cached_id = _voice_channel_cache.get(key)
    if cached_id:
        ch = category.guild.get_channel(cached_id)
        if ch:
            return ch

    # In Kategorie suchen (nach Key im Namen)
    for vc in category.voice_channels:
        if key.upper() in vc.name.upper():
            _voice_channel_cache[key] = vc.id
            return vc

    # Nicht gefunden — neu erstellen
    try:
        # Keine Verbindung erlauben (nur Anzeige)
        overwrites = {
            category.guild.default_role: discord.PermissionOverwrite(connect=False),
            category.guild.me: discord.PermissionOverwrite(
                connect=True, manage_channels=True
            ),
        }
        vc = await category.create_voice_channel(
            name=initial_name, overwrites=overwrites
        )
        _voice_channel_cache[key] = vc.id
        logger.info(f"Voice-Channel erstellt: {initial_name} (ID: {vc.id})")
        return vc
    except discord.Forbidden:
        logger.warning(f"Keine Berechtigung zum Erstellen von Voice-Channel: {initial_name}")
        return None
    except Exception as e:
        logger.error(f"Voice-Channel erstellen fehlgeschlagen: {e}")
        return None


@tasks.loop(seconds=300)
async def update_voice_stats():
    """Update voice channel stats every 5 minutes — erstellt Channels automatisch"""
    # Ganzer Koerper gekapselt: ein HTTPException beim Anlegen eines
    # Voice-Channels beendete die Schleife bisher endgueltig — und mit ihr
    # sowohl die Voice-Statistik als auch den Minecraft-Spielerabgleich.
    try:
        if not VOICE_STATS_CATEGORY_ID or not GUILD_ID:
            return

        guild = bot.get_guild(GUILD_ID)
        if not guild:
            return

        category = guild.get_channel(VOICE_STATS_CATEGORY_ID)
        if not category or not isinstance(category, discord.CategoryChannel):
            return

        # --- Satisfactory Status ---
        try:
            sat_running = await sat_server.is_running()
        except Exception:
            sat_running = False

        sat_players = 0
        sat_limit = 0
        if sat_running:
            status = health_checker.status
            sat_players = status.players_online
            sat_limit = status.player_limit
            try:
                api_state = await sat_api.query_server_state()
                # ok=False heisst: Abfrage gescheitert, die Werte sind Defaults.
                if api_state and api_state.ok:
                    sat_players = api_state.num_players
                    if api_state.player_limit > 0:
                        sat_limit = api_state.player_limit
            except Exception as e:
                logger.debug(f"Status-Write/API-Query swallowed (B110-refactor 3.1): {e}")

        # SAT Voice-Channel
        sat_vc = await _get_or_create_voice_channel(
            category, "SAT", "SAT-1 | 🔴 Offline"
        )
        if sat_vc:
            if sat_running:
                new_name = f"SAT-1 | 🟢 {sat_players}/{sat_limit}"
            else:
                new_name = "SAT-1 | 🔴 Offline"
            try:
                if sat_vc.name != new_name:
                    await sat_vc.edit(name=new_name)
            except discord.Forbidden:
                logger.warning(f"Keine Berechtigung fuer Voice-Channel: {sat_vc.name}")
            except Exception as e:
                logger.error(f"Voice stats SAT error: {e}")

        # --- Minecraft Voice-Channels ---
        if hasattr(bot, 'mc_servers'):
            for sid, srv in bot.mc_servers.items():
                try:
                    mc_running = await srv.is_running()
                    mc_players = 0
                    mc_max = 0
                    mc_player_names = []
                    if mc_running:
                        mc_players, mc_max = await srv.get_player_count()
                        # Spielernamen via RCON holen und PlayerTracker synchronisieren
                        try:
                            mc_player_names = await srv.get_players()
                        except Exception:
                            mc_player_names = []
                except Exception:
                    mc_running = False
                    mc_players = 0
                    mc_max = 0
                    mc_player_names = []

                # PlayerTracker mit RCON-Daten synchronisieren
                mc_pt = mc_player_trackers.get(sid)
                if mc_pt:
                    if mc_running and mc_player_names:
                        await mc_pt.update(set(mc_player_names))
                    elif not mc_running and mc_pt.get_online_players():
                        # Server offline aber Tracker zeigt noch Spieler → bereinigen
                        await mc_pt.update(set())

                vc_key = f"MC-{sid}"
                mc_vc = await _get_or_create_voice_channel(
                    category, vc_key, f"MC-{sid} | 🔴 Offline"
                )
                if mc_vc:
                    if mc_running:
                        new_name = f"MC-{sid} | 🟢 {mc_players}/{mc_max}"
                    else:
                        new_name = f"MC-{sid} | 🔴 Offline"
                    try:
                        if mc_vc.name != new_name:
                            await mc_vc.edit(name=new_name)
                    except discord.Forbidden:
                        logger.warning(f"Keine Berechtigung fuer Voice-Channel: {mc_vc.name}")
                    except Exception as e:
                        logger.error(f"Voice stats MC-{sid} error: {e}")
    except Exception as e:  # noqa: BLE001
        logger.error(f"Voice-Statistik fehlgeschlagen: {e}", exc_info=True)


@update_voice_stats.before_loop
async def before_voice_stats():
    await bot.wait_until_ready()
    await asyncio.sleep(30)


# ------------------------------------------------------------------
# Optimization Task (every 15 min)
# ------------------------------------------------------------------

@tasks.loop(seconds=900)
async def optimize_task():
    """Check system health and auto-optimize if needed"""
    try:
        results = await optimizer.check_and_optimize()

        if results and "triggers" in results:
            # Auto-optimization was triggered - notify admin
            triggers = "\n".join(f"• {t}" for t in results["triggers"])
            actions = []
            for key, val in results.items():
                if key == "triggers":
                    continue
                status = "✅" if val["success"] else "❌"
                actions.append(f"{status} {key}: {val['message']}")

            await notifier.send_admin(
                "⚡ Auto-Optimierung ausgeführt",
                f"**Auslöser:**\n{triggers}\n\n"
                f"**Aktionen:**\n" + "\n".join(actions),
                NotifyLevel.WARNING,
            )

    except Exception as e:
        logger.error(f"Optimize task error: {e}", exc_info=True)


@optimize_task.before_loop
async def before_optimize_task():
    await bot.wait_until_ready()
    await asyncio.sleep(60)  # Wait a bit longer before first optimization


@tasks.loop(hours=168)  # Weekly
async def weekly_snapshot_task():
    """Create weekly savegame snapshot for delta comparison"""
    if hasattr(bot, 'savegame_analyzer'):
        try:
            result = await bot.savegame_analyzer.create_weekly_snapshot()
            if result:
                logger.info(f"Weekly snapshot created: {result.get('iso_week', '?')}")
        except Exception as e:
            logger.error(f"Weekly snapshot error: {e}")


@weekly_snapshot_task.before_loop
async def before_weekly_snapshot():
    await bot.wait_until_ready()
    await asyncio.sleep(120)


# ------------------------------------------------------------------
# F63/F56: Database Maintenance Task (taeglich)
# ------------------------------------------------------------------

@tasks.loop(hours=6)
async def db_maintenance_task():
    """F63/F56: SQLite-Backup + Daten-Retention alle 6 Stunden."""
    try:
        results = await db_maintenance.full_maintenance()
        backup = results.get("backup", {})
        retention = results.get("retention", {})
        if backup.get("success"):
            logger.info(
                f"DB-Maintenance: Backup OK ({backup.get('size_mb', 0):.1f} MB), "
                f"Rotation: {results.get('rotated_files', 0)} geloescht, "
                f"Retention: {sum(retention.values())} Zeilen bereinigt"
            )
        else:
            logger.warning(f"DB-Maintenance: Backup fehlgeschlagen — {backup.get('error', '?')}")
    except Exception as e:
        logger.error(f"DB-Maintenance-Task Fehler: {e}")

    # F55: FTS5 Suchindex inkrementell aktualisieren
    try:
        from modules.database.search_indexer import SearchIndexer
        indexer = SearchIndexer()
        idx_result = await indexer.incremental_update()
        new_entries = idx_result.get("new_entries", 0)
        if new_entries > 0:
            logger.info(f"FTS5 Index: {new_entries} neue Eintraege indiziert")
    except Exception as e:
        logger.debug(f"FTS5 Index-Update fehlgeschlagen: {e}")


@db_maintenance_task.before_loop
async def before_db_maintenance():
    await bot.wait_until_ready()
    await asyncio.sleep(300)  # 5 Minuten nach Start warten


# ------------------------------------------------------------------
# F28-8: Ban-Expiry Background-Task
# ------------------------------------------------------------------

@tasks.loop(seconds=60)
async def ban_expiry_task():
    """Prueft alle 60 Sekunden ob temporaere Bans abgelaufen sind."""
    try:
        expired = await ban_manager.check_expired_bans()
        if expired > 0:
            logger.info(f"Ban-Expiry: {expired} abgelaufene Bans entfernt")
    except Exception as e:
        logger.debug(f"Ban-Expiry-Task Fehler: {e}")


@ban_expiry_task.before_loop
async def before_ban_expiry():
    await bot.wait_until_ready()
    await asyncio.sleep(30)


# ------------------------------------------------------------------
# Web-Status-Seite Task (Phase 8g)
# ------------------------------------------------------------------

@tasks.loop(seconds=60)
async def web_status_task():
    """Generiert die HTML-Status-Seite alle 60 Sekunden"""
    if web_status_generator:
        try:
            success = await web_status_generator.generate()
            if not success:
                logger.debug("Web-Status-Seite Generierung fehlgeschlagen")
        except Exception as e:
            logger.debug(f"Web-Status-Task Fehler: {e}")


@web_status_task.before_loop
async def before_web_status():
    await bot.wait_until_ready()
    await asyncio.sleep(15)


# ------------------------------------------------------------------
# Bot Events
# ------------------------------------------------------------------

_first_ready = True  # Track first on_ready vs reconnects


@bot.event
async def setup_hook():
    """Called once before the bot connects. Load cogs and sync commands here."""
    # tracemalloc-Diagnose — Default seit 2026-08-14 AUS.
    # Es lief seit der RSS-Analyse dauerhaft mit, weil der Default "1" war und
    # niemand die Variable je gesetzt hat. Gemessen kostet das Allokationen um
    # den Faktor 4 und rund 34 MB Metadaten je 200k Objekte; der recon-Bot lag
    # bei 346 MB RSS gegen 69-72 MB der Schwester-Prozesse, bei MemoryMax=768M.
    # Zum Messen bewusst einschalten: TRACEMALLOC_ENABLED=1 in der .env.
    if os.getenv("TRACEMALLOC_ENABLED", "0") == "1":
        tracemalloc.start()
        logger.info("tracemalloc gestartet (Diagnose-Modus)")

    # Load cogs (runs exactly once)
    cog_list = [
        "cogs.monitor_cog",
        "cogs.scheduler_cog",
        "cogs.maintenance_mode_cog",  # F43: Wartungsmodus-Toggle
        "cogs.shutdown_cog",          # F54: Geplanter Shutdown
        "cogs.update_cog",            # /modpack + /update Commands (braucht UpdateManager)
    ]
    for cog in cog_list:
        try:
            await bot.load_extension(cog)
            logger.info(f"Loaded cog: {cog}")
        except Exception as e:
            logger.error(f"Failed to load {cog}: {e}")

    # Sync commands
    if GUILD_ID:
        guild = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        logger.info(f"Synced {len(synced)} commands to guild {GUILD_ID}")

    # Review-Fix 2026-06-12 (HIGH): Signal-Handler HIER registrieren, nicht in
    # main() — setup_hook laeuft im Event-Loop den bot.run() via asyncio.run()
    # erstellt. Ein Aufruf vor bot.run() wuerde auf einem anderen, nie
    # laufenden Loop landen. Damit feuert _graceful_shutdown (inkl.
    # register_cleanup-Callbacks: WAL-Checkpoint, sat_api.close, Watchdog-Stop)
    # auch bei SIGTERM/systemctl stop — nicht nur bei KeyboardInterrupt.
    setup_signal_handlers(bot, "Recon")

    logger.info("Setup hook complete")


@tasks.loop(minutes=10)
async def channel_snapshot_task():
    """Schreibt die Guild-Channel-Liste nach data/guild_channels.json — Bruecke fuer
    die Dashboard-Channel-Dropdowns (Namen statt roher IDs). tasks.loop feuert die
    erste Iteration sofort beim start() (in on_ready), danach alle 10 min als Fallback.
    Die schnelle Aktualisierung laeuft event-getrieben (on_guild_channel_* unten)."""
    if not GUILD_ID:
        return
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        return
    try:
        # In den Thread-Pool: der Write ist klein, aber blockierend — und ein
        # IO-Fehler beendete die Schleife bisher dauerhaft.
        await asyncio.to_thread(write_channel_snapshot, guild)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Channel-Snapshot fehlgeschlagen: {e}")


# Event-getriebener Schnell-Sync: bei Channel-Aenderungen in der Haupt-Guild sofort
# neu snapshotten (mit 2s-Debounce, damit Bulk-Ops nicht dutzende Writes ausloesen).
_channel_snap_pending = False


async def _debounced_channel_snapshot():
    global _channel_snap_pending
    if _channel_snap_pending:
        return
    _channel_snap_pending = True
    try:
        await asyncio.sleep(2)  # Burst-Coalescing (mehrere Aenderungen -> 1 Write)
        guild = bot.get_guild(GUILD_ID) if GUILD_ID else None
        if guild is not None:
            write_channel_snapshot(guild)
    finally:
        _channel_snap_pending = False


def _is_primary_guild_channel(channel) -> bool:
    g = getattr(channel, "guild", None)
    return bool(GUILD_ID and g is not None and getattr(g, "id", None) == GUILD_ID)


@bot.event
async def on_guild_channel_create(channel):
    if _is_primary_guild_channel(channel):
        asyncio.create_task(_debounced_channel_snapshot())


@bot.event
async def on_guild_channel_delete(channel):
    if _is_primary_guild_channel(channel):
        asyncio.create_task(_debounced_channel_snapshot())


@bot.event
async def on_guild_channel_update(before, after):
    if _is_primary_guild_channel(after):
        asyncio.create_task(_debounced_channel_snapshot())


@bot.event
async def on_ready():
    """Called on every (re)connection. Only idempotent operations here."""
    global _status_message_id, _first_ready
    # Schutz gegen None-Zustand bei fehlgeschlagener Verbindung
    if not bot.user:
        return
    logger.info(f"Recon online: {bot.user} (ID: {bot.user.id})")

    # Load persisted status message ID
    # Review-Fix 2026-06-12 (LOW): File-IO via to_thread statt blocking im Loop
    _status_message_id = await asyncio.to_thread(_load_status_message_id)
    if _status_message_id:
        logger.info(f"Loaded persisted status message ID: {_status_message_id}")

    # Configure notifier channels (safe to re-run on reconnect)
    notifier.set_channels(
        admin_log=ADMIN_LOG_CHANNEL_ID,
        notify_role=NOTIFY_ROLE_ID,
    )

    # Set presence (safe to re-run on reconnect)
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="Server Health",
        )
    )

    # Start background tasks (is_running guard prevents duplicates)
    core_tasks = [
        health_check_task, player_log_task, update_status_embed,
        update_voice_stats, optimize_task, login_audit_task,
        weekly_snapshot_task, health_auto_restart_task, ssl_check_task,
        ban_expiry_task, db_maintenance_task, channel_snapshot_task,
    ]
    # Web-Status-Task nur starten wenn aktiviert
    if web_status_generator:
        core_tasks.append(web_status_task)

    # MC-Tasks nur starten wenn MC-Server konfiguriert sind
    if mc_servers:
        core_tasks.extend([mc_chat_bridge_task, mc_health_check_task])

    # Fehler-Handler auf jede Schleife legen, BEVOR sie startet. Ohne das
    # beendet discord.py eine Schleife bei der ersten unerwarteten Ausnahme
    # endgueltig: der Dienst bleibt "active", die Aufgabe ist tot, und der
    # Ausfall sieht aus wie Ruhe — keine Backups, kein Offline-Alarm, keine
    # ablaufenden Bans.
    loop_guard.set_reporter(
        lambda titel, text: notifier.send_admin(titel, text, NotifyLevel.ERROR)
    )
    loop_guard.guard_all(*core_tasks)

    for task in core_tasks:
        if not task.is_running():
            task.start()

    # Minecraft Chat-Bridge Channel-Map befuellen
    _build_mc_chat_channel_map()

    # F28: SQLite-Datenbank initialisieren (idempotent bei Reconnect)
    db = None
    try:
        db = await init_db()
        # Beim ersten Start: JSON-Daten importieren falls DB leer
        if await check_import_needed(db):
            logger.info("Leere Datenbank erkannt — starte JSON-Import...")
            import_stats = await import_all(db)
            logger.info(f"JSON-Import abgeschlossen: {import_stats}")
    except Exception as e:
        logger.error(f"Datenbank-Initialisierung fehlgeschlagen: {e}")

    # StatsTracker aus SQLite laden
    try:
        await stats_tracker.load_from_db()
        for _st_sid, _st_tracker in mc_stats_trackers.items():
            await _st_tracker.load_from_db()
    except Exception as e:
        logger.warning(f"StatsTracker DB-Load fehlgeschlagen: {e}")

    # ------------------------------------------------------------------
    # UpdateManager pro MC-Server erstellen / aktualisieren
    # ------------------------------------------------------------------
    try:
        db_helper = DBHelper(db) if db else None

        if not hasattr(bot, 'mc_update_managers') or not bot.mc_update_managers:
            # Erster Start: UpdateManager-Instanzen erstellen

            # ModpackUpdater-Versionen aus DB laden (bevorzugt gegenueber ENV)
            for _um_sid, _um_mpu in mc_modpack_updaters.items():
                if db_helper:
                    await _um_mpu.load_from_db(db_helper)

            mc_update_managers: dict[str, UpdateManager] = {}
            for _um_sid, _um_srv in mc_servers.items():
                # Discord-Channel fuer Update-Nachrichten
                _um_channel = None
                if _um_srv.game_chat_channel_id:
                    _um_channel = bot.get_channel(_um_srv.game_chat_channel_id)

                _um = UpdateManager(
                    server_id=_um_sid,
                    mc_server=_um_srv,
                    modpack_updater=mc_modpack_updaters.get(_um_sid),
                    channel=_um_channel,
                    db=db_helper,
                    har=health_auto_restart,
                    notifier=notifier,
                    backup_manager=getattr(bot, 'mc_backup_mgrs', {}).get(_um_sid),
                    onedrive_backup=onedrive_backup,
                )
                mc_update_managers[_um_sid] = _um
                logger.info(f"UpdateManager [{_um_sid}] erstellt")

            bot.mc_update_managers = mc_update_managers

            # Chat-Bridge: Referenzen auf UpdateManager + MinecraftServer setzen
            for _um_sid, _um_bridge in mc_chat_bridges.items():
                _um_bridge.mc_server = mc_servers.get(_um_sid)
                _um_bridge.update_manager = mc_update_managers.get(_um_sid)

            # Crash-Recovery: Abgebrochene Updates pruefen und fortsetzen
            for _um_sid, _um_mgr in mc_update_managers.items():
                try:
                    await _um_mgr.check_and_resume()
                except Exception as e:
                    logger.error(f"[{_um_sid}] Crash-Recovery fehlgeschlagen: {e}")
        else:
            # Reconnect: Channel-Referenzen aktualisieren
            for _um_sid, _um_mgr in bot.mc_update_managers.items():
                _um_srv = mc_servers.get(_um_sid)
                if _um_srv and _um_srv.game_chat_channel_id:
                    _um_mgr.channel = bot.get_channel(_um_srv.game_chat_channel_id)

    except Exception as e:
        logger.error(f"UpdateManager-Initialisierung fehlgeschlagen: {e}")
        if not hasattr(bot, 'mc_update_managers'):
            bot.mc_update_managers = {}

    # F28-8: Boot-Restore — iptables-Bans aus DB wiederherstellen
    try:
        restored = await ban_manager.restore_bans()
        if restored > 0:
            logger.info(f"Boot-Restore: {restored} iptables-Bans wiederhergestellt")
    except Exception as e:
        logger.warning(f"Ban Boot-Restore fehlgeschlagen: {e}")

    # StatsCollector als Hintergrund-Task starten (nur beim ersten Start)
    if not stats_collector._running:
        await stats_collector.start()

    # StatusWriter als Hintergrund-Task starten (schreibt JSON fuer Dashboard)
    if not status_writer._running:
        await status_writer.start()

    # Phase 1: Neue Monitoring-Module starten (eigene asyncio Tasks)
    service_watchdog.start()
    disk_guard.start()
    duckdns_monitor.start()
    port_monitor.start()
    package_checker.start()

    if _first_ready:
        logger.info("All background tasks started")

        # Multi-Tenant-Registry befuellen: jede Guild die der Bot bedient in die
        # `guilds`-Tabelle (idempotenter Upsert). Fundament fuer Multi-Hub/Per-Guild.
        try:
            for _g in bot.guilds:
                await GuildConfig.ensure_guild(
                    _g.id,
                    name=_g.name,
                    owner_id=getattr(_g, "owner_id", None),
                )
            logger.info(f"Guild-Registry aktualisiert ({len(bot.guilds)} Guild(s))")
        except Exception as e:  # noqa: BLE001 — Registry-Fehler darf Ready nicht killen
            logger.warning(f"Guild-Registry-Update fehlgeschlagen: {e}")

        # Initialen Update-Check als Background-Task starten
        # (installierte + verfuegbare Build-ID fuer Dashboard)
        async def _initial_update_check():
            try:
                # Installierte Build-ID (schnell, liest lokale Datei)
                build_id = await update_checker._get_installed_buildid()
                if build_id:
                    update_checker.installed_buildid = build_id
                    logger.info(f"SAT installierte Build-ID: {build_id}")

                # Verfuegbare Build-ID via SteamCMD (kann bis 120s dauern)
                logger.info("SteamCMD Update-Check gestartet...")
                available = await update_checker._get_available_buildid()
                if available:
                    update_checker.last_known_buildid = available
                    logger.info(f"SAT verfuegbare Build-ID: {available}")
                    if build_id and available != build_id:
                        update_checker.update_available = True
                        logger.info(f"SAT Update verfuegbar: {build_id} -> {available}")
                else:
                    logger.warning("SteamCMD konnte verfuegbare Build-ID nicht ermitteln")
            except Exception as e:
                logger.warning(f"Initialer Update-Check fehlgeschlagen: {e}")

        track_task(_initial_update_check(), name="recon_bot.initial_update_check")

        # Initialen MC-Version-Check starten (Paper-basierte Server)
        async def _initial_mc_version_check():
            try:
                for mc_sid, mc_uc in mc_update_checkers.items():
                    version, build = await mc_uc._get_installed_version()
                    if version:
                        mc_uc.current_version = version
                        mc_uc.current_build = build
                        build_str = f" Build {build}" if build else ""
                        logger.info(f"MC [{mc_sid}] Version: {version}{build_str}")
                    else:
                        logger.debug(f"MC [{mc_sid}] Version konnte nicht ermittelt werden")
            except Exception as e:
                logger.debug(f"Initialer MC-Version-Check fehlgeschlagen: {e}")

        if mc_update_checkers:
            track_task(_initial_mc_version_check(), name="recon_bot.initial_mc_version_check")

        # Send startup notification only on first connect
        await notifier.send_admin(
            "Recon gestartet",
            "Alle Monitoring-Tasks aktiv.",
            NotifyLevel.SUCCESS,
        )
        _first_ready = False
    else:
        logger.info("Reconnected to Discord")


# ------------------------------------------------------------------
# Discord → Minecraft Chat-Bridge (on_message)
# ------------------------------------------------------------------

# Channel-ID → Server-ID Mapping (wird in on_ready befuellt)
_mc_chat_channel_map: dict[int, str] = {}


@bot.event
async def on_message(message: discord.Message):
    """Discord-Nachrichten an Minecraft weiterleiten"""
    # Bots ignorieren
    if message.author.bot:
        return

    # MC-Chat-Bridge: Nur konfigurierte Channels
    if message.channel.id in _mc_chat_channel_map:
        server_id = _mc_chat_channel_map[message.channel.id]
        srv = mc_servers.get(server_id)
        bridge = mc_chat_bridges.get(server_id)

        if srv and bridge:
            try:
                if await srv.is_running():
                    author_name = message.author.display_name
                    content = message.clean_content
                    if content:
                        await bridge.send_to_minecraft(srv, author_name, content)
            except Exception as e:
                logger.debug(f"Discord-Channel-Send / Stat-Sample swallowed (B110-refactor 3.1): {e}")

    # Commands immer verarbeiten (auch fuer nicht-MC Channels)
    await bot.process_commands(message)


# Channel-Map beim Start befuellen
def _build_mc_chat_channel_map():
    """Baut die Channel-ID → Server-ID Map auf"""
    _mc_chat_channel_map.clear()
    for sid, srv in mc_servers.items():
        if srv.game_chat_channel_id:
            _mc_chat_channel_map[srv.game_chat_channel_id] = sid
            logger.info(
                f"[{sid}] Chat-Bridge Channel: {srv.game_chat_channel_id}"
            )


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

async def shutdown():
    """Graceful shutdown: close sessions and resources"""
    logger.info("Shutting down...")
    player_tracker.close_all_sessions()
    await stats_collector.stop()
    # Phase 1: Neue Module stoppen
    service_watchdog.stop()
    disk_guard.stop()
    duckdns_monitor.stop()
    port_monitor.stop()
    package_checker.stop()
    await sat_api.close()
    # F28: Datenbank sauber schliessen (WAL-Checkpoint + Close)
    await close_db()
    logger.info("Cleanup complete")


def main():
    if not TOKEN:
        logger.error("DISCORD_TOKEN_WATCHDOG not set in .env!")
        sys.exit(1)

    # F62: Startup Selftest
    selftest_ok = execute_selftest(
        "Recon",
        required_env=["DISCORD_TOKEN_WATCHDOG", "GUILD_ID"],
    )
    if not selftest_ok:
        logger.error("Selftest fehlgeschlagen — Bot wird nicht gestartet!")
        sys.exit(1)

    logger.info("Starting Recon...")

    # F61: Cleanup-Callbacks registrieren
    async def _cleanup_monitor():
        try:
            await shutdown()
        except Exception as e:
            logger.debug(f"Cleanup error: {e}")

    register_cleanup(_cleanup_monitor)

    try:
        bot.run(TOKEN)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
        try:
            asyncio.run(shutdown())
        except (RuntimeError, Exception) as e:
            logger.debug(f"Shutdown nach KeyboardInterrupt fehlgeschlagen: {e}")
    except Exception as e:
        logger.error(f"Fatal: {e}", exc_info=True)
        try:
            asyncio.run(shutdown())
        except (RuntimeError, Exception) as e:
            logger.debug(f"Shutdown nach Fatal-Error fehlgeschlagen: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
