"""
Admin Bot — Discord-Moderation, Community-Features & TeamSpeak-Steuerung
Bot 3 of 3 — unabhaengig von Gameservern

Architektur:
  - Cog-basiertes Design mit Hot-Reloading
  - Features: Moderation, Reaction Roles, Leveling, Tickets, Audit, Giveaways
  - Berechtigungen: Owner > Admin > Spieler > Alle
"""

import sys
import discord
from discord.ext import commands
from pathlib import Path

# Projektroot zum Pfad hinzufuegen
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.config import load_env, get_env, get_config, DATA_DIR
from utils.logger import get_logger

# Umgebung laden
load_env()

# Konfiguration
TOKEN = get_env("ADMIN_BOT_TOKEN")
GUILD_ID = get_env("GUILD_ID", cast=int)

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
]


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
    logger.info("Starting Admin Bot...")
    try:
        bot.run(TOKEN)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
