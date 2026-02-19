"""
Monitor Bot - Health Monitoring, Dashboard & Auto-Tasks
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
import discord
from discord.ext import commands, tasks
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.config import load_env, get_env, get_config
from utils.logger import get_logger
from utils.formatting import format_uptime, format_bytes, status_emoji, progress_bar

from modules.satisfactory.server import SatisfactoryServer
from modules.satisfactory.api_client import SatisfactoryAPI
from modules.monitoring.health_check import HealthChecker, ServerState
from modules.monitoring.performance import PerformanceMonitor, PerformanceThresholds
from modules.monitoring.player_tracker import PlayerTracker
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
from modules.monitoring.graceful_degradation import GracefulDegradation
from modules.monitoring.steam_changelog import SteamChangelog
from modules.config_validator import ConfigValidator
from modules.minecraft.server import MinecraftServer
from modules.minecraft.chat_bridge import MinecraftChatBridge

load_env()

# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------

TOKEN = get_env("DISCORD_TOKEN_WATCHDOG")
GUILD_ID = get_env("GUILD_ID", cast=int)
ADMIN_LOG_CHANNEL_ID = get_env("ADMIN_LOG_CHANNEL_ID", cast=int, default=0)
STATUS_EMBED_CHANNEL_ID = get_env("STATUS_EMBED_CHANNEL_ID", cast=int, default=0)
GAME_CHAT_CHANNEL_ID = get_env("GAME_CHAT_CHANNEL_ID", cast=int, default=0)
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

logger = get_logger("monitor_bot")
config = get_config()

# ------------------------------------------------------------------
# Bot Setup
# ------------------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

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
config_backup = ConfigBackup(
    project_root=PROJECT_ROOT,
    onedrive_backup=onedrive_backup,
    remote_path="Backups/ServerConfig",
    max_backups=7,
)

# Stats tracker (persisted history for reports)
stats_tracker = StatsTracker(data_dir=PROJECT_ROOT / "data")

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
    data_file=PROJECT_ROOT / "data" / "player_ips.json",
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
# Minecraft Multi-Server + Chat-Bridge
# ------------------------------------------------------------------

MC_SERVER_IDS = ["BMC", "VANILLA"]
mc_servers: dict[str, MinecraftServer] = {}
mc_chat_bridges: dict[str, MinecraftChatBridge] = {}

for _mc_sid in MC_SERVER_IDS:
    _mc_srv = MinecraftServer(_mc_sid)
    if _mc_srv.enabled:
        mc_servers[_mc_sid] = _mc_srv
        logger.info(f"Minecraft-Server aktiviert: {_mc_srv.display_name} ({_mc_sid})")

bot.mc_servers = mc_servers

# Chat-Bridge Callbacks (werden in on_ready mit Channels verbunden)


async def _mc_on_chat(server_id: str, player: str, message: str):
    """MC Chat → Discord Channel"""
    srv = mc_servers.get(server_id)
    if not srv or not srv.game_chat_channel_id:
        return
    channel = bot.get_channel(srv.game_chat_channel_id)
    if channel:
        # Embed fuer saubere Darstellung
        await channel.send(f"**<{player}>** {message}")


async def _mc_on_join(server_id: str, player: str):
    """MC Join → Discord Channel"""
    srv = mc_servers.get(server_id)
    if not srv or not srv.game_chat_channel_id:
        return
    channel = bot.get_channel(srv.game_chat_channel_id)
    if channel:
        await channel.send(f"**{player}** ist dem Server beigetreten")


async def _mc_on_leave(server_id: str, player: str):
    """MC Leave → Discord Channel"""
    srv = mc_servers.get(server_id)
    if not srv or not srv.game_chat_channel_id:
        return
    channel = bot.get_channel(srv.game_chat_channel_id)
    if channel:
        await channel.send(f"**{player}** hat den Server verlassen")


async def _mc_on_advancement(server_id: str, player: str, advancement: str):
    """MC Advancement → Discord Channel"""
    srv = mc_servers.get(server_id)
    if not srv or not srv.game_chat_channel_id:
        return
    channel = bot.get_channel(srv.game_chat_channel_id)
    if channel:
        await channel.send(f"**{player}** hat den Fortschritt **{advancement}** erreicht!")


async def _mc_on_death(server_id: str, player: str, death_msg: str):
    """MC Death → Discord Channel"""
    srv = mc_servers.get(server_id)
    if not srv or not srv.game_chat_channel_id:
        return
    channel = bot.get_channel(srv.game_chat_channel_id)
    if channel:
        await channel.send(f"{death_msg}")


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
                        embed = discord.Embed(
                            title="⚠️ Savegame Integritaetsproblem",
                            description="\n".join(integrity["issues"]),
                            color=0xff6600,
                            timestamp=datetime.now(),
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
                embed = discord.Embed(
                    title=f"🔍 Crash Replay #{crash_event.crash_number}",
                    description=(
                        f"Letzte {crash_replay.context_lines} Log-Zeilen vor dem Crash:\n"
                        f"```\n{summary}\n```"
                    ),
                    color=0xff0000,
                    timestamp=datetime.now(),
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
            embed = discord.Embed(
                description=desc, color=0xff0000,
                timestamp=datetime.now(),
            )
            await channel.send(
                content=f"<@&{NOTIFY_ROLE_ID}>" if NOTIFY_ROLE_ID else None,
                embed=embed,
            )
        elif is_new:
            # New player - blue embed, no ping
            embed = discord.Embed(
                description=(
                    f"🆕 Neuer Spieler: **{name}**\n"
                    f"IP: {ip_str}\n"
                    f"Automatisch zur Whitelist hinzugefügt."
                ),
                color=0x3498db,
                timestamp=datetime.now(),
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
                embed = discord.Embed(
                    title=f"\u26a0\ufe0f Service ausgefallen: {name}",
                    description=f"Fehler: {error}\n\nBot laeuft weiter mit eingeschraenkter Funktionalitaet.\nAutomatischer Retry aktiv.",
                    color=0xffa500,
                    timestamp=datetime.now(),
                )
                await channel.send(embed=embed)
            except Exception:
                pass


async def _on_service_recovered(name):
    if ADMIN_LOG_CHANNEL_ID:
        channel = bot.get_channel(ADMIN_LOG_CHANNEL_ID)
        if channel:
            try:
                embed = discord.Embed(
                    title=f"\u2705 Service wiederhergestellt: {name}",
                    color=0x00ff00,
                    timestamp=datetime.now(),
                )
                await channel.send(embed=embed)
            except Exception:
                pass


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

                embed = discord.Embed(
                    title="\U0001f6a8 CRASH LOOP ERKANNT!",
                    description=(
                        f"{result['crashes_in_window']} Crashes in {result['window_minutes']} Minuten.\n"
                        f"Auto-Restart wurde **deaktiviert**.\n\n"
                        f"Bitte manuell pruefen und mit `/sat start` neu starten."
                        f"{lkg_text}"
                    ),
                    color=0xff0000,
                    timestamp=datetime.now(),
                )
                content = f"<@&{NOTIFY_ROLE_ID}>" if NOTIFY_ROLE_ID else None
                await channel.send(content=content, embed=embed)
            except Exception as e:
                logger.error(f"Crash loop notification error: {e}")


savegame_protection.on_crash_loop = _on_crash_loop


# -- Login Audit Callbacks (Phase 4a) --

async def _on_unknown_login(alert):
    if ADMIN_LOG_CHANNEL_ID:
        channel = bot.get_channel(ADMIN_LOG_CHANNEL_ID)
        if channel:
            try:
                embed = discord.Embed(
                    title="\U0001f6a8 Unbekannter SSH-Login!",
                    description=(
                        f"**User:** {alert['user']}\n"
                        f"**IP:** `{alert['ip']}`\n"
                        f"**Zeit:** {alert['timestamp'][:19]}"
                    ),
                    color=0xff0000,
                    timestamp=datetime.now(),
                )
                content = f"<@&{NOTIFY_ROLE_ID}>" if NOTIFY_ROLE_ID else None
                await channel.send(content=content, embed=embed)
            except Exception:
                pass

    await email_notifier.send_security_alert(
        f"SSH-Login von unbekannter IP: {alert['user']}@{alert['ip']}"
    )


async def _on_failed_login_burst(alert):
    if ADMIN_LOG_CHANNEL_ID:
        channel = bot.get_channel(ADMIN_LOG_CHANNEL_ID)
        if channel:
            try:
                embed = discord.Embed(
                    title="\u26a0\ufe0f SSH Brute-Force erkannt",
                    description=(
                        f"**IP:** `{alert['ip']}`\n"
                        f"**Versuche:** {alert['count']}+\n"
                        f"Fail2Ban sollte diese IP sperren."
                    ),
                    color=0xffa500,
                    timestamp=datetime.now(),
                )
                await channel.send(embed=embed)
            except Exception:
                pass


login_audit.on_unknown_login = _on_unknown_login
login_audit.on_failed_login_burst = _on_failed_login_burst


# ------------------------------------------------------------------
# Player Detection via Server Log
# ------------------------------------------------------------------

_PLAYER_JOIN_RE = re.compile(
    r'LogNet:.*?Join succeeded:\s*(.+?)$', re.MULTILINE
)
_PLAYER_LEAVE_RE = re.compile(
    r'LogNet:.*?UNetConnection::Close.*?PlayerName=(.+?)[\s,]', re.MULTILINE
)

_sat_log_path = (
    sat_server.server_path / "FactoryGame" / "Saved" / "Logs" / "FactoryGame.log"
)
_log_last_pos: int = 0
_log_last_size: int = 0
_log_lock = asyncio.Lock()
_status_lock = asyncio.Lock()


def _init_log_position():
    """Seek to end of log to avoid replaying old events"""
    global _log_last_pos, _log_last_size
    try:
        if _sat_log_path.exists():
            _log_last_size = _sat_log_path.stat().st_size
            _log_last_pos = _log_last_size
    except Exception as e:
        logger.debug(f"Log-Position initialisieren fehlgeschlagen: {e}")


async def _poll_player_events():
    """
    Read new log lines and detect join/leave events.
    Updates player_tracker with current player set.
    """
    global _log_last_pos, _log_last_size

    if not _sat_log_path.exists():
        return

    async with _log_lock:
        try:
            current_size = _sat_log_path.stat().st_size

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

            # Detect events and let player_tracker handle them
            joined = set()
            left = set()

            for match in _PLAYER_JOIN_RE.finditer(new_content):
                name = match.group(1).strip()
                if name:
                    joined.add(name)

            for match in _PLAYER_LEAVE_RE.finditer(new_content):
                name = match.group(1).strip()
                if name:
                    left.add(name)

            # Build current set: previous online + joins - leaves
            current = player_tracker.get_online_players()
            current = (current | joined) - left
            await player_tracker.update(current)

        except Exception as e:
            logger.debug(f"Player log poll error: {e}")


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
        logger.debug(f"Player log poll error: {e}")


@player_log_task.before_loop
async def before_player_log():
    await bot.wait_until_ready()
    await asyncio.sleep(12)
    _init_log_position()


# ------------------------------------------------------------------
# Minecraft Chat-Bridge Task
# ------------------------------------------------------------------

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
            logger.debug(f"[{sid}] Chat-Bridge Poll Fehler: {e}")


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
                _mc_consecutive_offline[sid] = _mc_consecutive_offline.get(sid, 0) + 1
                count = _mc_consecutive_offline[sid]

                # Nach 3 aufeinanderfolgenden Checks (6 Min) benachrichtigen
                if count >= 3 and not _mc_downtime_notified.get(sid, False):
                    _mc_downtime_notified[sid] = True
                    admin_ch = bot.get_channel(ADMIN_LOG_CHANNEL_ID)
                    if admin_ch:
                        try:
                            embed = discord.Embed(
                                title=f"Minecraft {srv.display_name} offline",
                                description=(
                                    f"{srv.display_name} ist seit {count * 2} Minuten "
                                    f"nicht erreichbar.\n"
                                    f"Service: `{srv.service_name}`"
                                ),
                                color=0xff0000,
                                timestamp=datetime.now(),
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
                                embed = discord.Embed(
                                    title=f"Minecraft {srv.display_name} wieder online",
                                    description=(
                                        f"Server ist wieder erreichbar.\n"
                                        f"War {_mc_consecutive_offline[sid] * 2} Minuten offline."
                                    ),
                                    color=0x00ff00,
                                    timestamp=datetime.now(),
                                )
                                await admin_ch.send(embed=embed)
                            except Exception as e:
                                logger.debug(f"[{sid}] Recovery-Notification Fehler: {e}")

                _mc_consecutive_offline[sid] = 0

                # Spielerzahl fuer Stats tracken
                try:
                    online, max_p = await srv.get_player_count()
                    stats_tracker.record_player_count(online)
                except Exception:
                    pass

        except Exception as e:
            logger.debug(f"[{sid}] MC Health-Check Fehler: {e}")


@mc_health_check_task.before_loop
async def before_mc_health_check():
    await bot.wait_until_ready()
    await asyncio.sleep(30)  # Nach anderen Tasks starten


@tasks.loop(seconds=60)
async def login_audit_task():
    """Check auth.log for SSH login events every 60 seconds"""
    if not config.get("features", {}).get("login_audit", True):
        return
    try:
        await login_audit.check()
    except Exception as e:
        logger.debug(f"Login audit error: {e}")


@login_audit_task.before_loop
async def before_login_audit():
    await bot.wait_until_ready()
    await asyncio.sleep(20)
    login_audit.init_position()


@tasks.loop(seconds=120)
async def health_check_task():
    """Health check every 2 minutes"""
    global _consecutive_offline_checks, _downtime_notified
    try:
        status = await health_checker.check()

        # -- Downtime Notification (Phase 10b) --
        if status.state != ServerState.ONLINE:
            _consecutive_offline_checks += 1

            # After 3 consecutive checks (6 minutes) offline and not yet notified
            if _consecutive_offline_checks >= 3 and not _downtime_notified and GAME_CHAT_CHANNEL_ID:
                _downtime_notified = True
                channel = bot.get_channel(GAME_CHAT_CHANNEL_ID)
                if channel:
                    try:
                        embed = discord.Embed(
                            title="🔴 Server ist offline",
                            description=(
                                f"Der Satisfactory-Server ist derzeit nicht erreichbar.\n"
                                f"Dies wurde um {datetime.now().strftime('%H:%M:%S')} erkannt.\n\n"
                                f"Status wird weiterhin überwacht..."
                            ),
                            color=0xff0000,
                            timestamp=datetime.now(),
                        )
                        await channel.send(embed=embed)
                        logger.info(f"Downtime notification sent to {GAME_CHAT_CHANNEL_ID}")
                    except Exception as e:
                        logger.error(f"Failed to send downtime notification: {e}")
        else:
            # Server is online
            if _consecutive_offline_checks > 0:
                # Server was offline, now back online
                if _downtime_notified and GAME_CHAT_CHANNEL_ID:
                    _downtime_notified = False
                    channel = bot.get_channel(GAME_CHAT_CHANNEL_ID)
                    if channel:
                        try:
                            embed = discord.Embed(
                                title="🟢 Server ist wieder online",
                                description=(
                                    f"Der Server ist um {datetime.now().strftime('%H:%M:%S')} wieder erreichbar.\n"
                                    f"Der Server war {_consecutive_offline_checks * 2} Minuten offline."
                                ),
                                color=0x00ff00,
                                timestamp=datetime.now(),
                            )
                            await channel.send(embed=embed)
                            logger.info(f"Server recovery notification sent")
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
            except Exception:
                pass

        # Sanity check: log polling runs in player_log_task (10s)
        # Here we only do the API cross-check every 2 min
        if status.state == ServerState.ONLINE:
            # Sanity check: if API says 0 players but tracker has some,
            # close all sessions (server might have restarted without us noticing)
            api_count = status.players_online
            tracked = player_tracker.get_online_players()
            if api_count == 0 and len(tracked) > 0:
                logger.info(
                    f"API reports 0 players but tracker has {len(tracked)}, "
                    f"closing stale sessions"
                )
                player_tracker.close_all_sessions()
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
    await _update_status_embed_impl()


async def _update_status_embed_impl():
    """Implementation of status embed update (callable from command too)"""
    global _status_message_id

    if not STATUS_EMBED_CHANNEL_ID:
        return

    channel = bot.get_channel(STATUS_EMBED_CHANNEL_ID)
    if not channel:
        return

    # Load persisted message ID on first run
    if _status_message_id is None:
        _status_message_id = _load_status_message_id()

    status = health_checker.status
    running = status.process_running
    now = datetime.now()

    # Get API data for extra info
    api_state = None
    if running:
        try:
            api_state = await sat_api.query_server_state()
        except Exception:
            pass

    # Get savegame stats (cached, non-blocking)
    world_stats = None
    try:
        world_stats = await savegame_analyzer.get_stats()
    except Exception as e:
        logger.debug(f"Savegame stats unavailable: {e}")

    # -- Build Embed --
    lines = []
    lines.append("▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬")
    lines.append("**LIVE SERVER STATUS**")
    lines.append("▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬")
    lines.append("")

    # -- Satisfactory Server --
    dot = "🟢" if running else "🔴"
    state_text = "Online" if running else "Offline"

    lines.append("🏭 **Satisfactory Server**")

    if running:
        uptime_str = format_uptime(status.uptime)
        player_count = status.players_online
        player_limit = status.player_limit

        # Override with API data if available
        if api_state and api_state.num_players >= 0:
            player_count = api_state.num_players
        if api_state and api_state.player_limit > 0:
            player_limit = api_state.player_limit

        lines.append(
            f"{dot} {state_text} | "
            f"{player_count}/{player_limit} Players | "
            f"🕐 {uptime_str}"
        )

        # Online players
        online = player_tracker.get_online_players()
        if online:
            player_list = "\n".join(f"  👤 {name}" for name in sorted(online))
            lines.append(f"\n{player_list}")
        else:
            lines.append("\nKeine Spieler online")

        # -- World Stats (from savegame) --
        if world_stats and world_stats.available:
            lines.append("")
            power_str = f"{world_stats.total_power_mw:,.0f} MW".replace(",", ".")
            lines.append(
                f"🏗️ {world_stats.total_buildings:,} Gebäude | "
                f"⚡ {world_stats.generators} Gen ({power_str})"
            )
            lines.append(
                f"🏭 {world_stats.production_machines} Prod | "
                f"📦 {world_stats.conveyor_belts} Bänder | "
                f"🔧 {world_stats.pipes} Rohre"
            )
            if world_stats.trains > 0 or world_stats.vehicles > 0:
                lines.append(
                    f"🚂 {world_stats.trains} Züge | "
                    f"🚗 {world_stats.vehicles} Fahrzeuge"
                )
        elif world_stats and world_stats.session_name:
            lines.append(f"\n📋 Session: {world_stats.session_name}")
            if world_stats.play_hours > 0:
                lines.append(f"⏱️ Spielzeit: {world_stats.play_hours}h")

        # -- Save Info --
        if world_stats:
            save_parts = []
            if world_stats.save_size:
                save_parts.append(f"💾 {world_stats.save_size}")
            if world_stats.play_hours > 0:
                save_parts.append(f"⏱️ {world_stats.play_hours}h Spielzeit")
            if save_parts:
                lines.append("\n" + " | ".join(save_parts))

        # -- Next Restart --
        try:
            scheduler_cog = bot.get_cog("SchedulerCog")
            if scheduler_cog:
                restart_hour = getattr(scheduler_cog, '_daily_restart_hour', 4)
                restart_min = getattr(scheduler_cog, '_daily_restart_minute', 0)
                next_restart = now.replace(
                    hour=restart_hour, minute=restart_min,
                    second=0, microsecond=0
                )
                if next_restart <= now:
                    next_restart += timedelta(days=1)

                delta = next_restart - now
                hours_left = int(delta.total_seconds() // 3600)
                mins_left = int((delta.total_seconds() % 3600) // 60)
                lines.append(f"\n🔄 Nächster Restart: {hours_left}h {mins_left}m")
        except Exception:
            pass

    else:
        lines.append(f"{dot} {state_text}")
        lines.append("\nServer ist offline")

    # -- Tick Rate (only when online) --
    if running:
        tick_rate = 0.0
        if api_state and hasattr(api_state, 'average_tick_rate'):
            tick_rate = api_state.average_tick_rate
        elif status.tick_rate > 0:
            tick_rate = status.tick_rate

        if tick_rate > 0:
            tick_icon = "🟢" if tick_rate >= 25 else "🟡" if tick_rate >= 15 else "🔴"
            lines.append(f"\n🎯 Tick Rate: {tick_icon} {tick_rate:.1f}/30")

    # -- Update Available --
    if update_checker and update_checker.update_available:
        lines.append("\n📦 **Server-Update verfuegbar!**")

    lines.append("\n▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬")

    # -- Footer --
    lines.append("")
    lines.append(
        f"📊 Updates every 5 minutes • "
        f"Last update • {now.strftime('%d.%m.%Y %H:%M')}"
    )

    embed = discord.Embed(
        description="\n".join(lines),
        color=0x00ff00 if running else 0xff0000,
    )

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
            _save_status_message_id(msg.id)
        except Exception as e:
            logger.error(f"Status embed error: {e}")


# Expose for cog access
bot.update_status_embed_now = _update_status_embed_impl


@update_status_embed.before_loop
async def before_status_embed():
    await bot.wait_until_ready()
    await asyncio.sleep(20)


@tasks.loop(seconds=300)
async def update_voice_stats():
    """Update voice channel stats every 5 minutes"""
    if not VOICE_STATS_CATEGORY_ID or not GUILD_ID:
        return

    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    # Use direct server check for most reliable status
    try:
        running = await sat_server.is_running()
    except Exception:
        running = False

    # Get player info from health checker or API
    players_online = 0
    player_limit = 0
    if running:
        status = health_checker.status
        players_online = status.players_online
        player_limit = status.player_limit

        # Try API for more accurate data
        try:
            api_state = await sat_api.query_server_state()
            if api_state:
                if api_state.num_players >= 0:
                    players_online = api_state.num_players
                if api_state.player_limit > 0:
                    player_limit = api_state.player_limit
        except Exception:
            pass

    channel = guild.get_channel(VOICE_STATS_CATEGORY_ID)
    if not channel:
        return

    # Collect voice channels to update
    if isinstance(channel, discord.CategoryChannel):
        voice_channels = channel.voice_channels
    elif isinstance(channel, discord.VoiceChannel):
        voice_channels = [channel]
    else:
        return

    for vc in voice_channels:
        try:
            if "SAT" in vc.name.upper() or vc.id == VOICE_STATS_CATEGORY_ID:
                if running:
                    new_name = f"SAT-1 | 🟢 {players_online}/{player_limit}"
                else:
                    new_name = "SAT-1 | 🔴 Offline"
                if vc.name != new_name:
                    await vc.edit(name=new_name)

        except discord.Forbidden:
            logger.warning(f"No permission to edit voice channel: {vc.name}")
        except Exception as e:
            logger.error(f"Voice stats error: {e}")


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
# Bot Events
# ------------------------------------------------------------------

_first_ready = True  # Track first on_ready vs reconnects


@bot.event
async def setup_hook():
    """Called once before the bot connects. Load cogs and sync commands here."""
    # Load cogs (runs exactly once)
    cog_list = ["cogs.monitor_cog", "cogs.scheduler_cog"]
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

    logger.info("Setup hook complete")


@bot.event
async def on_ready():
    """Called on every (re)connection. Only idempotent operations here."""
    global _status_message_id, _first_ready
    logger.info(f"Monitor Bot online: {bot.user} (ID: {bot.user.id})")

    # Load persisted status message ID
    _status_message_id = _load_status_message_id()
    if _status_message_id:
        logger.info(f"Loaded persisted status message ID: {_status_message_id}")

    # Configure notifier channels (safe to re-run on reconnect)
    notifier.set_channels(
        admin_log=ADMIN_LOG_CHANNEL_ID,
        game_chat=GAME_CHAT_CHANNEL_ID,
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
    for task in [health_check_task, player_log_task, mc_chat_bridge_task, mc_health_check_task, update_status_embed, update_voice_stats, optimize_task, login_audit_task, weekly_snapshot_task]:
        if not task.is_running():
            task.start()

    # Minecraft Chat-Bridge Channel-Map befuellen
    _build_mc_chat_channel_map()

    if _first_ready:
        logger.info("All background tasks started")
        # Send startup notification only on first connect
        await notifier.send_admin(
            "Monitor Bot gestartet",
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

    # Nur konfigurierte MC-Chat-Channels
    if message.channel.id not in _mc_chat_channel_map:
        return

    server_id = _mc_chat_channel_map[message.channel.id]
    srv = mc_servers.get(server_id)
    bridge = mc_chat_bridges.get(server_id)

    if not srv or not bridge:
        return

    # Nur weiterleiten wenn Server laeuft
    try:
        if not await srv.is_running():
            return
    except Exception:
        return

    # An Minecraft senden
    author_name = message.author.display_name
    content = message.clean_content
    if content:
        await bridge.send_to_minecraft(srv, author_name, content)

    # Commands trotzdem verarbeiten
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
    await sat_api.close()
    logger.info("Cleanup complete")


def main():
    if not TOKEN:
        logger.error("DISCORD_TOKEN_WATCHDOG not set in .env!")
        sys.exit(1)

    logger.info("Starting Monitor Bot...")
    try:
        bot.run(TOKEN)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
        try:
            asyncio.run(shutdown())
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Fatal: {e}", exc_info=True)
        try:
            asyncio.run(shutdown())
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
