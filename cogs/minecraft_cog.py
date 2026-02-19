"""
Minecraft Unified Cog - All /mc commands with sub-groups

Command Structure:
  /mc status                              (Server status - Alle)
  /mc start|stop|restart|cancel           (Server control - Admin)
  /mc players list|kick|ban              (Player management - Spieler)
  /mc backup create|list|restore          (Backup management - Spieler)
  /mc command <cmd>                       (Execute RCON - Owner)
  /mc whitelist add|remove|list           (Whitelist management - Admin)
  /mc say <message>                       (Broadcast message - Admin)
  /mc difficulty <level>                  (Set difficulty - Admin)
  /mc weather <type>                      (Set weather - Admin)
  /mc time <value>                        (Set time - Admin)
  /mc gamemode <mode> <player>            (Set gamemode - Admin)
"""

import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional
from pathlib import Path

from utils import get_logger, format_uptime, format_bytes, status_emoji
from utils.permissions import admin_only, spieler_only, owner_only, is_admin, is_owner
from modules.restart_timer import TimerResult
from modules.minecraft.server import MinecraftServer
from modules.minecraft.backup import MinecraftBackupManager

logger = get_logger("cogs.minecraft")


class MinecraftCog(commands.Cog):
    """All Minecraft server commands unified under /mc"""

    # ==================================================================
    # Group & Sub-Group Definitions
    # ==================================================================

    mc = app_commands.Group(
        name="mc", description="Minecraft Server Befehle"
    )
    players_grp = app_commands.Group(
        name="players", parent=mc, description="Spieler-Verwaltung"
    )
    backup_grp = app_commands.Group(
        name="backup", parent=mc, description="Backup & Welt"
    )
    whitelist_grp = app_commands.Group(
        name="whitelist", parent=mc, description="Whitelist-Verwaltung"
    )

    # ==================================================================
    # Init
    # ==================================================================

    def __init__(self, bot):
        self.bot = bot
        self.server = MinecraftServer()
        self.backup_mgr = MinecraftBackupManager()
        self.timer_mgr = bot.timer_mgr
        self._stop_timer: Optional[asyncio.Task] = None

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  CORE: /mc status | start | stop | restart | cancel           ║
    # ╚════════════════════════════════════════════════════════════════╝

    @mc.command(name="status", description="Server-Status anzeigen")
    async def mc_status(self, interaction: discord.Interaction):
        """Show server status"""
        await interaction.response.defer()

        status = await self.server.get_status()
        online = status["running"]

        embed = discord.Embed(
            title=f"{status_emoji(online)} Minecraft Server",
            color=0x00ff00 if online else 0xff0000,
        )

        if online:
            embed.add_field(
                name="Spieler",
                value=f"{status['players_online']}/{status['players_max']}",
                inline=True,
            )
            embed.add_field(
                name="Uptime",
                value=format_uptime(status["uptime"]),
                inline=True,
            )
            embed.add_field(
                name="CPU",
                value=f"{status['cpu_percent']:.1f}%",
                inline=True,
            )
            embed.add_field(
                name="RAM",
                value=f"{status['memory_mb']} MB",
                inline=True,
            )
            embed.add_field(
                name="Typ",
                value=status["type"].capitalize(),
                inline=True,
            )
        else:
            embed.description = "Server ist offline."

        active_timer = self.timer_mgr.get_active()
        if active_timer:
            embed.add_field(
                name="\u23f0 Geplant",
                value=f"{active_timer.action_name} laeuft...",
                inline=False,
            )

        await interaction.followup.send(embed=embed)

    @mc.command(name="start", description="Server starten")
    @admin_only()
    async def mc_start(self, interaction: discord.Interaction):
        """Start the server"""
        await interaction.response.defer()

        if await self.server.is_running():
            await interaction.followup.send("Server laeuft bereits!")
            return

        await interaction.followup.send("Server wird gestartet...")
        success, msg = await self.server.start()

        if success:
            embed = discord.Embed(
                title=f"{status_emoji(True)} Server gestartet",
                description=msg,
                color=0x00ff00,
            )
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.edit_original_response(content=None, embed=embed)
            logger.info(f"Server started by {interaction.user}")
        else:
            embed = discord.Embed(
                title="Start fehlgeschlagen",
                description=msg,
                color=0xff0000,
            )
            await interaction.edit_original_response(content=None, embed=embed)

    @mc.command(name="stop", description="Server mit Countdown stoppen")
    @admin_only()
    async def mc_stop(self, interaction: discord.Interaction,
                     countdown: int = 10):
        """Stop server with countdown warning"""
        await interaction.response.defer()

        if not await self.server.is_running():
            await interaction.followup.send("Server ist nicht gestartet.")
            return

        if self.timer_mgr.get_active():
            await interaction.followup.send("Ein Timer laeuft bereits. Nutze /mc cancel um diesen zu beenden.")
            return

        await interaction.followup.send(
            f"Server wird in {countdown} Minute(n) gestoppt!"
        )

        # Create a new timer for this shutdown
        from modules.restart_timer import RestartTimer

        async def perform_stop():
            success, msg = await self.server.stop()
            if success:
                embed = discord.Embed(
                    title=f"{status_emoji(False)} Server gestoppt",
                    description=msg,
                    color=0xff0000,
                )
            else:
                embed = discord.Embed(
                    title="Stop fehlgeschlagen",
                    description=msg,
                    color=0xff9900,
                )

            try:
                await interaction.followup.send(embed=embed)
            except:
                pass

        timer = RestartTimer()
        result = await timer.countdown(
            duration_minutes=countdown,
            action_name="Server Stop",
            warnings=[10, 5, 3, 1],
            on_complete=perform_stop
        )

        if result == TimerResult.COMPLETED:
            await perform_stop()
        elif result == TimerResult.CANCELLED:
            try:
                await interaction.followup.send("Server-Stop abgebrochen!")
            except:
                pass

    @mc.command(name="restart", description="Server mit Countdown neustarten")
    @admin_only()
    async def mc_restart(self, interaction: discord.Interaction,
                        countdown: int = 10):
        """Restart server with countdown warning"""
        await interaction.response.defer()

        if not await self.server.is_running():
            await interaction.followup.send("Server ist nicht gestartet.")
            return

        if self.timer_mgr.get_active():
            await interaction.followup.send("Ein Timer laeuft bereits. Nutze /mc cancel um diesen zu beenden.")
            return

        await interaction.followup.send(
            f"Server wird in {countdown} Minute(n) neugestartet!"
        )

        # Send in-game warning
        try:
            await self.server.rcon_command(
                f"say Der Server wird in {countdown} Minuten neugestartet!"
            )
        except Exception as e:
            logger.debug(f"Could not send in-game restart warning: {e}")

        from modules.restart_timer import RestartTimer

        async def perform_restart():
            success, msg = await self.server.restart()
            if success:
                embed = discord.Embed(
                    title=f"{status_emoji(True)} Server neugestartet",
                    description=msg,
                    color=0x00ff00,
                )
            else:
                embed = discord.Embed(
                    title="Restart fehlgeschlagen",
                    description=msg,
                    color=0xff9900,
                )

            try:
                await interaction.followup.send(embed=embed)
            except:
                pass

        timer = RestartTimer()
        result = await timer.countdown(
            duration_minutes=countdown,
            action_name="Server Restart",
            warnings=[10, 5, 3, 1],
            on_complete=perform_restart
        )

        if result == TimerResult.COMPLETED:
            await perform_restart()
        elif result == TimerResult.CANCELLED:
            try:
                await interaction.followup.send("Server-Restart abgebrochen!")
            except:
                pass

    @mc.command(name="cancel", description="Aktiven Timer abbrechen")
    @admin_only()
    async def mc_cancel(self, interaction: discord.Interaction):
        """Cancel active timer"""
        active = self.timer_mgr.get_active()
        if not active:
            await interaction.response.send_message(
                "Kein aktiver Timer.",
                ephemeral=True
            )
            return

        active.cancel()
        await interaction.response.send_message(
            f"Timer abgebrochen: {active.action_name}",
            ephemeral=True
        )

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  PLAYERS: /mc players list | kick | ban                        ║
    # ╚════════════════════════════════════════════════════════════════╝

    @players_grp.command(name="list", description="Online Spieler anzeigen")
    async def mc_players_list(self, interaction: discord.Interaction):
        """List online players"""
        await interaction.response.defer()

        if not await self.server.is_running():
            await interaction.followup.send("Server ist offline.")
            return

        try:
            players = await self.server.get_players()
            online, max_players = await self.server.get_player_count()

            embed = discord.Embed(
                title="Online Spieler",
                color=0x0099ff,
            )
            embed.add_field(
                name="Spieleranzahl",
                value=f"{online}/{max_players}",
                inline=True,
            )

            if players:
                embed.add_field(
                    name="Spieler",
                    value="\n".join(players),
                    inline=False,
                )
            else:
                embed.add_field(
                    name="Spieler",
                    value="Keine Spieler online",
                    inline=False,
                )

            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(
                f"Fehler beim Abrufen der Spielerliste: {e}",
                ephemeral=True
            )
            logger.error(f"Failed to get player list: {e}")

    @players_grp.command(name="kick", description="Spieler kicken")
    @admin_only()
    async def mc_kick(self, interaction: discord.Interaction,
                     player: str,
                     reason: str = "Gekickt vom Admin"):
        """Kick a player"""
        await interaction.response.defer()

        if not await self.server.is_running():
            await interaction.followup.send("Server ist offline.")
            return

        try:
            response = await self.server.rcon_command(
                f"kick {player} {reason}"
            )
            embed = discord.Embed(
                title="Spieler gekickt",
                description=f"{player} wurde gekickt.",
                color=0xff9900,
            )
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.followup.send(embed=embed)
            logger.info(f"Player {player} kicked by {interaction.user}")
        except Exception as e:
            await interaction.followup.send(
                f"Fehler beim Kicken: {e}",
                ephemeral=True
            )

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  BACKUP: /mc backup create | list | restore                    ║
    # ╚════════════════════════════════════════════════════════════════╝

    @backup_grp.command(name="create", description="Welt-Backup erstellen")
    @spieler_only()
    async def mc_backup_create(self, interaction: discord.Interaction,
                              name: Optional[str] = None):
        """Create world backup"""
        await interaction.response.defer()

        await interaction.followup.send("Backup wird erstellt...")

        try:
            success, msg, backup_path = await self.backup_mgr.create_backup(
                name=name,
                created_by=str(interaction.user)
            )

            if success:
                embed = discord.Embed(
                    title="Backup erstellt",
                    description=msg,
                    color=0x00ff00,
                )
                embed.set_footer(text=f"von {interaction.user.display_name}")
                await interaction.edit_original_response(content=None, embed=embed)
                logger.info(f"Backup created by {interaction.user}: {name}")
            else:
                embed = discord.Embed(
                    title="Backup fehlgeschlagen",
                    description=msg,
                    color=0xff0000,
                )
                await interaction.edit_original_response(content=None, embed=embed)
        except Exception as e:
            await interaction.followup.send(
                f"Fehler beim Erstellen des Backups: {e}",
                ephemeral=True
            )
            logger.error(f"Backup creation failed: {e}")

    @backup_grp.command(name="list", description="Verfuegbare Backups anzeigen")
    @spieler_only()
    async def mc_backup_list(self, interaction: discord.Interaction):
        """List available backups"""
        await interaction.response.defer()

        backups = self.backup_mgr.list_backups(max_results=20)

        if not backups:
            await interaction.followup.send("Keine Backups verfuegbar.")
            return

        embed = discord.Embed(
            title="Verfuegbare Backups",
            color=0x0099ff,
        )

        for backup in backups[:10]:  # Show top 10
            embed.add_field(
                name=backup["name"],
                value=f"**Groesse:** {backup['size_mb']:.1f} MB\n"
                      f"**Erstellt:** {backup['created']}\n"
                      f"**Von:** {backup['created_by']}",
                inline=False,
            )

        await interaction.followup.send(embed=embed)

    @backup_grp.command(name="restore", description="Backup wiederherstellen")
    @owner_only()
    async def mc_backup_restore(self, interaction: discord.Interaction,
                               name: str):
        """Restore world from backup"""
        await interaction.response.defer()

        # Confirm restoration
        embed = discord.Embed(
            title="Wiederherstellung bestaetigen",
            description=f"Moechtest du das Backup **{name}** wirklich wiederherstellen?\n"
                       "Die aktuelle Welt wird geloescht!",
            color=0xff0000,
        )

        await interaction.followup.send(embed=embed)

        try:
            success, msg = await self.backup_mgr.restore(name)

            if success:
                embed = discord.Embed(
                    title="Wiederhergestellt",
                    description=msg,
                    color=0x00ff00,
                )
            else:
                embed = discord.Embed(
                    title="Wiederherstellung fehlgeschlagen",
                    description=msg,
                    color=0xff0000,
                )

            await interaction.followup.send(embed=embed)
            logger.info(f"Backup restored by {interaction.user}: {name}")
        except Exception as e:
            await interaction.followup.send(
                f"Fehler bei der Wiederherstellung: {e}",
                ephemeral=True
            )
            logger.error(f"Backup restore failed: {e}")

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  ADMIN COMMANDS: say, difficulty, weather, time, gamemode      ║
    # ╚════════════════════════════════════════════════════════════════╝

    @mc.command(name="say", description="Nachricht im Spiel ansagen")
    @admin_only()
    async def mc_say(self, interaction: discord.Interaction,
                    message: str):
        """Broadcast message to players"""
        await interaction.response.defer()

        if not await self.server.is_running():
            await interaction.followup.send("Server ist offline.")
            return

        try:
            await self.server.rcon_command(f"say {message}")
            embed = discord.Embed(
                title="Nachricht gesendet",
                description=message,
                color=0x00ff00,
            )
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.followup.send(embed=embed)
            logger.info(f"Broadcast by {interaction.user}: {message}")
        except Exception as e:
            await interaction.followup.send(
                f"Fehler beim Senden der Nachricht: {e}",
                ephemeral=True
            )

    @mc.command(name="difficulty", description="Schwierigkeitsgrad einstellen")
    @admin_only()
    async def mc_difficulty(self, interaction: discord.Interaction,
                           level: str):
        """Set server difficulty"""
        valid_levels = ["peaceful", "easy", "normal", "hard"]

        if level.lower() not in valid_levels:
            await interaction.response.send_message(
                f"Ungueltige Schwierigkeit. Valide Werte: {', '.join(valid_levels)}",
                ephemeral=True
            )
            return

        await interaction.response.defer()

        if not await self.server.is_running():
            await interaction.followup.send("Server ist offline.")
            return

        try:
            await self.server.rcon_command(f"difficulty {level.lower()}")
            embed = discord.Embed(
                title="Schwierigkeit geaendert",
                description=f"Neue Schwierigkeit: {level}",
                color=0x00ff00,
            )
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.followup.send(embed=embed)
            logger.info(f"Difficulty changed by {interaction.user}: {level}")
        except Exception as e:
            await interaction.followup.send(
                f"Fehler beim Aendern der Schwierigkeit: {e}",
                ephemeral=True
            )

    @mc.command(name="weather", description="Wetter einstellen")
    @admin_only()
    async def mc_weather(self, interaction: discord.Interaction,
                        weather_type: str):
        """Set server weather"""
        valid_types = ["clear", "rain", "thunder"]

        if weather_type.lower() not in valid_types:
            await interaction.response.send_message(
                f"Ungueltige Wetter. Valide Werte: {', '.join(valid_types)}",
                ephemeral=True
            )
            return

        await interaction.response.defer()

        if not await self.server.is_running():
            await interaction.followup.send("Server ist offline.")
            return

        try:
            await self.server.rcon_command(f"weather {weather_type.lower()}")
            embed = discord.Embed(
                title="Wetter geaendert",
                description=f"Neues Wetter: {weather_type}",
                color=0x00ff00,
            )
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.followup.send(embed=embed)
            logger.info(f"Weather changed by {interaction.user}: {weather_type}")
        except Exception as e:
            await interaction.followup.send(
                f"Fehler beim Aendern des Wetters: {e}",
                ephemeral=True
            )

    @mc.command(name="time", description="Tageszeit einstellen")
    @admin_only()
    async def mc_time(self, interaction: discord.Interaction,
                     value: str):
        """Set server time"""
        valid_presets = ["day", "night", "noon", "midnight"]

        # Check if it's a preset or a number
        if value.lower() not in valid_presets:
            try:
                time_val = int(value)
                if not (0 <= time_val <= 24000):
                    await interaction.response.send_message(
                        f"Zeit muss zwischen 0 und 24000 liegen oder ein Preset sein.",
                        ephemeral=True
                    )
                    return
            except ValueError:
                await interaction.response.send_message(
                    f"Ungueltige Zeit. Valide Werte: {', '.join(valid_presets)} oder 0-24000",
                    ephemeral=True
                )
                return

        await interaction.response.defer()

        if not await self.server.is_running():
            await interaction.followup.send("Server ist offline.")
            return

        try:
            await self.server.rcon_command(f"time set {value}")
            embed = discord.Embed(
                title="Tageszeit geaendert",
                description=f"Neue Zeit: {value}",
                color=0x00ff00,
            )
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.followup.send(embed=embed)
            logger.info(f"Time changed by {interaction.user}: {value}")
        except Exception as e:
            await interaction.followup.send(
                f"Fehler beim Aendern der Zeit: {e}",
                ephemeral=True
            )

    @mc.command(name="gamemode", description="Spielmodus setzen")
    @admin_only()
    async def mc_gamemode(self, interaction: discord.Interaction,
                         mode: str,
                         player: Optional[str] = None):
        """Set player gamemode"""
        valid_modes = ["survival", "creative", "adventure", "spectator"]

        if mode.lower() not in valid_modes:
            await interaction.response.send_message(
                f"Ungueltige Spielmodus. Valide Werte: {', '.join(valid_modes)}",
                ephemeral=True
            )
            return

        await interaction.response.defer()

        if not await self.server.is_running():
            await interaction.followup.send("Server ist offline.")
            return

        try:
            if player:
                cmd = f"gamemode {mode.lower()} {player}"
            else:
                cmd = f"gamemode {mode.lower()}"

            await self.server.rcon_command(cmd)

            target = player or "dir selbst"
            embed = discord.Embed(
                title="Spielmodus geaendert",
                description=f"Spielmodus fuer {target}: {mode}",
                color=0x00ff00,
            )
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.followup.send(embed=embed)
            logger.info(f"Gamemode changed by {interaction.user}: {mode} for {player or 'self'}")
        except Exception as e:
            await interaction.followup.send(
                f"Fehler beim Aendern des Spielmodus: {e}",
                ephemeral=True
            )

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  OWNER COMMANDS: RCON raw command execution                    ║
    # ╚════════════════════════════════════════════════════════════════╝

    @mc.command(name="command", description="RCON Befehl ausfuehren")
    @owner_only()
    async def mc_command(self, interaction: discord.Interaction,
                        cmd: str):
        """Execute raw RCON command (owner only)"""
        await interaction.response.defer()

        if not await self.server.is_running():
            await interaction.followup.send("Server ist offline.")
            return

        try:
            response = await self.server.rcon_command(cmd)

            # Truncate long responses
            if len(response) > 1900:
                response = response[:1900] + "... (gekuerzt)"

            embed = discord.Embed(
                title="RCON Befehl ausgefuehrt",
                description=f"**Befehl:** `{cmd}`\n\n**Antwort:**\n```\n{response}\n```",
                color=0x0099ff,
            )
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.followup.send(embed=embed)
            logger.info(f"RCON command by {interaction.user}: {cmd}")
        except Exception as e:
            embed = discord.Embed(
                title="RCON Fehler",
                description=f"**Befehl:** `{cmd}`\n**Fehler:** {e}",
                color=0xff0000,
            )
            await interaction.followup.send(embed=embed)
            logger.error(f"RCON command failed: {cmd} - {e}")

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
    """Load this cog"""
    await bot.add_cog(MinecraftCog(bot))
