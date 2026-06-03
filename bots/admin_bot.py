"""
Admin Bot — Discord-Moderation, Community-Features & TeamSpeak-Steuerung
Bot 3 of 3 — unabhaengig von Gameservern

Architektur:
  - Cog-basiertes Design mit Hot-Reloading
  - Features: Moderation, Reaction Roles, Leveling, Tickets, Audit, Giveaways,
              Temp Voice, TeamSpeak, Server Backup
  - Berechtigungen: Owner > Admin > Spieler > Alle
"""

import sys
import json
import time
import asyncio
import discord
from discord.ext import commands, tasks
from pathlib import Path
from datetime import datetime, timezone

# Projektroot zum Pfad hinzufuegen
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.config import load_env, get_env, get_config, DATA_DIR
from utils.logger import get_logger
from utils.selftest import execute_selftest
from utils.shutdown import setup_signal_handlers, register_cleanup
from modules.database.db_manager import init_db, close_db
from modules.guild_context import get_primary_guild_id

# Umgebung laden
load_env()

# Konfiguration
TOKEN = get_env("ADMIN_BOT_TOKEN")
GUILD_ID = get_primary_guild_id()  # zentralisiert via Multi-Tenant-Resolver (Phase 0.5)

logger = get_logger("admin_bot")

# ------------------------------------------------------------------
# Token-Pruefung — ohne Token wird der Bot nicht gestartet
# ------------------------------------------------------------------

if not TOKEN:
    logger.info("ADMIN_BOT_TOKEN nicht gesetzt — Admin Bot deaktiviert")
    sys.exit(0)

# ------------------------------------------------------------------
# Bot Setup
# ------------------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True  # Fuer Reaction Roles benoetigt

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None,  # Eigener /help oder keiner
    allowed_mentions=discord.AllowedMentions.none(),
)

# Konfiguration und Datenverzeichnis fuer Cogs bereitstellen
bot.config = get_config()

ADMIN_DATA_DIR = DATA_DIR / "admin"
bot.admin_data_dir = ADMIN_DATA_DIR

# Cogs die beim Start geladen werden
INITIAL_COGS: list[str] = [
    "cogs.moderation_cog",      # Phase 11b: WordFilter + AntiSpam
    "cogs.warn_cog",            # Phase 11c: Warn-System
    "cogs.reaction_roles_cog",  # Phase 11d: Reaction Roles
    "cogs.leveling_cog",        # Phase 11e: Leveling/XP-System
    "cogs.tickets_cog",         # Phase 11f: Ticket-System
    "cogs.audit_cog",           # Phase 11g: Audit-Logging
    "cogs.giveaway_cog",        # Phase 11h: Giveaway-System
    "cogs.temp_voice_cog",      # Phase 12a: Temp Voice Channels
    # "cogs.teamspeak_cog",     # Etappe 3.7 (2026-05-01): TS-Server inactive/not-installed,
    #                            Cog deaktiviert. Reaktivieren falls TS-Server wieder kommt.
    "cogs.server_backup_cog",   # Phase 12e: Server-Backup
    "cogs.embed_sender_cog",    # Embed-Sender (Dashboard-Queue)
    "cogs.custom_commands_cog", # F30: Custom-Commands
    "cogs.profile_cog",        # F39: Spieler-Profil + Leaderboard
    "cogs.linked_accounts_cog",  # Wave 2: Linked-Accounts (Activision/Steam/...)
    "cogs.verification_cog",     # Wave 2 G: Verification-Gate + Raid-Schutz
    "cogs.notify_cog",         # F40: Spieler-Benachrichtigungen
    "cogs.welcome_cog",        # F41: Willkommens-System
    "cogs.command_stats_cog",  # F59: Command-Nutzungsstatistik
    "cogs.pipeline_approval_cog",  # Phase E: n8n-Pipeline Approve/Dismiss-Buttons
]


# ------------------------------------------------------------------
# Bot-Status-Writer (Ping + Uptime fuer Dashboard)
# ------------------------------------------------------------------

_admin_bot_start_time = time.time()


def _write_admin_bot_status():
    """Schreibt Admin Bot Status (Ping, Uptime) als JSON fuer Dashboard."""
    try:
        ADMIN_DATA_DIR.mkdir(parents=True, exist_ok=True)
        uptime_secs = int(time.time() - _admin_bot_start_time)
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
        tmp = ADMIN_DATA_DIR / "bot_status.json.tmp"
        target = ADMIN_DATA_DIR / "bot_status.json"
        with open(tmp, "w") as f:
            json.dump(data, f)
        tmp.replace(target)
    except Exception as e:
        logger.debug(f"Bot-Status schreiben fehlgeschlagen: {e}")


@tasks.loop(seconds=30)
async def admin_status_writer_task():
    """Schreibt Bot-Status alle 30 Sekunden."""
    await asyncio.to_thread(_write_admin_bot_status)


@admin_status_writer_task.before_loop
async def before_admin_status_writer():
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
    logger.info(f"Admin Bot online: {bot.user} (ID: {bot.user.id})")
    logger.info(f"Guilds: {[g.name for g in bot.guilds]}")

    # Bot-Status-Writer starten (Ping fuer Dashboard)
    if not admin_status_writer_task.is_running():
        admin_status_writer_task.start()


@bot.event
async def on_command_error(ctx, error):
    logger.error(f"Command error: {error}", exc_info=True)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    if isinstance(error, discord.app_commands.CheckFailure):
        # Keine Berechtigung — wird bereits von Decorators behandelt
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
    """Alle Cogs laden"""
    for cog in INITIAL_COGS:
        try:
            await bot.load_extension(cog)
            logger.info(f"Cog loaded: {cog}")
        except Exception as e:
            logger.error(f"Failed to load cog {cog}: {e}", exc_info=True)


@bot.event
async def setup_hook():
    # Datenverzeichnis erstellen
    ADMIN_DATA_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(f"Admin data dir: {ADMIN_DATA_DIR}")

    # F28: SQLite-Datenbank initialisieren
    try:
        await init_db()
        logger.info("SQLite-Datenbank initialisiert")
    except Exception as e:
        logger.error(f"Datenbank-Initialisierung fehlgeschlagen: {e}")

    # Cogs laden
    await load_cogs()

    # Slash Commands synchronisieren (einmalig, nicht bei jedem Reconnect)
    if GUILD_ID:
        guild = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        logger.info(f"Synced {len(synced)} commands to guild {GUILD_ID}")
    else:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} commands globally")

    logger.info("Setup hook complete")


def main():
    # F62: Startup Selftest
    selftest_ok = execute_selftest(
        "Admin Bot",
        required_env=["ADMIN_BOT_TOKEN", "GUILD_ID"],
    )
    if not selftest_ok:
        logger.error("Selftest fehlgeschlagen — Bot wird nicht gestartet!")
        sys.exit(1)

    logger.info("Starting Admin Bot...")

    # F28/F61: DB-Close bei Shutdown registrieren
    async def _cleanup_admin_db():
        try:
            await close_db()
        except Exception as e:
            logger.debug(f"DB cleanup error: {e}")

    register_cleanup(_cleanup_admin_db)

    try:
        bot.run(TOKEN)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
