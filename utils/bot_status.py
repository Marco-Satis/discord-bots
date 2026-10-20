"""
Status-Zeile der Bots (Custom Status im Profil): "📡 12 Module · ⚡ 41 Commands".

Marco 2026-10-20: keine Live-Serverwerte, sondern Angaben zum Bot selbst.
Gezaehlt wird beim Ready, also nach dem Laden aller Cogs.
"""
import discord
from discord import app_commands
from discord.ext import commands

from utils.logger import get_logger

logger = get_logger(__name__)


def count_commands(bot: commands.Bot) -> int:
    """Aufrufbare Slash-Befehle inkl. Untergruppen, global und je Guild, ohne Doppelte."""
    namen = {c.qualified_name for c in bot.tree.walk_commands()
             if isinstance(c, app_commands.Command)}
    for guild in bot.guilds:
        namen |= {c.qualified_name for c in bot.tree.walk_commands(guild=guild)
                  if isinstance(c, app_commands.Command)}
    return len(namen)


def status_text(bot: commands.Bot, symbol: str) -> str:
    """Text der Status-Zeile; Bots ohne Slash-Befehle werden per Button gesteuert."""
    module = len(bot.cogs)
    befehle = count_commands(bot)
    if befehle:
        return f"{symbol} {module} Module · ⚡ {befehle} Commands"
    return f"{symbol} {module} Module · Steuerung per Button"


async def set_bot_status(bot: commands.Bot, symbol: str) -> None:
    """Custom Status setzen. Fehler nur loggen — der Bot laeuft auch ohne Status."""
    text = status_text(bot, symbol)
    try:
        await bot.change_presence(activity=discord.CustomActivity(name=text))
        logger.info(f"Status gesetzt: {text}")
    except Exception as e:
        logger.warning(f"Status setzen fehlgeschlagen: {e}")
