"""
Mod Management Cog - Phase 14: Nur noch Lese-Commands

Phase 14 (F25): Admin-Commands (install, uninstall, update, search,
export, import) ins Dashboard migriert. Nur Lese-Commands bleiben:
  /mod list [game]     — Installierte Mods anzeigen (Spieler)
  /mod info <mod_name> — Mod-Details anzeigen (Spieler)
"""

import discord
from discord import app_commands
from discord.ext import commands
from pathlib import Path
from typing import Optional

from utils import get_logger, spieler_only, truncate
from modules.mod_manager import ModManager
from utils.embeds import COLOR_SUCCESS, COLOR_WARNING

logger = get_logger("cogs.mod")


class ModCog(commands.Cog):
    """Mod-Informationen anzeigen (Verwaltung über Dashboard)"""

    mod = app_commands.Group(name="mod", description="Mod-Informationen")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.mod_managers = {}  # Cache für ModManager-Instanzen

    def _get_mod_manager(self, game: str) -> ModManager:
        """ModManager-Instanz für ein Spiel holen oder erstellen"""
        game_lower = game.lower()
        if game_lower not in self.mod_managers:
            if game_lower == "satisfactory":
                server_path = Path(self.bot.sat_server.server_path) if hasattr(self.bot, 'sat_server') else Path("/opt/satisfactory")
            else:  # minecraft
                server_path = Path("/opt/minecraft") if not hasattr(self.bot, 'minecraft_path') else Path(self.bot.minecraft_path)

            self.mod_managers[game_lower] = ModManager(game_lower, server_path)

        return self.mod_managers[game_lower]

    # ==================================================================
    # /mod list - Installierte Mods anzeigen (Spieler)
    # ==================================================================

    @mod.command(name="list", description="Installierte Mods auflisten")
    async def mod_list(self, interaction: discord.Interaction, game: Optional[str] = None):
        """Alle installierten Mods für ein Spiel anzeigen"""
        await interaction.response.defer()

        try:
            if game is None:
                game = "satisfactory"

            game = game.lower()
            if game not in ["satisfactory", "minecraft"]:
                await interaction.followup.send(
                    f"Spiel nicht unterstuetzt: `{game}` (satisfactory oder minecraft)",
                    ephemeral=True
                )
                return

            mod_mgr = self._get_mod_manager(game)
            mods = mod_mgr.list_installed()

            if not mods:
                embed = discord.Embed(
                    title=f"{game.capitalize()} - Mods",
                    description="Keine Mods installiert",
                    color=COLOR_WARNING
                )
                await interaction.followup.send(embed=embed)
                return

            embed = discord.Embed(
                title=f"{game.capitalize()} - Installierte Mods ({len(mods)})",
                color=COLOR_SUCCESS
            )

            for i, mod_entry in enumerate(mods[:25], 1):
                mod_text = (
                    f"**Version:** {mod_entry.get('version', 'N/A')}\n"
                    f"**Beschreibung:** {truncate(mod_entry.get('description', 'Keine'), 100)}\n"
                    f"**Installiert:** {mod_entry.get('installed_at', 'Unbekannt')[:10]}"
                )
                embed.add_field(
                    name=f"{i}. {mod_entry.get('name', 'Unbekannt')}",
                    value=mod_text,
                    inline=False
                )

            if len(mods) > 25:
                embed.set_footer(text=f"... und {len(mods) - 25} weitere")

            await interaction.followup.send(embed=embed)
            logger.info(f"Listed {len(mods)} mods for {game}")

        except Exception as e:
            logger.error(f"Fehler beim Auflisten der Mods: {e}")
            await interaction.followup.send(
                "Fehler beim Auflisten der Mods.",
                ephemeral=True
            )

    # ==================================================================
    # /mod info - Mod-Details anzeigen (Spieler)
    # ==================================================================

    @mod.command(name="info", description="Mod-Details anzeigen")
    @spieler_only()
    async def mod_info(
        self,
        interaction: discord.Interaction,
        mod_name: str
    ):
        """Detaillierte Informationen zu einem installierten Mod anzeigen"""
        await interaction.response.defer()

        try:
            # In allen Spielen suchen
            mod_entry = None
            game_found = None

            for game in ["satisfactory", "minecraft"]:
                mod_mgr = self._get_mod_manager(game)
                mod_entry = mod_mgr.get_mod_info(mod_name)
                if mod_entry:
                    game_found = game
                    break

            if not mod_entry:
                await interaction.followup.send(
                    f"Mod nicht gefunden: `{mod_name}`",
                    ephemeral=True
                )
                return

            embed = discord.Embed(
                title=f"Mod: {mod_entry.get('name')}",
                description=mod_entry.get("description", "Keine Beschreibung"),
                color=COLOR_SUCCESS
            )

            embed.add_field(
                name="Allgemein",
                value=(
                    f"**ID:** `{mod_entry.get('mod_id')}`\n"
                    f"**Spiel:** {mod_entry.get('game', 'N/A').capitalize()}\n"
                    f"**Version:** {mod_entry.get('version', 'N/A')}"
                ),
                inline=False
            )

            embed.add_field(
                name="Installation",
                value=(
                    f"**Datum:** {mod_entry.get('installed_at', 'Unbekannt')[:10]}\n"
                    f"**Pfad:** `{truncate(mod_entry.get('file_path', 'N/A'), 60)}`"
                ),
                inline=False
            )

            await interaction.followup.send(embed=embed)
            logger.info(f"Showed info for mod: {mod_name}")

        except Exception as e:
            logger.error(f"Fehler beim Abrufen der Mod-Info: {e}")
            await interaction.followup.send(
                "Fehler beim Abrufen der Mod-Informationen.",
                ephemeral=True
            )

    async def cog_app_command_error(self, interaction: discord.Interaction,
                                     error: app_commands.AppCommandError) -> None:
        """Fehlerbehandlung für alle Commands in diesem Cog."""
        if isinstance(error, app_commands.CheckFailure):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Keine Berechtigung für diesen Befehl.", ephemeral=True
                )
            return
        logger.error(f"Command-Fehler in {interaction.command.name if interaction.command else 'unknown'}: {error}", exc_info=True)
        try:
            msg = "Ein Fehler ist aufgetreten."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception as e:
            logger.debug(f"Exception swallowed (B110-refactor 3.1): {e}")


async def setup(bot):
    """Load cog"""
    await bot.add_cog(ModCog(bot))
