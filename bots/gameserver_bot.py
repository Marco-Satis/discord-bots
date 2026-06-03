"""
GameServer Bot - Satisfactory Server Control & Management
Bot 1 of 2 - handles all /sat commands, /help, /server, /timeout

Architecture:
  - Cog-based design for hot-reloading
  - systemd for server management (not screen)
  - Satisfactory HTTPS API for game data
  - Command permissions: Owner > Admin > Spieler > Alle
"""

import os
import sys
import json
import time
import asyncio
import discord
from discord.ext import commands, tasks
from pathlib import Path
from datetime import datetime, timezone

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.config import load_env, get_env, get_config, DATA_DIR
from utils.logger import get_logger
from utils.selftest import execute_selftest
from utils.shutdown import setup_signal_handlers, register_cleanup
from modules.database.db_manager import init_db, close_db
from modules.satisfactory.server import SatisfactoryServer
from modules.satisfactory.api_client import SatisfactoryAPI
from modules.satisfactory.whitelist import WhitelistManager
from modules.satisfactory.blacklist import BlacklistManager
from modules.satisfactory.blueprint_manager import BlueprintManager
from modules.satisfactory.savegame_stats import SavegameStats
from modules.backup.backup_manager import BackupManager
from modules.restart_timer import RestartTimerManager
from modules.word_filter import WordFilter
from modules.anti_spam import AntiSpam
from modules.command_logger import CommandLogger
from modules.monitoring.update_checker import UpdateChecker
from modules.satisfactory.settings_backup import SettingsBackup
from modules.mod_manager import ModManager
from modules.maintenance import BotMaintenance
from modules.guild_context import get_primary_guild_id

# Load environment
load_env()

# Configuration
TOKEN = get_env("DISCORD_TOKEN_MANAGER")
GUILD_ID = get_primary_guild_id()  # zentralisiert via Multi-Tenant-Resolver (Phase 0.5)

logger = get_logger("gameserver_bot")


# ------------------------------------------------------------------
# Bot Setup
# ------------------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None,  # We use our own /help
    allowed_mentions=discord.AllowedMentions.none(),
)

# Attach shared instances to bot for cog access
bot.sat_server = SatisfactoryServer(
    service_name=get_env("SATISFACTORY_SERVICE", "satisfactory.service"),
    server_user=get_env("SATISFACTORY_USER", "satisfactory"),
    server_path=get_env("SATISFACTORY_SERVER_PATH",
                        "/home/satisfactory/SatisfactoryDedicatedServer")
)

bot.sat_api = SatisfactoryAPI(
    host=get_env("API_HOST", "127.0.0.1"),
    port=get_env("API_PORT", 7777, cast=int),
    token=get_env("API_TOKEN"),
    verify_ssl=get_env("API_VERIFY_SSL", False, cast=bool)
)

bot.config = get_config()

# Phase 2: Whitelist, Blacklist, Blueprints, Savegame Stats, Backup
bot.whitelist_mgr = WhitelistManager()
bot.blacklist_mgr = BlacklistManager()
bot.blueprint_mgr = BlueprintManager(
    blueprint_path=bot.sat_server.blueprint_path
)
bot.savegame_stats = SavegameStats(
    savegame_path=bot.sat_server.savegame_path
)
bot.backup_mgr = BackupManager(
    savegame_path=bot.sat_server.savegame_path,
    backup_path=Path(get_env("BACKUP_PATH", "/home/satisfactory/backups")),
    max_backups=bot.config.get("backup", {}).get("max_local", 20)
)

# Phase 3: Restart Timer, Word Filter, Anti-Spam, Command Logger
bot.timer_mgr = RestartTimerManager()
bot.word_filter = WordFilter()
bot.anti_spam = AntiSpam(
    max_messages=5, window_seconds=10, cooldown_seconds=30,
    command_limit=3, command_window=10
)

bot.command_logger = CommandLogger()
bot.update_checker = UpdateChecker(
    steamcmd_path=get_env("STEAMCMD_PATH", "/usr/games/steamcmd"),
    install_dir=get_env("SATISFACTORY_SERVER_PATH",
                        "/home/satisfactory/SatisfactoryDedicatedServer"),
)

# Phase 3: Settings Backup
bot.settings_backup = SettingsBackup(
    api=bot.sat_api,
    backup_dir=DATA_DIR / "settings_backups",
)

# Phase 4: Mod Management & Maintenance
# Minecraft Multi-Server (conditional per Server-ID)
from modules.minecraft.server import MinecraftServer
bot.mc_servers: dict[str, MinecraftServer] = {}

MC_SERVER_IDS = ["BMC", "VANILLA"]
for _sid in MC_SERVER_IDS:
    _srv = MinecraftServer(_sid)
    if _srv.enabled:
        bot.mc_servers[_sid] = _srv
        logger.info(f"Minecraft-Server aktiviert: {_srv.display_name} ({_sid})")

