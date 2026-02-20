"""
Maintenance Cog - Phase 14: Alle Commands entfernt (→ Dashboard)

Alle Wartungs-Commands (/maint version, network, ports, tokens, restart-bot)
wurden ins Web-Dashboard migriert (Phase 13i: System-Verwaltungsseite).
Dieser Cog bleibt als leere Huelle erhalten, falls spaeter neue
Maintenance-Commands benoetigt werden.
"""

from discord.ext import commands
from utils import get_logger

logger = get_logger("cogs.maintenance")


class MaintenanceCog(commands.Cog):
    """Maintenance-Commands — alle ins Dashboard migriert (Phase 14)"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        logger.info("MaintenanceCog geladen (alle Commands ins Dashboard migriert)")


async def setup(bot):
    """Load cog"""
    await bot.add_cog(MaintenanceCog(bot))
