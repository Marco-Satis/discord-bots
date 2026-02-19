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
import asyncio
import discord
from discord.ext import commands
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.config import load_env, get_env, get_config, DATA_DIR
from utils.logger import get_logger
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

# Load environment
load_env()

# Configuration
TOKEN = get_env("DISCORD_TOKEN_MANAGER")
GUILD_ID = get_env("GUILD_ID", cast=int)

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
    help_command=None  # We use our own /help
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
# Events
# ------------------------------------------------------------------

@bot.event
async def on_ready():
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
    # Initialize async managers
    await bot.whitelist_mgr.load()
    await bot.blacklist_mgr.load()
    await bot.blueprint_mgr.load()
    await bot.backup_mgr.load()
    await bot.word_filter.load()
    logger.info("All managers initialized")
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

    logger.info("Starting GameServer Bot...")
    logger.info(f"Server: {bot.sat_server.service_name}")
    logger.info(f"API: {bot.sat_api.host}:{bot.sat_api.port}")

    async def shutdown():
        """Graceful cleanup on shutdown"""
        try:
            await bot.sat_api.close()
            logger.info("API session closed")
        except Exception as e:
            logger.debug(f"Shutdown cleanup error: {e}")

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
