"""
Timeout Cog
/timeout [spieler] [dauer_min] [grund]
Kicks player from game server AND sets Discord timeout simultaneously
"""

import re as _re

import discord
from discord import app_commands
from discord.ext import commands
from datetime import timedelta
from typing import Optional
from utils import get_logger, admin_only


def _sanitize_input(text: str, max_length: int = 100) -> str:
    """Sanitisiert User-Input fuer Server-Befehle. Erlaubt nur sichere Zeichen."""
    sanitized = _re.sub(r'[^\w\s\-]', '', text, flags=_re.UNICODE)
    return sanitized[:max_length].strip()

logger = get_logger("cogs.timeout")


class TimeoutCog(commands.Cog):
    """Cross-platform timeout: Game kick + Discord timeout"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.server = bot.sat_server
        self.api = bot.sat_api

    @app_commands.command(
        name="timeout",
        description="Spieler vom Server kicken + Discord-Timeout setzen"
    )
    @app_commands.describe(
        spieler="Name des Spielers (Game + Discord)",
        dauer_min="Timeout-Dauer in Minuten",
        grund="Grund fuer den Timeout"
    )
    @admin_only()
    async def timeout(self, interaction: discord.Interaction,
                      spieler: str, dauer_min: int,
                      grund: str = "Kein Grund angegeben"):
        await interaction.response.defer()

        if dauer_min < 1:
            await interaction.followup.send("Dauer muss mindestens 1 Minute sein.", ephemeral=True)
            return
        if dauer_min > 40320:  # Discord max: 28 days
            await interaction.followup.send("Max. 28 Tage (40320 Minuten).", ephemeral=True)
            return

        results = {
            "game_kick": False,
            "discord_timeout": False,
            "game_error": None,
            "discord_error": None
        }

        # 1. Kick from game server
        if await self.server.is_running():
            try:
                safe_spieler = _sanitize_input(spieler)
                result = await self.api.run_command(f"KickPlayer {safe_spieler}")
                if "Error" not in str(result):
                    results["game_kick"] = True
                else:
                    results["game_error"] = result
            except Exception as e:
                results["game_error"] = str(e)
        else:
            results["game_error"] = "Server offline"

        # 2. Set Discord timeout
        # Try to find the Discord member by display name or username
        discord_member = None
        if interaction.guild:
            # Search by display name (case-insensitive)
            spieler_lower = spieler.lower()
            for member in interaction.guild.members:
                if (member.display_name.lower() == spieler_lower or
                        member.name.lower() == spieler_lower):
                    discord_member = member
                    break

        if discord_member:
            try:
                duration = timedelta(minutes=dauer_min)
                await discord_member.timeout(
                    duration,
                    reason=f"Timeout von {interaction.user.display_name}: {grund}"
                )
                results["discord_timeout"] = True
            except discord.Forbidden:
                results["discord_error"] = "Keine Berechtigung (Bot-Rolle zu niedrig?)"
            except discord.HTTPException as e:
                results["discord_error"] = str(e)
        else:
            results["discord_error"] = f"Discord-User '{spieler}' nicht gefunden"

        # Build response embed
        embed = discord.Embed(
            title="Timeout gesetzt",
            color=0xe67e22
        )
        embed.add_field(name="Spieler", value=spieler, inline=True)

        if dauer_min >= 60:
            display_duration = f"{dauer_min // 60}h {dauer_min % 60}m"
        else:
            display_duration = f"{dauer_min} Min"
        embed.add_field(name="Dauer", value=display_duration, inline=True)
        embed.add_field(name="Grund", value=grund, inline=False)

        # Game kick status
        if results["game_kick"]:
            embed.add_field(name="Game-Kick", value="✅ Gekickt", inline=True)
        else:
            embed.add_field(
                name="Game-Kick",
                value=f"❌ {results['game_error'] or 'Fehlgeschlagen'}",
                inline=True
            )

        # Discord timeout status
        if results["discord_timeout"]:
            embed.add_field(
                name="Discord-Timeout",
                value=f"✅ {discord_member.display_name}",
                inline=True
            )
        else:
            embed.add_field(
                name="Discord-Timeout",
                value=f"❌ {results['discord_error'] or 'Fehlgeschlagen'}",
                inline=True
            )

        embed.set_footer(text=f"von {interaction.user.display_name}")

        # Overall color based on results
        if results["game_kick"] and results["discord_timeout"]:
            embed.color = 0x2ecc71  # Green - both succeeded
        elif results["game_kick"] or results["discord_timeout"]:
            embed.color = 0xe67e22  # Orange - partial success
        else:
            embed.color = 0xe74c3c  # Red - both failed

        await interaction.followup.send(embed=embed)

        logger.info(
            f"Timeout: {spieler} ({dauer_min}min) by {interaction.user} "
            f"- Game: {results['game_kick']}, Discord: {results['discord_timeout']} "
            f"- Reason: {grund}"
        )

    async def cog_app_command_error(self, interaction: discord.Interaction,
                                     error: app_commands.AppCommandError) -> None:
        """Handle errors for all commands in this cog."""
        if isinstance(error, app_commands.CheckFailure):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Keine Berechtigung fuer diesen Befehl.", ephemeral=True
                )
            return
        logger.error(f"Command error in {interaction.command.name if interaction.command else 'unknown'}: {error}", exc_info=True)
        try:
            msg = f"Ein Fehler ist aufgetreten: {error}"
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass


async def setup(bot):
    await bot.add_cog(TimeoutCog(bot))
