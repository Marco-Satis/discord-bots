"""
Mod Management Cog - Phase 15
Commands for managing game server mods (Satisfactory & Minecraft)

Commands:
  /mod list [game]
  /mod install <game> <mod_id> [version]
  /mod uninstall <game> <mod_name>
  /mod update <game> [mod_name]
  /mod search <game> <query>
  /mod info <mod_name>
  /mod export <game>
  /mod import <game> <modpack_file>
"""

import discord
from discord import app_commands
from discord.ext import commands
from pathlib import Path
from typing import Optional

from utils import get_logger, admin_only, spieler_only, owner_only, truncate, format_timestamp
from modules.mod_manager import ModManager

logger = get_logger("cogs.mod")


class ModCog(commands.Cog):
    """Mod management commands for Satisfactory and Minecraft"""

    mod = app_commands.Group(name="mod", description="Mod-Verwaltung")

    def __init__(self, bot):
        self.bot = bot
        self.mod_managers = {}  # Cache for ModManager instances

    def _get_mod_manager(self, game: str) -> ModManager:
        """Get or create ModManager instance for game"""
        game_lower = game.lower()
        if game_lower not in self.mod_managers:
            # Assuming bot has game paths configured
            if game_lower == "satisfactory":
                server_path = Path(self.bot.sat_server.server_path) if hasattr(self.bot, 'sat_server') else Path("/opt/satisfactory")
            else:  # minecraft
                server_path = Path("/opt/minecraft") if not hasattr(self.bot, 'minecraft_path') else Path(self.bot.minecraft_path)

            self.mod_managers[game_lower] = ModManager(game_lower, server_path)

        return self.mod_managers[game_lower]

    # ==================================================================
    # /mod list - List installed mods
    # ==================================================================

    @mod.command(name="list", description="Installierte Mods auflisten")
    async def mod_list(self, interaction: discord.Interaction, game: Optional[str] = None):
        """List all installed mods for a game"""
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
                    color=0xFFA500
                )
                await interaction.followup.send(embed=embed)
                return

            embed = discord.Embed(
                title=f"{game.capitalize()} - Installierte Mods ({len(mods)})",
                color=0x00FF00
            )

            for i, mod in enumerate(mods[:25], 1):
                mod_text = (
                    f"**Version:** {mod.get('version', 'N/A')}\n"
                    f"**Beschreibung:** {truncate(mod.get('description', 'Keine'), 100)}\n"
                    f"**Installiert:** {mod.get('installed_at', 'Unbekannt')[:10]}"
                )
                embed.add_field(
                    name=f"{i}. {mod.get('name', 'Unbekannt')}",
                    value=mod_text,
                    inline=False
                )

            if len(mods) > 25:
                embed.set_footer(text=f"... und {len(mods) - 25} weitere")

            await interaction.followup.send(embed=embed)
            logger.info(f"Listed {len(mods)} mods for {game}")

        except Exception as e:
            logger.error(f"Error listing mods: {e}")
            await interaction.followup.send(
                f"Fehler beim Auflisten der Mods: {str(e)}",
                ephemeral=True
            )

    # ==================================================================
    # /mod install - Install a mod (admin)
    # ==================================================================

    @mod.command(name="install", description="Mod installieren (Admin)")
    @admin_only()
    async def mod_install(
        self,
        interaction: discord.Interaction,
        game: str,
        mod_id: str,
        version: Optional[str] = None
    ):
        """Install a mod from repository"""
        await interaction.response.defer()

        try:
            game = game.lower()
            if game not in ["satisfactory", "minecraft"]:
                await interaction.followup.send(
                    f"Spiel nicht unterstuetzt: `{game}`",
                    ephemeral=True
                )
                return

            mod_mgr = self._get_mod_manager(game)
            success, message = await mod_mgr.install_mod(mod_id, version or "latest")

            color = 0x00FF00 if success else 0xFF0000
            embed = discord.Embed(
                title="Mod Installation",
                description=message,
                color=color
            )
            embed.set_footer(text=f"Spiel: {game.capitalize()}")

            await interaction.followup.send(embed=embed)

            if success:
                logger.info(f"Mod installed: {mod_id} v{version or 'latest'} for {game}")
            else:
                logger.warning(f"Mod installation failed: {mod_id}")

        except Exception as e:
            logger.error(f"Error installing mod: {e}")
            await interaction.followup.send(
                f"Fehler bei der Installation: {str(e)}",
                ephemeral=True
            )

    # ==================================================================
    # /mod uninstall - Uninstall a mod (admin)
    # ==================================================================

    @mod.command(name="uninstall", description="Mod deinstallieren (Admin)")
    @admin_only()
    async def mod_uninstall(
        self,
        interaction: discord.Interaction,
        game: str,
        mod_name: str
    ):
        """Uninstall a mod"""
        await interaction.response.defer()

        try:
            game = game.lower()
            if game not in ["satisfactory", "minecraft"]:
                await interaction.followup.send(
                    f"Spiel nicht unterstuetzt: `{game}`",
                    ephemeral=True
                )
                return

            mod_mgr = self._get_mod_manager(game)
            success, message = await mod_mgr.uninstall_mod(mod_name)

            color = 0x00FF00 if success else 0xFF0000
            embed = discord.Embed(
                title="Mod Deinstallation",
                description=message,
                color=color
            )
            embed.set_footer(text=f"Spiel: {game.capitalize()}")

            await interaction.followup.send(embed=embed)

            if success:
                logger.info(f"Mod uninstalled: {mod_name} from {game}")
            else:
                logger.warning(f"Mod uninstall failed: {mod_name}")

        except Exception as e:
            logger.error(f"Error uninstalling mod: {e}")
            await interaction.followup.send(
                f"Fehler bei der Deinstallation: {str(e)}",
                ephemeral=True
            )

    # ==================================================================
    # /mod update - Update mods (admin)
    # ==================================================================

    @mod.command(name="update", description="Mod(s) aktualisieren (Admin)")
    @admin_only()
    async def mod_update(
        self,
        interaction: discord.Interaction,
        game: str,
        mod_name: Optional[str] = None
    ):
        """Update a specific mod or check all for updates"""
        await interaction.response.defer()

        try:
            game = game.lower()
            if game not in ["satisfactory", "minecraft"]:
                await interaction.followup.send(
                    f"Spiel nicht unterstuetzt: `{game}`",
                    ephemeral=True
                )
                return

            mod_mgr = self._get_mod_manager(game)

            if mod_name:
                # Update specific mod
                success, message = await mod_mgr.update_mod(mod_name)
                color = 0x00FF00 if success else 0xFF0000
                embed = discord.Embed(
                    title="Mod Update",
                    description=message,
                    color=color
                )
            else:
                # Check all mods for updates
                updates = await mod_mgr.check_updates()

                if not updates:
                    embed = discord.Embed(
                        title="Mod Updates",
                        description="Alle Mods sind aktuell!",
                        color=0x00FF00
                    )
                else:
                    embed = discord.Embed(
                        title="Mod Updates verfuegbar",
                        description=f"{len(updates)} Mods koennen aktualisiert werden",
                        color=0xFFA500
                    )
                    for update in updates[:10]:
                        embed.add_field(
                            name=update.get("name"),
                            value=f"{update.get('current')} → {update.get('latest')}",
                            inline=True
                        )
                    if len(updates) > 10:
                        embed.set_footer(text=f"... und {len(updates) - 10} weitere")

            embed.set_footer(text=f"Spiel: {game.capitalize()}")
            await interaction.followup.send(embed=embed)
            logger.info(f"Checked updates for {game} mods")

        except Exception as e:
            logger.error(f"Error updating mods: {e}")
            await interaction.followup.send(
                f"Fehler beim Update: {str(e)}",
                ephemeral=True
            )

    # ==================================================================
    # /mod search - Search for mods (spieler)
    # ==================================================================

    @mod.command(name="search", description="Mods durchsuchen")
    @spieler_only()
    async def mod_search(
        self,
        interaction: discord.Interaction,
        game: str,
        query: str
    ):
        """Search for mods in repository"""
        await interaction.response.defer()

        try:
            game = game.lower()
            if game not in ["satisfactory", "minecraft"]:
                await interaction.followup.send(
                    f"Spiel nicht unterstuetzt: `{game}`",
                    ephemeral=True
                )
                return

            mod_mgr = self._get_mod_manager(game)
            results = await mod_mgr.search_mods(query, game)

            if not results:
                embed = discord.Embed(
                    title="Mod-Suche",
                    description=f"Keine Ergebnisse fuer `{query}`",
                    color=0xFFA500
                )
                await interaction.followup.send(embed=embed)
                return

            embed = discord.Embed(
                title=f"Mod-Suche: {query}",
                description=f"Gefunden: {len(results)} Ergebnisse",
                color=0x00FF00
            )

            for i, result in enumerate(results[:10], 1):
                result_text = (
                    f"**ID:** `{result.get('mod_id')}`\n"
                    f"**Version:** {result.get('latest_version')}\n"
                    f"**Downloads:** {result.get('downloads', 0):,}\n"
                    f"**Beschreibung:** {result.get('description', 'Keine')}"
                )
                embed.add_field(
                    name=f"{i}. {result.get('name')}",
                    value=result_text,
                    inline=False
                )

            embed.set_footer(text=f"Nutze /mod install {game} <mod_id> zum Installieren")
            await interaction.followup.send(embed=embed)
            logger.info(f"Searched for mods: {query} on {game}")

        except Exception as e:
            logger.error(f"Error searching mods: {e}")
            await interaction.followup.send(
                f"Fehler bei der Suche: {str(e)}",
                ephemeral=True
            )

    # ==================================================================
    # /mod info - Show mod details (spieler)
    # ==================================================================

    @mod.command(name="info", description="Mod-Details anzeigen")
    @spieler_only()
    async def mod_info(
        self,
        interaction: discord.Interaction,
        mod_name: str
    ):
        """Show detailed info about an installed mod"""
        await interaction.response.defer()

        try:
            # Search in all games
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
                color=0x00FF00
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
            logger.error(f"Error getting mod info: {e}")
            await interaction.followup.send(
                f"Fehler beim Abrufen der Info: {str(e)}",
                ephemeral=True
            )

    # ==================================================================
    # /mod export - Export modpack (admin)
    # ==================================================================

    @mod.command(name="export", description="Modpack exportieren (Admin)")
    @admin_only()
    async def mod_export(
        self,
        interaction: discord.Interaction,
        game: str,
        name: Optional[str] = None
    ):
        """Export current mod list as modpack"""
        await interaction.response.defer()

        try:
            game = game.lower()
            if game not in ["satisfactory", "minecraft"]:
                await interaction.followup.send(
                    f"Spiel nicht unterstuetzt: `{game}`",
                    ephemeral=True
                )
                return

            modpack_name = name or f"{game}_modpack"
            mod_mgr = self._get_mod_manager(game)
            success, message, file_path = await mod_mgr.create_modpack(modpack_name)

            if success and file_path:
                embed = discord.Embed(
                    title="Modpack Export",
                    description=message,
                    color=0x00FF00
                )
                embed.add_field(name="Datei", value=f"`{file_path.name}`", inline=False)

                try:
                    await interaction.followup.send(
                        embed=embed,
                        file=discord.File(str(file_path))
                    )
                except Exception:
                    await interaction.followup.send(embed=embed)
            else:
                embed = discord.Embed(
                    title="Modpack Export",
                    description=message,
                    color=0xFF0000
                )
                await interaction.followup.send(embed=embed)

            if success:
                logger.info(f"Exported modpack: {modpack_name} for {game}")

        except Exception as e:
            logger.error(f"Error exporting modpack: {e}")
            await interaction.followup.send(
                f"Fehler beim Export: {str(e)}",
                ephemeral=True
            )

    # ==================================================================
    # /mod import - Import modpack (admin)
    # ==================================================================

    @mod.command(name="import", description="Modpack importieren (Admin)")
    @admin_only()
    async def mod_import(
        self,
        interaction: discord.Interaction,
        game: str,
        modpack_file: str
    ):
        """Import mods from modpack JSON"""
        await interaction.response.defer()

        try:
            game = game.lower()
            if game not in ["satisfactory", "minecraft"]:
                await interaction.followup.send(
                    f"Spiel nicht unterstuetzt: `{game}`",
                    ephemeral=True
                )
                return

            modpack_path = Path(modpack_file)
            if not modpack_path.exists():
                # Try in data directory
                modpack_path = Path(__file__).parent.parent / "data" / modpack_file
                if not modpack_path.exists():
                    await interaction.followup.send(
                        f"Datei nicht gefunden: `{modpack_file}`",
                        ephemeral=True
                    )
                    return

            mod_mgr = self._get_mod_manager(game)
            success, message = await mod_mgr.import_modpack(modpack_path)

            color = 0x00FF00 if success else 0xFF0000
            embed = discord.Embed(
                title="Modpack Import",
                description=message,
                color=color
            )
            embed.set_footer(text=f"Spiel: {game.capitalize()}")

            await interaction.followup.send(embed=embed)

            if success:
                logger.info(f"Imported modpack: {modpack_file} for {game}")

        except Exception as e:
            logger.error(f"Error importing modpack: {e}")
            await interaction.followup.send(
                f"Fehler beim Import: {str(e)}",
                ephemeral=True
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
    """Load cog"""
    await bot.add_cog(ModCog(bot))
