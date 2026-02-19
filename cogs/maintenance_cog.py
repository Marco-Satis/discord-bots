"""
Maintenance Cog - Phase 16
Bot maintenance commands (network diagnostics, token management, version info)

Commands:
  /maint version
  /maint network
  /maint ports
  /maint tokens
  /maint restart-bot
"""

import asyncio

import discord
from discord import app_commands
from discord.ext import commands
from utils import get_logger, owner_only, admin_only
from modules.maintenance import BotMaintenance

logger = get_logger("cogs.maintenance")


class MaintenanceCog(commands.Cog):
    """Bot maintenance commands"""

    maint = app_commands.Group(name="maint", description="Bot-Wartung")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.maintenance = BotMaintenance()

    # ==================================================================
    # /maint version - Show bot version (everyone)
    # ==================================================================

    @maint.command(name="version", description="Bot-Version anzeigen")
    async def maint_version(self, interaction: discord.Interaction):
        """Show current bot version"""
        await interaction.response.defer()

        try:
            version = self.maintenance.get_current_version()

            embed = discord.Embed(
                title="Bot-Version",
                color=0x5865F2
            )

            embed.add_field(
                name="Aktuelle Version",
                value=f"`v{version}`",
                inline=True
            )

            embed.add_field(
                name="Python Version",
                value=f"`3.10+`",
                inline=True
            )

            embed.add_field(
                name="Latenz",
                value=f"`{round(self.bot.latency * 1000)}ms`",
                inline=True
            )

            await interaction.followup.send(embed=embed)
            logger.info(f"Showed version to {interaction.user}")

        except Exception as e:
            logger.error(f"Error showing version: {e}")
            await interaction.followup.send(
                f"Fehler beim Anzeigen der Version: {str(e)}",
                ephemeral=True
            )

    # ==================================================================
    # /maint network - Network diagnostics (admin)
    # ==================================================================

    @maint.command(name="network", description="Netzwerk-Diagnostik (Admin)")
    @admin_only()
    async def maint_network(self, interaction: discord.Interaction):
        """Check network connectivity"""
        await interaction.response.defer()

        try:
            embed = discord.Embed(
                title="Netzwerk-Diagnostik",
                description="Pruefte Verbindungen...",
                color=0x5865F2
            )

            network_info = await self.maintenance.check_network()

            # Internet
            internet_status = "✓ Online" if network_info.get("internet") else "✗ Offline"
            internet_latency = network_info.get("internet")
            if internet_latency:
                internet_status += f" ({internet_latency}ms)"

            # DNS
            dns_status = "✓ OK" if network_info.get("dns") else "✗ Fehler"

            # Discord API
            discord_latency = network_info.get("discord_api")
            discord_status = f"✓ OK ({discord_latency}ms)" if discord_latency else "✗ Nicht erreichbar"

            # Steam API
            steam_latency = network_info.get("steam_api")
            steam_status = f"✓ OK ({steam_latency}ms)" if steam_latency else "✗ Nicht erreichbar"

            embed.add_field(
                name="Internet",
                value=internet_status,
                inline=True
            )

            embed.add_field(
                name="DNS",
                value=dns_status,
                inline=True
            )

            embed.add_field(
                name="Discord API",
                value=discord_status,
                inline=True
            )

            embed.add_field(
                name="Steam API",
                value=steam_status,
                inline=True
            )

            embed.set_footer(text=f"Geprüft: {(network_info.get('timestamp') or '')[:19]}")

            await interaction.followup.send(embed=embed)
            logger.info(f"Network check performed by {interaction.user}")

        except Exception as e:
            logger.error(f"Error checking network: {e}")
            await interaction.followup.send(
                f"Fehler bei der Netzwerk-Pruefung: {str(e)}",
                ephemeral=True
            )

    # ==================================================================
    # /maint ports - Port check (admin)
    # ==================================================================

    @maint.command(name="ports", description="Port-Verfuegbarkeit pruefen (Admin)")
    @admin_only()
    async def maint_ports(self, interaction: discord.Interaction):
        """Check if game server ports are accessible"""
        await interaction.response.defer()

        try:
            embed = discord.Embed(
                title="Port-Verfuegbarkeit",
                description="Pruefte Ports...",
                color=0x5865F2
            )

            port_info = await self.maintenance.check_ports()

            # Satisfactory (15777)
            sat_port = port_info.get("satisfactory", {})
            sat_status = "✓ Offen" if sat_port.get("open") else f"✗ Geschlossen ({sat_port.get('reason', 'unknown')})"

            # Minecraft (25565)
            mc_port = port_info.get("minecraft", {})
            mc_status = "✓ Offen" if mc_port.get("open") else f"✗ Geschlossen ({mc_port.get('reason', 'unknown')})"

            embed.add_field(
                name="Satisfactory (15777)",
                value=sat_status,
                inline=True
            )

            embed.add_field(
                name="Minecraft (25565)",
                value=mc_status,
                inline=True
            )

            embed.add_field(
                name="Hinweis",
                value="Ports sind nur von außen erreichbar, wenn der Server lauft.",
                inline=False
            )

            embed.set_footer(text=f"Geprüft: {(port_info.get('timestamp') or '')[:19]}")

            await interaction.followup.send(embed=embed)
            logger.info(f"Port check performed by {interaction.user}")

        except Exception as e:
            logger.error(f"Error checking ports: {e}")
            await interaction.followup.send(
                f"Fehler bei der Port-Pruefung: {str(e)}",
                ephemeral=True
            )

    # ==================================================================
    # /maint tokens - Token rotation status (owner)
    # ==================================================================

    @maint.command(name="tokens", description="Token-Rotations-Status (Owner)")
    @owner_only()
    async def maint_tokens(self, interaction: discord.Interaction):
        """Check token rotation status"""
        await interaction.response.defer()

        try:
            token_info = self.maintenance.check_token_age()

            if not token_info.get("tokens_need_rotation"):
                embed = discord.Embed(
                    title="Token-Rotation",
                    description="✓ Alle Tokens sind aktuell",
                    color=0x00FF00
                )
            else:
                embed = discord.Embed(
                    title="Token-Rotation",
                    description=f"⚠ {len(token_info.get('tokens_need_rotation'))} Tokens brauchen Rotation",
                    color=0xFF0000
                )

                for token in token_info.get("tokens_need_rotation", []):
                    embed.add_field(
                        name=token.get("name"),
                        value=(
                            f"Rotiert: {token.get('rotated_at')[:10]}\n"
                            f"Alter: {token.get('days_old')} Tage"
                        ),
                        inline=False
                    )

            embed.add_field(
                name="Hinweis",
                value="Tokens sollten alle 90 Tage rotiert werden zur Sicherheit.",
                inline=False
            )

            embed.set_footer(text=f"Geprüft: {(token_info.get('timestamp') or '')[:19]}")

            await interaction.followup.send(embed=embed)
            logger.info(f"Token status checked by {interaction.user}")

        except Exception as e:
            logger.error(f"Error checking token status: {e}")
            await interaction.followup.send(
                f"Fehler beim Ueberprufen der Token: {str(e)}",
                ephemeral=True
            )

    # ==================================================================
    # /maint restart-bot - Restart bot processes (owner)
    # ==================================================================

    @maint.command(name="restart-bot", description="Bot neu starten (Owner)")
    @owner_only()
    async def maint_restart_bot(self, interaction: discord.Interaction):
        """Restart bot processes"""
        await interaction.response.defer()

        try:
            embed = discord.Embed(
                title="Bot-Neustart",
                description="Starte Bot neu...",
                color=0xFFD700
            )

            await interaction.followup.send(embed=embed)
            logger.info(f"Bot restart initiated by {interaction.user}")

            # Give time to send the message
            await asyncio.sleep(1)

            # Close the bot (it should be restarted by systemd)
            await self.bot.close()

        except Exception as e:
            logger.error(f"Error restarting bot: {e}")
            await interaction.followup.send(
                f"Fehler beim Neustart: {str(e)}",
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
    await bot.add_cog(MaintenanceCog(bot))