if bot.mc_servers:
    from modules.minecraft.backup import MinecraftBackupManager
    bot.mc_backup_mgrs: dict[str, MinecraftBackupManager] = {}
    for _sid, _srv in bot.mc_servers.items():
        bot.mc_backup_mgrs[_sid] = MinecraftBackupManager(
            savegame_path=_srv.world_path,
            backup_path=_srv.backup_path,
            max_backups=bot.config.get("backup", {}).get("max_local", 20),
            server_id=_sid,
        )

# ModpackUpdater fuer minecraft_cog (Versions-Check, Status-Abfrage)
from modules.minecraft.modpack_updater import ModpackUpdater
mc_modpack_updaters: dict[str, ModpackUpdater] = {}
for _mc_sid in bot.mc_servers:
    _mpu = ModpackUpdater.from_env(server_id=_mc_sid)
    if _mpu.enabled:
        mc_modpack_updaters[_mc_sid] = _mpu
        logger.info(f"ModpackUpdater aktiviert: {_mc_sid} (v{_mpu.current_version})")
    else:
        logger.debug(f"ModpackUpdater fuer {_mc_sid} nicht konfiguriert — uebersprungen")
bot.mc_modpack_updaters = mc_modpack_updaters
bot.modpack_updater = mc_modpack_updaters.get("BMC", ModpackUpdater())

# Phase 8e: MC Blacklist (serveruebergreifend)
from modules.minecraft.blacklist import MinecraftBlacklist
bot.mc_blacklist = MinecraftBlacklist()

# Phase 10c: MC IP-Tracker (fuer IP-basierte Bans via UFW)
from modules.monitoring.player_ip_tracker import PlayerIPTracker
bot.mc_ip_trackers: dict[str, PlayerIPTracker] = {}
for _sid in bot.mc_servers:
    bot.mc_ip_trackers[_sid] = PlayerIPTracker(
        game_type="mc",
    )
    logger.info(f"MC IP-Tracker aktiviert: {_sid}")

# Mod management
bot.sat_mod_mgr = ModManager("satisfactory",
                             server_path=bot.sat_server.server_path,
                             mods_dir=DATA_DIR / "mods" / "satisfactory")
bot.mc_mod_mgrs: dict[str, ModManager] = {}
for _sid, _srv in bot.mc_servers.items():
    try:
        bot.mc_mod_mgrs[_sid] = ModManager(
            "minecraft",
            server_path=_srv.server_path,
            mods_dir=_srv.server_path / "mods"
        )
    except Exception as _e:
        logger.warning(f"ModManager fuer MC-{_sid} nicht initialisiert: {_e}")

# Maintenance
bot.maintenance = BotMaintenance()

# Cogs to load at startup
# Feature toggles from config.json control optional cogs
features = bot.config.get("features", {})

INITIAL_COGS = [
    # Unified Satisfactory cog (all /sat commands with sub-groups)
    "cogs.satisfactory_cog",
    # General commands (/help, /server, /ping, /reload)
    "cogs.general_cog",
    # Moderation (/timeout)
    "cogs.timeout_cog",
    # Mod management
    "cogs.mod_cog",
    # Maintenance
    "cogs.maintenance_cog",
]

# Conditional cogs based on environment
if bot.mc_servers:
    INITIAL_COGS.append("cogs.minecraft_cog")
    logger.info(f"Feature enabled: minecraft ({len(bot.mc_servers)} Server)")
else:
    logger.info("Feature disabled: minecraft (kein MC_*_SERVICE konfiguriert)")


# ------------------------------------------------------------------
# Bot-Status-Writer (Ping + Uptime fuer Dashboard)
# ------------------------------------------------------------------

_gs_bot_start_time = time.time()
_GS_STATUS_DIR = DATA_DIR / "gameserver"


def _write_gs_bot_status():
    """Schreibt GameServer Bot Status (Ping, Uptime) als JSON fuer Dashboard."""
    try:
        _GS_STATUS_DIR.mkdir(parents=True, exist_ok=True)
        uptime_secs = int(time.time() - _gs_bot_start_time)
        hours = uptime_secs // 3600
        mins = (uptime_secs % 3600) // 60
        if hours > 0:
            uptime_str = f"{hours}h {mins}m"
        else:
            uptime_str = f"{mins}m"

        data = {
            "status": "online" if bot.is_ready() else "offline",
            "ping_ms": round(bot.latency * 1000) if bot.latency and bot.latency != float("inf") else 0,
            "uptime": uptime_str,
            "last_update": datetime.now(timezone.utc).isoformat(),
        }
        tmp = _GS_STATUS_DIR / "bot_status.json.tmp"
        target = _GS_STATUS_DIR / "bot_status.json"
        with open(tmp, "w") as f:
            json.dump(data, f)
        tmp.replace(target)
    except Exception as e:
        logger.debug(f"Bot-Status schreiben fehlgeschlagen: {e}")


@tasks.loop(seconds=30)
async def gs_status_writer_task():
    """Schreibt Bot-Status alle 30 Sekunden."""
    await asyncio.to_thread(_write_gs_bot_status)


@gs_status_writer_task.before_loop
async def before_gs_status_writer():
    await bot.wait_until_ready()
    await asyncio.sleep(5)


# ------------------------------------------------------------------
# Events
# ------------------------------------------------------------------

@bot.event
async def on_ready():
    # Schutz gegen None-Zustand bei fehlgeschlagener Verbindung
    if not bot.user:
        return
    logger.info(f"GameServer Bot online: {bot.user} (ID: {bot.user.id})")
    logger.info(f"Guilds: {[g.name for g in bot.guilds]}")

    # Idempotent operations only (safe to re-run on reconnect)
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="Satisfactory Server"
        )
    )

    # Initialize command logger with admin channel
    admin_channel_id = get_env("ADMIN_LOG_CHANNEL_ID", cast=int)
    if admin_channel_id:
        admin_ch = bot.get_channel(admin_channel_id)
        if admin_ch:
            bot.command_logger.set_admin_channel(admin_ch)
            if getattr(bot, '_first_ready', False):
                logger.info(f"Command logger connected to #{admin_ch.name}")

    # Bot-Status-Writer starten (Ping fuer Dashboard)
    if not gs_status_writer_task.is_running():
        gs_status_writer_task.start()

    if getattr(bot, '_first_ready', False):
        bot._first_ready = False


@bot.event
async def on_app_command_completion(interaction: discord.Interaction, command):
    """Log every successful slash command execution"""
    try:
        params = {}
        if interaction.namespace:
            for key, value in interaction.namespace.__dict__.items():
                # Serialize attachments/members to strings
                if isinstance(value, discord.Attachment):
                    params[key] = value.filename
                elif isinstance(value, discord.Member):
                    params[key] = str(value)
                else:
                    params[key] = str(value)[:100]

        await bot.command_logger.log_command(
            interaction,
            params=params,
            success=True
        )
    except Exception as e:
        logger.debug(f"Command logging failed: {e}")


@bot.event
async def on_command_error(ctx, error):
    logger.error(f"Command error: {error}", exc_info=True)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    if isinstance(error, discord.app_commands.CheckFailure):
        # Permission denied - already handled by decorators
        return
    logger.error(f"App command error: {error}", exc_info=True)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                f"Ein Fehler ist aufgetreten: {error}", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"Ein Fehler ist aufgetreten: {error}", ephemeral=True
            )
    except Exception as e:
        logger.debug(f"Fehler-Antwort konnte nicht gesendet werden: {e}")


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

async def load_cogs():
    """Load all cogs"""
    for cog in INITIAL_COGS:
        try:
            await bot.load_extension(cog)
            logger.info(f"Cog loaded: {cog}")
        except Exception as e:
            logger.error(f"Failed to load cog {cog}: {e}", exc_info=True)


@bot.event
async def setup_hook():
    # F28: SQLite-Datenbank initialisieren
    try:
        await init_db()
        logger.info("SQLite-Datenbank initialisiert")
    except Exception as e:
        logger.error(f"Datenbank-Initialisierung fehlgeschlagen: {e}")

    # Initialize async managers
    await bot.blueprint_mgr.load()
    await bot.backup_mgr.load()
    await bot.word_filter.load()
    logger.info("Blueprint/Backup/WordFilter managers initialized")

    # Daten aus SQLite laden (alleinige Datenquelle)
    try:
        await bot.whitelist_mgr.load_from_db()
        await bot.blacklist_mgr.load_from_db()
        await bot.mc_blacklist.load_from_db()
        logger.info("Whitelist/Blacklist aus SQLite geladen")
    except Exception as e:
        logger.warning(f"SQLite-Load fuer Listen fehlgeschlagen: {e}")
    await load_cogs()

    # Sync slash commands (once, not on every reconnect)
    if GUILD_ID:
        guild = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        logger.info(f"Synced {len(synced)} commands to guild {GUILD_ID}")
    else:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} commands globally")

    bot._first_ready = True
    logger.info("Setup hook complete")


def main():
    if not TOKEN:
        logger.error("DISCORD_TOKEN_MANAGER not set in .env!")
        sys.exit(1)

    # F62: Startup Selftest — pruefen ob alles konfiguriert ist
    selftest_ok = execute_selftest(
        "GameServer Bot",
        required_env=["DISCORD_TOKEN_MANAGER", "GUILD_ID"],
    )
    if not selftest_ok:
        logger.error("Selftest fehlgeschlagen — Bot wird nicht gestartet!")
        sys.exit(1)

    logger.info("Starting GameServer Bot...")
    logger.info(f"Server: {bot.sat_server.service_name}")
    logger.info(f"API: {bot.sat_api.host}:{bot.sat_api.port}")

    # F61: Cleanup-Callbacks registrieren
    async def _cleanup_api():
        try:
            await bot.sat_api.close()
            logger.info("API session closed")
        except Exception as e:
            logger.debug(f"API cleanup error: {e}")

    async def _cleanup_gs_db():
        try:
            await close_db()
        except Exception as e:
            logger.debug(f"DB cleanup error: {e}")

    register_cleanup(_cleanup_api)
    register_cleanup(_cleanup_gs_db)

    try:
        bot.run(TOKEN)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
