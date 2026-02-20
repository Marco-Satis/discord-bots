"""
Satisfactory Unified Cog - All /sat commands with sub-groups

Command Structure:
  /sat start|stop|restart|status|cancel          (Core - Admin/Alle)
  /sat players online|ban|unban|bans              (Spieler-Verwaltung)
  /sat backup create|save|download|list|restore  (Backup-Verwaltung)
  /sat config settings|playerlimit|autosave|console|load|update|stats (Konfiguration)
  /sat blueprints upload|list|download|delete     (Blueprint-Manager)
  /sat whitelist add|remove|list                  (Whitelist)
  /sat blacklist add|remove|list                  (Blacklist)
"""

import asyncio
import time
import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional, Dict
from pathlib import Path

from utils import get_logger, format_uptime, format_bytes, status_emoji
from utils.permissions import admin_only, spieler_only, owner_only, is_admin, is_owner, server_online_required
from modules.restart_timer import TimerResult

logger = get_logger("cogs.satisfactory")


class SatisfactoryCog(commands.Cog):
    """All Satisfactory server commands unified under /sat"""

    # ==================================================================
    # Group & Sub-Group Definitions
    # ==================================================================

    sat = app_commands.Group(
        name="sat", description="Satisfactory Server Befehle"
    )
    players_grp = app_commands.Group(
        name="players", parent=sat, description="Spieler-Verwaltung"
    )
    backup_grp = app_commands.Group(
        name="backup", parent=sat, description="Backup & Savegame"
    )
    config_grp = app_commands.Group(
        name="config", parent=sat, description="Server-Konfiguration"
    )
    blueprints_grp = app_commands.Group(
        name="blueprints", parent=sat, description="Blueprint-Manager"
    )
    whitelist_grp = app_commands.Group(
        name="whitelist", parent=sat, description="Whitelist-Verwaltung"
    )
    blacklist_grp = app_commands.Group(
        name="blacklist", parent=sat, description="Blacklist-Verwaltung"
    )

    # ==================================================================
    # Init
    # ==================================================================

    # Cooldown settings: command_name -> seconds
    COOLDOWNS = {
        "start": 30,
        "restart": 60,
        "stop": 30,
    }

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.server = bot.sat_server
        self.api = bot.sat_api
        self.timer_mgr = bot.timer_mgr
        self._cooldowns: Dict[str, float] = {}  # command_name -> last_used timestamp

    def _check_cooldown(self, command: str) -> Optional[int]:
        """Check if command is on cooldown. Returns remaining seconds or None."""
        cooldown_secs = self.COOLDOWNS.get(command)
        if not cooldown_secs:
            return None
        last_used = self._cooldowns.get(command, 0)
        elapsed = time.monotonic() - last_used
        if elapsed < cooldown_secs:
            return int(cooldown_secs - elapsed)
        return None

    def _set_cooldown(self, command: str) -> None:
        """Set cooldown timestamp for a command."""
        self._cooldowns[command] = time.monotonic()

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  CORE: /sat start | stop | restart | status | cancel         ║
    # ╚════════════════════════════════════════════════════════════════╝

    @sat.command(name="status", description="Server-Status anzeigen")
    async def sat_status(self, interaction: discord.Interaction):
        await interaction.response.defer()

        status = await self.server.get_status()
        online = status["running"]

        embed = discord.Embed(
            title=f"{status_emoji(online)} Satisfactory Server",
            color=0x00ff00 if online else 0xff0000,
        )

        if online:
            try:
                state = await self.api.query_server_state()
                embed.add_field(
                    name="Spieler",
                    value=f"{state.num_players}/{state.player_limit}",
                    inline=True,
                )
                embed.add_field(
                    name="Tick Rate",
                    value=f"{state.average_tick_rate:.1f} FPS",
                    inline=True,
                )
                if state.active_session:
                    embed.add_field(
                        name="Session", value=state.active_session, inline=True
                    )
                if state.tech_tier > 0:
                    embed.add_field(
                        name="Tech-Tier", value=str(state.tech_tier), inline=True
                    )
            except Exception as e:
                logger.debug(f"API not available: {e}")
                embed.add_field(name="API", value="Nicht erreichbar", inline=True)

            embed.add_field(
                name="Uptime", value=format_uptime(status["uptime"]), inline=True
            )
            embed.add_field(
                name="CPU", value=f"{status['cpu_percent']:.1f}%", inline=True
            )
            embed.add_field(
                name="RAM", value=f"{status['memory_mb']} MB", inline=True
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

    @sat.command(name="start", description="Server starten")
    @admin_only()
    async def sat_start(self, interaction: discord.Interaction):
        await interaction.response.defer()

        remaining = self._check_cooldown("start")
        if remaining:
            await interaction.followup.send(
                f"Bitte warte noch {remaining}s bevor du /sat start erneut verwendest.",
                ephemeral=True,
            )
            return

        if await self.server.is_running():
            await interaction.followup.send("Server laeuft bereits!")
            return

        self._set_cooldown("start")
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
            await interaction.edit_original_response(content=f"Fehler: {msg}")

    @sat.command(name="stop", description="Server stoppen")
    @admin_only()
    @server_online_required("server")
    async def sat_stop(self, interaction: discord.Interaction):
        await interaction.response.defer()

        remaining = self._check_cooldown("stop")
        if remaining:
            await interaction.followup.send(
                f"Bitte warte noch {remaining}s bevor du /sat stop erneut verwendest.",
                ephemeral=True,
            )
            return

        if self.timer_mgr.has_active:
            await interaction.followup.send(
                "Es laeuft bereits ein Timer. Nutze `/sat cancel` zuerst."
            )
            return

        players_online = 0
        try:
            state = await self.api.query_server_state()
            players_online = state.num_players
        except Exception:
            pass

        if players_online > 0:
            await interaction.followup.send(
                f"{players_online} Spieler online! "
                f"Server wird in 5 Minuten gestoppt."
            )
            timer = self.timer_mgr.get_or_create(
                "satisfactory", api=self.api, channel=interaction.channel
            )
            result = await timer.countdown(
                duration_minutes=5, action_name="Stop", warnings=[5, 3, 1]
            )
            if result == TimerResult.CANCELLED:
                return
            if result != TimerResult.COMPLETED:
                await interaction.channel.send("Timer-Fehler beim Stop.")
                return
        else:
            await interaction.followup.send("Server wird gestoppt...")

        self._set_cooldown("stop")
        success, msg = await self.server.stop()

        if success:
            embed = discord.Embed(
                title=f"{status_emoji(False)} Server gestoppt",
                description=msg,
                color=0xff0000,
            )
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.channel.send(embed=embed)
            logger.info(f"Server stopped by {interaction.user}")
        else:
            await interaction.channel.send(f"Fehler: {msg}")

    @sat.command(name="restart", description="Server neustarten (sofort oder mit Countdown)")
    @admin_only()
    async def sat_restart(self, interaction: discord.Interaction):
        remaining = self._check_cooldown("restart")
        if remaining:
            await interaction.response.send_message(
                f"Bitte warte noch {remaining}s bevor du /sat restart erneut verwendest.",
                ephemeral=True,
            )
            return

        if not await self.server.is_running():
            await interaction.response.defer()
            await interaction.followup.send("Server ist offline, wird gestartet...")
            success, msg = await self.server.start()
            await interaction.edit_original_response(content=msg)
            return

        if self.timer_mgr.has_active:
            await interaction.response.send_message(
                "Es laeuft bereits ein Timer. Nutze `/sat cancel` zuerst.",
                ephemeral=True,
            )
            return

        # Show restart mode selection
        self._set_cooldown("restart")
        view = RestartModeView(self, interaction.user.id)
        await interaction.response.send_message(
            "🔄 **Server-Neustart** — Wie soll neugestartet werden?",
            view=view,
        )

    async def _do_restart_countdown(self, channel, user, minutes):
        """Execute restart with countdown"""
        warnings = {
            10: [10, 5, 3, 1],
            5: [5, 3, 1],
            3: [3, 1],
            1: [1],
        }
        warn_list = warnings.get(minutes, [minutes] if minutes > 1 else [])

        await channel.send(
            f"🔄 Server-Neustart in **{minutes} Minuten**! "
            f"Nutze `/sat cancel` zum Abbrechen."
        )
        timer = self.timer_mgr.get_or_create(
            "satisfactory", api=self.api, channel=channel
        )
        result = await timer.countdown(
            duration_minutes=minutes, action_name="Restart", warnings=warn_list
        )

        if result == TimerResult.CANCELLED:
            return
        if result != TimerResult.COMPLETED:
            await channel.send("Timer-Fehler beim Restart.")
            return

        await self._execute_restart(channel, user)

    async def _do_restart_immediate(self, channel, user):
        """Execute restart immediately"""
        await channel.send("⚡ Server wird **sofort** neugestartet...")
        await self._execute_restart(channel, user)

    async def _execute_restart(self, channel, user):
        """Common restart execution"""
        success, msg = await self.server.restart()

        if success:
            embed = discord.Embed(
                title=f"{status_emoji(True)} Server neugestartet",
                description=msg,
                color=0x00ff00,
            )
            embed.set_footer(text=f"von {user.display_name}")
            await channel.send(embed=embed)
            logger.info(f"Server restarted by {user}")
        else:
            await channel.send(f"Restart fehlgeschlagen: {msg}")

    @sat.command(name="cancel", description="Laufenden Restart/Stop abbrechen")
    @admin_only()
    async def sat_cancel(self, interaction: discord.Interaction):
        active = self.timer_mgr.get_active()
        if active:
            active.cancel()
            await interaction.response.send_message(
                f"{active.action_name} wird abgebrochen..."
            )
            logger.info(f"Timer cancelled by {interaction.user}")
        else:
            await interaction.response.send_message(
                "Kein laufender Vorgang zum Abbrechen.", ephemeral=True
            )

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  PLAYERS: /sat players online|ban|unban|bans                 ║
    # ╚════════════════════════════════════════════════════════════════╝

    @players_grp.command(name="online", description="Online-Spieler anzeigen")
    @spieler_only()
    @server_online_required("server")
    async def players_online(self, interaction: discord.Interaction):
        await interaction.response.defer()

        try:
            state = await self.api.query_server_state()
            embed = discord.Embed(
                title=f"Spieler ({state.num_players}/{state.player_limit})",
                color=0x3498db,
            )

            if state.num_players == 0:
                embed.description = "Keine Spieler online."
            else:
                try:
                    result = await self.api.run_command("ListPlayers")
                    if result and "Error" not in result:
                        embed.description = f"```\n{result}\n```"
                    else:
                        embed.description = (
                            f"{state.num_players} Spieler online.\n"
                            f"*(Detaillierte Spielerliste nicht verfuegbar)*"
                        )
                except Exception:
                    embed.description = (
                        f"{state.num_players} Spieler online.\n"
                        f"*(Spielerliste ueber API nicht verfuegbar)*"
                    )

            embed.add_field(
                name="Session", value=state.active_session or "\u2014", inline=True
            )
            embed.add_field(
                name="Tick Rate",
                value=f"{state.average_tick_rate:.1f} FPS",
                inline=True,
            )
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"Fehler: {e}")

    @players_grp.command(name="ban", description="Spieler permanent bannen (IP-Block)")
    @app_commands.describe(player="Name des Spielers", reason="Grund fuer den Ban")
    @admin_only()
    async def players_ban(
        self,
        interaction: discord.Interaction,
        player: str,
        reason: str = "Kein Grund angegeben",
    ):
        await interaction.response.defer()

        ip_tracker = getattr(self.bot, "player_ip_tracker", None)
        if not ip_tracker:
            await interaction.followup.send("❌ IP-Tracker nicht verfügbar.")
            return

        # Phase 1b: SaveGame vor Ban
        try:
            await self.api.save_game()
            await asyncio.sleep(3)
        except Exception as e:
            logger.warning(f"Save before ban failed (continuing): {e}")

        success, msg = await ip_tracker.ban_player(
            player, reason, interaction.user.display_name
        )

        if success:
            # Also add to blacklist for record keeping
            if hasattr(self.bot, "blacklist_mgr"):
                await self.bot.blacklist_mgr.add(
                    player, reason, interaction.user.display_name
                )

            embed = discord.Embed(
                title="🚫 Spieler gebannt",
                description=msg,
                color=0xe74c3c,
            )
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.followup.send(embed=embed)
            logger.info(f"Player banned: {player} by {interaction.user} - {reason}")
        else:
            await interaction.followup.send(f"❌ {msg}")

    @players_ban.autocomplete("player")
    async def ban_autocomplete(
        self, interaction: discord.Interaction, current: str
    ):
        ip_tracker = getattr(self.bot, "player_ip_tracker", None)
        if not ip_tracker:
            return []
        mappings = ip_tracker.get_all_mappings()
        return [
            app_commands.Choice(name=name, value=name)
            for name in mappings
            if current.lower() in name.lower()
        ][:25]

    @players_grp.command(name="unban", description="Spieler-Ban aufheben (IP freigeben)")
    @app_commands.describe(player="Name des Spielers")
    @admin_only()
    async def players_unban(self, interaction: discord.Interaction, player: str):
        await interaction.response.defer()

        ip_tracker = getattr(self.bot, "player_ip_tracker", None)
        if not ip_tracker:
            await interaction.followup.send("❌ IP-Tracker nicht verfügbar.")
            return

        success, msg = await ip_tracker.unban_player(player)

        if success:
            # Also remove from blacklist
            if hasattr(self.bot, "blacklist_mgr"):
                await self.bot.blacklist_mgr.remove(player)

            embed = discord.Embed(
                title="✅ Ban aufgehoben",
                description=msg,
                color=0x2ecc71,
            )
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.followup.send(embed=embed)
            logger.info(f"Player unbanned: {player} by {interaction.user}")
        else:
            await interaction.followup.send(f"❌ {msg}")

    @players_unban.autocomplete("player")
    async def unban_autocomplete(
        self, interaction: discord.Interaction, current: str
    ):
        ip_tracker = getattr(self.bot, "player_ip_tracker", None)
        if not ip_tracker:
            return []
        bans = ip_tracker.get_all_bans()
        return [
            app_commands.Choice(name=f"{b['name']} ({b['ip']})", value=b["name"])
            for b in bans
            if current.lower() in b["name"].lower()
        ][:25]

    @players_grp.command(name="bans", description="Aktive Bans anzeigen")
    @admin_only()
    async def players_bans(self, interaction: discord.Interaction):
        await interaction.response.defer()

        ip_tracker = getattr(self.bot, "player_ip_tracker", None)
        if not ip_tracker:
            await interaction.followup.send("❌ IP-Tracker nicht verfügbar.")
            return

        bans = ip_tracker.get_all_bans()

        embed = discord.Embed(
            title=f"🚫 Aktive Bans ({len(bans)})",
            color=0xe74c3c,
        )

        if not bans:
            embed.description = "Keine aktiven Bans."
        else:
            entries = []
            for ban in bans:
                name = ban.get("name", "?")
                ip = ban.get("ip", "?")
                reason = ban.get("reason", "Kein Grund angegeben")
                date = ban.get("date", "?")
                banned_by = ban.get("banned_by", "?")
                entries.append(
                    f"\u2022 **{name}** ({ip})\n"
                    f"   Grund: {reason}\n"
                    f"   Datum: {date} | gebannt von: {banned_by}"
                )
            embed.description = "\n".join(entries)

        await interaction.followup.send(embed=embed)

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  BACKUP: /sat backup create|save|download|list|restore       ║
    # ╚════════════════════════════════════════════════════════════════╝

    @backup_grp.command(name="create", description="Manuelles Backup erstellen")
    @app_commands.describe(name="Optionaler Name fuer das Backup")
    @spieler_only()
    async def backup_create(
        self, interaction: discord.Interaction, name: str = None
    ):
        await interaction.response.defer()

        if await self.server.is_running():
            try:
                await self.api.save_game()
                await interaction.followup.send(
                    "Spiel gespeichert, erstelle Backup..."
                )
            except Exception:
                await interaction.followup.send("Erstelle Backup...")
        else:
            await interaction.followup.send(
                "Server offline. Erstelle Backup der vorhandenen Daten..."
            )

        success, msg, backup_path = await self.bot.backup_mgr.create_backup(
            name=name, created_by=interaction.user.display_name
        )

        if success:
            embed = discord.Embed(
                title="Backup erstellt", description=msg, color=0x2ecc71
            )
            embed.add_field(
                name="Backups gesamt",
                value=str(self.bot.backup_mgr.count()),
                inline=True,
            )
            embed.add_field(
                name="Speicher gesamt",
                value=format_bytes(self.bot.backup_mgr.total_size()),
                inline=True,
            )
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.edit_original_response(content=None, embed=embed)
            logger.info(f"Backup created by {interaction.user}: {msg}")
        else:
            await interaction.edit_original_response(content=f"Fehler: {msg}")

    @backup_grp.command(name="save", description="Spiel speichern via API")
    @spieler_only()
    @server_online_required("server")
    async def backup_save(self, interaction: discord.Interaction):
        await interaction.response.defer()

        try:
            success = await self.api.save_game()
            if success:
                embed = discord.Embed(
                    title="Spiel gespeichert",
                    description="Savegame wurde erfolgreich gespeichert.",
                    color=0x2ecc71,
                )
                embed.set_footer(text=f"von {interaction.user.display_name}")
                await interaction.followup.send(embed=embed)
            else:
                await interaction.followup.send("Speichern fehlgeschlagen!")
        except Exception as e:
            await interaction.followup.send(f"Fehler: {e}")

    @backup_grp.command(
        name="download", description="Neuestes Savegame herunterladen"
    )
    @spieler_only()
    async def backup_download(self, interaction: discord.Interaction):
        await interaction.response.defer()

        try:
            stats_mgr = self.bot.savegame_stats
            latest = stats_mgr.get_latest_save()

            if not latest:
                await interaction.followup.send("Keine Savegames gefunden.")
                return

            save_path = Path(latest["path"])

            if latest["size_bytes"] > 25 * 1024 * 1024:
                await interaction.followup.send(
                    f"Savegame zu gross fuer Discord ({latest['size_human']}). "
                    f"Max. 25 MB.\nDatei: `{save_path.name}`"
                )
                return

            file = discord.File(save_path, filename=save_path.name)
            embed = discord.Embed(
                title="Savegame Download",
                description=f"**{latest['name']}**",
                color=0x3498db,
            )
            embed.add_field(
                name="Groesse", value=latest["size_human"], inline=True
            )
            embed.add_field(
                name="Letzte Aenderung",
                value=latest["modified_str"],
                inline=True,
            )
            await interaction.followup.send(embed=embed, file=file)
            logger.info(
                f"Savegame downloaded by {interaction.user}: {latest['name']}"
            )
        except Exception as e:
            await interaction.followup.send(f"Download fehlgeschlagen: {e}")

    @backup_grp.command(name="list", description="Alle Backups auflisten")
    @spieler_only()
    async def backup_list(self, interaction: discord.Interaction):
        await interaction.response.defer()

        backups = self.bot.backup_mgr.list_backups(max_results=20)

        if not backups:
            await interaction.followup.send("Keine Backups vorhanden.")
            return

        embed = discord.Embed(
            title=f"Backups ({len(backups)})", color=0x3498db
        )

        entries = []
        for i, bp in enumerate(backups[:15], 1):
            created = bp.get("created_at", "?")[:16].replace("T", " ")
            typ = "Auto" if bp.get("type") == "auto" else "Manuell"
            entries.append(
                f"`{i}.` **{bp['name']}**\n"
                f"   {bp.get('size_human', '?')} | {created} | {typ} | {bp.get('created_by', '?')}"
            )

        embed.description = "\n".join(entries)
        total = format_bytes(self.bot.backup_mgr.total_size())
        embed.set_footer(
            text=f"Gesamt: {total} | Max: {self.bot.backup_mgr.max_backups}"
        )
        await interaction.followup.send(embed=embed)

    @backup_grp.command(
        name="restore",
        description="Backup wiederherstellen (Server muss offline sein)",
    )
    @app_commands.describe(backup_name="Name des Backups")
    @owner_only()
    async def backup_restore(
        self, interaction: discord.Interaction, backup_name: str
    ):
        await interaction.response.defer()

        if await self.server.is_running():
            await interaction.followup.send(
                "Server muss offline sein fuer ein Restore!\n"
                "Nutze `/sat stop` zuerst."
            )
            return

        bp = self.bot.backup_mgr.get_backup(backup_name)
        if not bp:
            await interaction.followup.send(
                f"Backup '{backup_name}' nicht gefunden!"
            )
            return

        view = RestoreConfirmView(self, interaction, backup_name)
        embed = discord.Embed(
            title="Restore bestaetigen",
            description=(
                f"Backup **{backup_name}** wiederherstellen?\n\n"
                f"Groesse: {bp.get('size_human', '?')}\n"
                f"Erstellt: {bp.get('created_at', '?')[:16]}\n\n"
                f"**ACHTUNG: Aktuelle Savegames werden ueberschrieben!**\n"
                f"*(Ein Pre-Restore Backup wird automatisch erstellt)*"
            ),
            color=0xe74c3c,
        )
        await interaction.followup.send(embed=embed, view=view)

    @backup_restore.autocomplete("backup_name")
    async def restore_autocomplete(
        self, interaction: discord.Interaction, current: str
    ):
        backups = self.bot.backup_mgr.list_backups(max_results=25)
        choices = []
        for bp in backups:
            name = bp["name"]
            if current.lower() in name.lower():
                label = f"{name} ({bp.get('size_human', '?')})"
                choices.append(
                    app_commands.Choice(name=label[:100], value=name)
                )
        return choices[:25]

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  CONFIG: /sat config settings|playerlimit|autosave|...       ║
    # ╚════════════════════════════════════════════════════════════════╝

    @config_grp.command(
        name="settings", description="Servereinstellungen anzeigen"
    )
    @spieler_only()
    async def config_settings(self, interaction: discord.Interaction):
        await interaction.response.defer()

        embed = discord.Embed(
            title="Satisfactory Servereinstellungen", color=0x3498db
        )

        if not await self.server.is_running():
            embed.description = (
                "Server ist offline. Einstellungen nicht abrufbar."
            )
            await interaction.followup.send(embed=embed)
            return

        try:
            options = await self.api.get_server_options()
            state = await self.api.query_server_state()

            embed.add_field(
                name="Session", value=state.active_session or "\u2014", inline=True
            )
            embed.add_field(
                name="Spielerlimit",
                value=str(state.player_limit),
                inline=True,
            )
            embed.add_field(
                name="Tech-Tier",
                value=str(state.tech_tier) if state.tech_tier > 0 else "\u2014",
                inline=True,
            )
            embed.add_field(
                name="Spielphase",
                value=state.game_phase or "\u2014",
                inline=True,
            )
            embed.add_field(
                name="Pausiert",
                value="Ja" if state.is_paused else "Nein",
                inline=True,
            )
            embed.add_field(
                name="Tick Rate",
                value=f"{state.average_tick_rate:.1f} FPS",
                inline=True,
            )

            if options:
                display_keys = {
                    "FG.DSAutoPause": "Auto-Pause",
                    "FG.DSAutoSaveOnDisconnect": "Save bei Disconnect",
                    "FG.AutosaveInterval": "Autosave-Intervall",
                    "FG.ServerRestartTimeSlot": "Restart-Zeitfenster",
                    "FG.SendGameplayData": "Gameplay-Daten senden",
                    "FG.NetworkQuality": "Netzwerkqualitaet",
                }
                settings_text = []
                for key, label in display_keys.items():
                    val = options.get(key)
                    if val is not None:
                        settings_text.append(f"**{label}:** {val}")

                if settings_text:
                    embed.add_field(
                        name="Server-Optionen",
                        value="\n".join(settings_text),
                        inline=False,
                    )

        except Exception as e:
            embed.add_field(
                name="Fehler",
                value=f"Einstellungen konnten nicht abgerufen werden: {e}",
                inline=False,
            )

        await interaction.followup.send(embed=embed)

    @config_grp.command(
        name="playerlimit", description="Max. Spieleranzahl aendern"
    )
    @app_commands.describe(limit="Neues Spielerlimit")
    @app_commands.choices(
        limit=[
            app_commands.Choice(name="4 Spieler", value=4),
            app_commands.Choice(name="8 Spieler", value=8),
            app_commands.Choice(name="16 Spieler", value=16),
        ]
    )
    @admin_only()
    @server_online_required("server")
    async def config_playerlimit(
        self, interaction: discord.Interaction, limit: int
    ):
        await interaction.response.defer()

        try:
            success = await self.api.apply_server_options(
                {"FG.PlayerLimit": str(limit)}
            )
            if success:
                embed = discord.Embed(
                    title="Spielerlimit geaendert",
                    description=f"Neues Limit: **{limit} Spieler**",
                    color=0x2ecc71,
                )
                embed.set_footer(text=f"von {interaction.user.display_name}")
                await interaction.followup.send(embed=embed)
                logger.info(
                    f"Player limit changed to {limit} by {interaction.user}"
                )
            else:
                await interaction.followup.send("Aenderung fehlgeschlagen!")
        except Exception as e:
            await interaction.followup.send(f"Fehler: {e}")

    @config_grp.command(
        name="autosave", description="Autosave-Intervall aendern"
    )
    @app_commands.describe(seconds="Intervall in Sekunden (min. 30)")
    @admin_only()
    @server_online_required("server")
    async def config_autosave(
        self, interaction: discord.Interaction, seconds: int
    ):
        if seconds < 30:
            await interaction.response.send_message(
                "Minimum ist 30 Sekunden.", ephemeral=True
            )
            return

        await interaction.response.defer()

        try:
            success = await self.api.apply_server_options(
                {"FG.AutosaveInterval": str(seconds)}
            )
            if success:
                display = f"{seconds // 60} Minuten" if seconds >= 60 else f"{seconds} Sekunden"

                embed = discord.Embed(
                    title="Autosave geaendert",
                    description=f"Neues Intervall: **{display}**",
                    color=0x2ecc71,
                )
                embed.set_footer(text=f"von {interaction.user.display_name}")
                await interaction.followup.send(embed=embed)
                logger.info(
                    f"Autosave interval changed to {seconds}s by {interaction.user}"
                )
            else:
                await interaction.followup.send("Aenderung fehlgeschlagen!")
        except Exception as e:
            await interaction.followup.send(f"Fehler: {e}")

    @config_grp.command(
        name="console", description="Konsolen-Befehl ausfuehren (Owner)"
    )
    @app_commands.describe(command="Server-Befehl")
    @owner_only()
    @server_online_required("server")
    async def config_console(
        self, interaction: discord.Interaction, command: str
    ):
        await interaction.response.defer(ephemeral=True)

        try:
            result = await self.api.run_command(command)
            embed = discord.Embed(title="Konsolen-Befehl", color=0x95a5a6)
            embed.add_field(
                name="Befehl", value=f"`{command}`", inline=False
            )
            embed.add_field(
                name="Ergebnis",
                value=f"```\n{result[:1900] if result else 'Kein Output'}\n```",
                inline=False,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            logger.info(
                f"Console command by {interaction.user}: {command}"
            )
        except Exception as e:
            await interaction.followup.send(f"Fehler: {e}", ephemeral=True)

    @config_grp.command(
        name="load", description="Savegame laden (Owner)"
    )
    @app_commands.describe(savename="Name des Savegames")
    @owner_only()
    @server_online_required("server")
    async def config_load(
        self, interaction: discord.Interaction, savename: str
    ):
        await interaction.response.defer()

        view = LoadConfirmView(self, interaction, savename)
        embed = discord.Embed(
            title="Savegame laden bestaetigen",
            description=(
                f"Savegame **{savename}** laden?\n\n"
                f"Alle verbundenen Spieler werden getrennt.\n"
                f"Der aktuelle Spielstand geht verloren wenn nicht gespeichert!"
            ),
            color=0xe67e22,
        )
        await interaction.followup.send(embed=embed, view=view)

    @config_load.autocomplete("savename")
    async def load_autocomplete(
        self, interaction: discord.Interaction, current: str
    ):
        if hasattr(self.bot, "savegame_stats"):
            saves = self.bot.savegame_stats.list_saves(max_results=25)
            return [
                app_commands.Choice(
                    name=f"{s['name']} ({s['size_human']} - {s['modified_str']})"[:100],
                    value=s["name"],
                )
                for s in saves
                if current.lower() in s["name"].lower()
            ][:25]
        return []

    @config_grp.command(
        name="update", description="Server-Update via SteamCMD (Owner)"
    )
    @owner_only()
    async def config_update(self, interaction: discord.Interaction):
        await interaction.response.defer()

        if await self.server.is_running():
            await interaction.followup.send(
                "Server muss offline sein fuer ein Update!\n"
                "Nutze `/sat stop` zuerst."
            )
            return

        await interaction.followup.send(
            "Starte Server-Update via SteamCMD..."
        )

        # Delegate to UpdateChecker if available (🔵-7)
        update_checker = getattr(self.bot, "update_checker", None)
        if update_checker:
            success, msg = await update_checker.perform_update(self.server)
        else:
            success, msg = await self._run_steamcmd_update()

        if success:
            embed = discord.Embed(
                title="Server-Update abgeschlossen",
                description=msg,
                color=0x2ecc71,
            )
            embed.set_footer(text="Starte den Server mit /sat start")
            await interaction.edit_original_response(
                content=None, embed=embed
            )
            logger.info(f"Server update completed by {interaction.user}")
        else:
            await interaction.edit_original_response(
                content=f"Update fehlgeschlagen: {msg}"
            )

    async def _run_steamcmd_update(self) -> tuple:
        """Fallback SteamCMD update without UpdateChecker"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "-u", self.server.server_user,
                "/usr/games/steamcmd",
                "+force_install_dir", str(self.server.server_path),
                "+login", "anonymous",
                "+app_update", "1690800", "validate",
                "+quit",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=600
            )
            output = stdout.decode()[-500:]

            if proc.returncode == 0:
                return True, f"SteamCMD Update erfolgreich.\n```\n{output}\n```"
            else:
                err = stderr.decode()[-500:]
                return False, f"Exit {proc.returncode}:\n```\n{err}\n```"

        except asyncio.TimeoutError:
            return False, "Update-Timeout nach 10 Minuten!"
        except Exception as e:
            return False, f"Update-Fehler: {e}"

    @config_grp.command(
        name="settings_backup", description="Server-Einstellungen sichern"
    )
    @app_commands.describe(name="Optionaler Name fuer das Backup")
    @admin_only()
    @server_online_required("server")
    async def config_settings_backup(
        self, interaction: discord.Interaction, name: str = None
    ):
        await interaction.response.defer()

        settings_bk = getattr(self.bot, "settings_backup", None)
        if not settings_bk:
            await interaction.followup.send("Settings-Backup nicht verfuegbar.")
            return

        success, msg, filepath = await settings_bk.save_settings(name=name)
        if success:
            embed = discord.Embed(
                title="Settings gesichert",
                description=msg,
                color=0x2ecc71,
            )
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send(f"Fehler: {msg}")

    @config_grp.command(
        name="settings_restore", description="Server-Einstellungen wiederherstellen"
    )
    @app_commands.describe(filename="Name der Backup-Datei")
    @owner_only()
    @server_online_required("server")
    async def config_settings_restore(
        self, interaction: discord.Interaction, filename: str
    ):
        await interaction.response.defer()

        settings_bk = getattr(self.bot, "settings_backup", None)
        if not settings_bk:
            await interaction.followup.send("Settings-Backup nicht verfuegbar.")
            return

        success, msg = await settings_bk.restore_settings(filename)
        embed = discord.Embed(
            title="Settings Restore" if success else "Restore fehlgeschlagen",
            description=msg,
            color=0x2ecc71 if success else 0xe74c3c,
        )
        embed.set_footer(text=f"von {interaction.user.display_name}")
        await interaction.followup.send(embed=embed)

    @config_settings_restore.autocomplete("filename")
    async def settings_restore_autocomplete(
        self, interaction: discord.Interaction, current: str
    ):
        settings_bk = getattr(self.bot, "settings_backup", None)
        if not settings_bk:
            return []
        backups = settings_bk.list_backups()
        return [
            app_commands.Choice(
                name=f"{b['filename']} ({b['session']})",
                value=b["filename"],
            )
            for b in backups
            if current.lower() in b["filename"].lower()
        ][:25]

    @config_grp.command(
        name="stats", description="Savegame-Statistiken anzeigen"
    )
    @spieler_only()
    async def config_stats(self, interaction: discord.Interaction):
        await interaction.response.defer()

        try:
            stats_mgr = self.bot.savegame_stats
            stats = await stats_mgr.analyze()

            if stats.get("error"):
                await interaction.followup.send(stats["error"])
                return

            embed = discord.Embed(
                title=f"Savegame: {stats.get('name', 'Unbekannt')}",
                color=0x3498db,
            )
            embed.add_field(
                name="Groesse", value=stats.get("size", "?"), inline=True
            )
            embed.add_field(
                name="Letzte Aenderung",
                value=stats.get("last_modified", "?"),
                inline=True,
            )

            if stats.get("session_name"):
                embed.add_field(
                    name="Session", value=stats["session_name"], inline=True
                )
            if stats.get("play_hours"):
                embed.add_field(
                    name="Spielzeit",
                    value=f"{stats['play_hours']}h",
                    inline=True,
                )
            if stats.get("build_version"):
                embed.add_field(
                    name="Build",
                    value=str(stats["build_version"]),
                    inline=True,
                )
            if stats.get("save_date"):
                embed.add_field(
                    name="Save-Datum",
                    value=stats["save_date"],
                    inline=True,
                )

            save_count = stats_mgr.get_save_count()
            total_size = stats_mgr.get_total_size()
            embed.add_field(
                name="Savegames gesamt",
                value=f"{save_count} Dateien ({format_bytes(total_size)})",
                inline=False,
            )

            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"Analyse fehlgeschlagen: {e}")

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  BLUEPRINTS: /sat blueprints upload|list|download|delete     ║
    # ╚════════════════════════════════════════════════════════════════╝

    @blueprints_grp.command(
        name="upload",
        description="Blueprint(s) hochladen (.sbp+.sbpcfg oder .zip)",
    )
    @app_commands.describe(
        kategorie="Kategorie fuer den Blueprint",
        datei1="Blueprint-Datei (.sbp, .sbpcfg, oder .zip)",
        datei2="Zweite Datei (z.B. .sbpcfg wenn datei1 .sbp ist)",
    )
    @spieler_only()
    async def blueprints_upload(
        self,
        interaction: discord.Interaction,
        kategorie: str,
        datei1: discord.Attachment,
        datei2: discord.Attachment = None,
    ):
        await interaction.response.defer()

        from modules.satisfactory.blueprint_manager import CATEGORIES

        if kategorie not in CATEGORIES:
            await interaction.followup.send(
                f"Ungueltige Kategorie. Verfuegbar: {', '.join(CATEGORIES)}"
            )
            return

        attachments = [datei1]
        if datei2:
            attachments.append(datei2)

        filenames = [a.filename for a in attachments]

        # ZIP upload
        zip_files = [a for a in attachments if a.filename.endswith(".zip")]
        if zip_files:
            zip_att = zip_files[0]
            if zip_att.size > 50 * 1024 * 1024:
                await interaction.followup.send("ZIP zu gross (max. 50 MB)!")
                return

            zip_data = await zip_att.read()
            count, added, errors = await self.bot.blueprint_mgr.add_from_zip(
                zip_data,
                kategorie,
                interaction.user.id,
                interaction.user.display_name,
            )

            if errors:
                await interaction.followup.send(
                    f"Fehler beim ZIP-Upload:\n" + "\n".join(errors)
                )
                return

            embed = discord.Embed(
                title=f"{count} Blueprint(s) hochgeladen",
                description="\n".join(f"\u2022 {n}" for n in added),
                color=0x2ecc71,
            )
            embed.add_field(name="Kategorie", value=kategorie, inline=True)
            embed.set_footer(text=f"von {interaction.user.display_name}")

            view = BlueprintRestartView(self, interaction)
            await interaction.followup.send(embed=embed, view=view)
            return

        # Single blueprint upload
        valid, error, pairs = self.bot.blueprint_mgr.validate_files(filenames)
        if not valid:
            await interaction.followup.send(
                f"Validierung fehlgeschlagen:\n{error}\n\n"
                f"Du brauchst **beide** Dateien: `.sbp` + `.sbpcfg` (gleicher Name)\n"
                f"Oder eine `.zip` Datei mit mehreren Blueprints."
            )
            return

        added_names = []
        for sbp_name, cfg_name in pairs:
            name = sbp_name[:-4]
            sbp_att = next(a for a in attachments if a.filename == sbp_name)
            cfg_att = next(a for a in attachments if a.filename == cfg_name)
            sbp_data = await sbp_att.read()
            cfg_data = await cfg_att.read()

            success, msg = await self.bot.blueprint_mgr.add_blueprint(
                name,
                kategorie,
                interaction.user.id,
                interaction.user.display_name,
                sbp_data,
                cfg_data,
            )

            if success:
                added_names.append(name)
            else:
                await interaction.followup.send(msg)
                return

        embed = discord.Embed(
            title="Blueprint hochgeladen",
            description="\n".join(f"\u2022 **{n}**" for n in added_names),
            color=0x2ecc71,
        )
        embed.add_field(name="Kategorie", value=kategorie, inline=True)
        embed.set_footer(text=f"von {interaction.user.display_name}")

        view = BlueprintRestartView(self, interaction)
        await interaction.followup.send(embed=embed, view=view)

    @blueprints_upload.autocomplete("kategorie")
    async def kategorie_autocomplete(
        self, interaction: discord.Interaction, current: str
    ):
        from modules.satisfactory.blueprint_manager import CATEGORIES

        return [
            app_commands.Choice(name=cat, value=cat)
            for cat in CATEGORIES
            if current.lower() in cat.lower()
        ][:25]

    @blueprints_grp.command(
        name="list", description="Blueprints anzeigen"
    )
    @app_commands.describe(kategorie="Optional nach Kategorie filtern")
    @spieler_only()
    async def blueprints_list(
        self,
        interaction: discord.Interaction,
        kategorie: str = None,
    ):
        await interaction.response.defer()

        blueprints = self.bot.blueprint_mgr.get_list(category=kategorie)

        if not blueprints:
            msg = "Keine Blueprints vorhanden."
            if kategorie:
                msg = f"Keine Blueprints in Kategorie '{kategorie}'."
            await interaction.followup.send(msg)
            return

        embed = discord.Embed(
            title=f"Blueprints ({len(blueprints)})", color=0x3498db
        )
        if kategorie:
            embed.title += f" \u2014 {kategorie}"

        entries = []
        for i, bp in enumerate(blueprints[:20], 1):
            size = format_bytes(bp.get("size_bytes", 0))
            date = bp.get("uploaded_at", "?")[:10]
            entries.append(
                f"`{i}.` **{bp['name']}** [{bp['category']}]\n"
                f"   {size} | {date} | von {bp['uploader_name']}"
            )

        embed.description = "\n".join(entries)
        embed.set_footer(
            text=f"Gesamt: {self.bot.blueprint_mgr.count()} Blueprints"
        )
        await interaction.followup.send(embed=embed)

    @blueprints_grp.command(
        name="download", description="Blueprint herunterladen"
    )
    @app_commands.describe(name="Name des Blueprints")
    @spieler_only()
    async def blueprints_download(
        self, interaction: discord.Interaction, name: str
    ):
        await interaction.response.defer()

        bp = self.bot.blueprint_mgr.get_blueprint(name)
        if not bp:
            await interaction.followup.send(
                f"Blueprint '{name}' nicht gefunden!"
            )
            return

        sbp_path, cfg_path = self.bot.blueprint_mgr.get_files(name)
        if not sbp_path or not cfg_path:
            await interaction.followup.send(
                "Blueprint-Dateien nicht gefunden!"
            )
            return

        try:
            files = [
                discord.File(sbp_path, filename=sbp_path.name),
                discord.File(cfg_path, filename=cfg_path.name),
            ]
            embed = discord.Embed(
                title=f"Blueprint: {name}", color=0x3498db
            )
            embed.add_field(
                name="Kategorie", value=bp["category"], inline=True
            )
            embed.add_field(
                name="Uploader", value=bp["uploader_name"], inline=True
            )
            embed.add_field(
                name="Datum", value=bp["uploaded_at"][:10], inline=True
            )
            embed.set_footer(
                text="Beide Dateien (.sbp + .sbpcfg) in den Blueprint-Ordner kopieren"
            )
            await interaction.followup.send(embed=embed, files=files)
            logger.info(
                f"Blueprint downloaded: {name} by {interaction.user}"
            )
        except Exception as e:
            await interaction.followup.send(f"Download fehlgeschlagen: {e}")

    @blueprints_download.autocomplete("name")
    async def bp_download_autocomplete(
        self, interaction: discord.Interaction, current: str
    ):
        blueprints = self.bot.blueprint_mgr.get_list()
        return [
            app_commands.Choice(
                name=f"{bp['name']} ({bp['category']})"[:100],
                value=bp["name"],
            )
            for bp in blueprints
            if current.lower() in bp["name"].lower()
        ][:25]

    @blueprints_grp.command(
        name="delete", description="Blueprint loeschen"
    )
    @app_commands.describe(name="Name des Blueprints")
    @spieler_only()
    async def blueprints_delete(
        self, interaction: discord.Interaction, name: str
    ):
        bp = self.bot.blueprint_mgr.get_blueprint(name)
        if not bp:
            await interaction.response.send_message(
                f"Blueprint '{name}' nicht gefunden!", ephemeral=True
            )
            return

        user_is_admin = is_admin(interaction)
        success, msg = await self.bot.blueprint_mgr.delete(
            name, interaction.user.id, is_admin=user_is_admin
        )

        if success:
            embed = discord.Embed(
                title="Blueprint geloescht",
                description=f"**{name}** wurde geloescht.",
                color=0xe74c3c,
            )
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.response.send_message(embed=embed)
            logger.info(
                f"Blueprint deleted: {name} by {interaction.user}"
            )
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    @blueprints_delete.autocomplete("name")
    async def bp_delete_autocomplete(
        self, interaction: discord.Interaction, current: str
    ):
        blueprints = self.bot.blueprint_mgr.get_list()
        user_is_admin = is_admin(interaction)
        filtered = []
        for bp in blueprints:
            if user_is_admin or bp["uploader_id"] == interaction.user.id:
                if current.lower() in bp["name"].lower():
                    filtered.append(
                        app_commands.Choice(
                            name=f"{bp['name']} ({bp['category']})"[:100],
                            value=bp["name"],
                        )
                    )
        return filtered[:25]

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  WHITELIST: /sat whitelist add|remove|list                   ║
    # ╚════════════════════════════════════════════════════════════════╝

    @whitelist_grp.command(
        name="add", description="Spieler zur Whitelist hinzufuegen"
    )
    @app_commands.describe(player="Name des Spielers")
    @admin_only()
    async def whitelist_add(
        self, interaction: discord.Interaction, player: str
    ):
        added = await self.bot.whitelist_mgr.add(
            player, interaction.user.display_name
        )
        if added:
            await interaction.response.send_message(
                f"**{player}** zur Whitelist hinzugefuegt."
            )
        else:
            await interaction.response.send_message(
                f"**{player}** ist bereits auf der Whitelist.",
                ephemeral=True,
            )

    @whitelist_grp.command(
        name="remove", description="Spieler von Whitelist entfernen"
    )
    @app_commands.describe(player="Name des Spielers")
    @admin_only()
    async def whitelist_remove(
        self, interaction: discord.Interaction, player: str
    ):
        removed = await self.bot.whitelist_mgr.remove(player)
        if removed:
            await interaction.response.send_message(
                f"**{player}** von der Whitelist entfernt."
            )
        else:
            await interaction.response.send_message(
                f"**{player}** ist nicht auf der Whitelist.",
                ephemeral=True,
            )

    @whitelist_grp.command(
        name="list", description="Whitelist anzeigen"
    )
    @spieler_only()
    async def whitelist_list(self, interaction: discord.Interaction):
        players = self.bot.whitelist_mgr.get_list()
        embed = discord.Embed(
            title=f"Whitelist ({len(players)} Spieler)", color=0x2ecc71
        )
        embed.add_field(
            name="Status",
            value="Aktiviert" if self.bot.whitelist_mgr.enabled else "Deaktiviert",
            inline=False,
        )

        if players:
            entries = []
            for p in players[:25]:
                entries.append(f"\u2022 **{p['name']}** \u2014 von {p['added_by']}")
            embed.description = "\n".join(entries)
        else:
            embed.description = "Whitelist ist leer."

        await interaction.response.send_message(embed=embed)

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  BLACKLIST: /sat blacklist add|remove|list                   ║
    # ╚════════════════════════════════════════════════════════════════╝

    @blacklist_grp.command(
        name="add",
        description="Spieler bannen / zur Blacklist hinzufuegen",
    )
    @app_commands.describe(
        player="Name des Spielers", reason="Grund fuer den Ban"
    )
    @admin_only()
    async def blacklist_add(
        self,
        interaction: discord.Interaction,
        player: str,
        reason: str = "Kein Grund angegeben",
    ):
        added = await self.bot.blacklist_mgr.add(
            player, reason, interaction.user.display_name
        )
        if added:
            embed = discord.Embed(
                title="Spieler zur Blacklist hinzugefuegt", color=0xe74c3c
            )
            embed.add_field(name="Spieler", value=player, inline=True)
            embed.add_field(name="Grund", value=reason, inline=True)
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message(
                f"**{player}** ist bereits auf der Blacklist.",
                ephemeral=True,
            )

    @blacklist_grp.command(
        name="remove",
        description="Spieler von Blacklist entfernen (entbannen)",
    )
    @app_commands.describe(player="Name des Spielers")
    @admin_only()
    async def blacklist_remove(
        self, interaction: discord.Interaction, player: str
    ):
        removed = await self.bot.blacklist_mgr.remove(player)
        if removed:
            await interaction.response.send_message(
                f"**{player}** von der Blacklist entfernt."
            )
        else:
            await interaction.response.send_message(
                f"**{player}** ist nicht auf der Blacklist.",
                ephemeral=True,
            )

    @blacklist_grp.command(
        name="list", description="Blacklist anzeigen"
    )
    @admin_only()
    async def blacklist_list(self, interaction: discord.Interaction):
        players = self.bot.blacklist_mgr.get_list()
        embed = discord.Embed(
            title=f"Blacklist ({len(players)} Spieler)", color=0xe74c3c
        )

        if players:
            entries = []
            for p in players[:25]:
                entries.append(
                    f"\u2022 **{p['name']}** \u2014 {p.get('reason', 'N/A')} "
                    f"(von {p['banned_by']})"
                )
            embed.description = "\n".join(entries)
        else:
            embed.description = "Blacklist ist leer."

        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------------
    # Error handler
    # ------------------------------------------------------------------

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


# ══════════════════════════════════════════════════════════════════════
# UI Views
# ══════════════════════════════════════════════════════════════════════


class RestartModeView(discord.ui.View):
    """Buttons to choose restart mode: immediate, 5min, or 10min countdown"""

    def __init__(self, cog, user_id: int):
        super().__init__(timeout=30)
        self.cog = cog
        self.user_id = user_id

    async def _check_user(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Nur der Befehlsgeber kann dies ausfuehren.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Sofort", style=discord.ButtonStyle.danger, emoji="⚡")
    async def restart_now(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        self.stop()
        await interaction.response.edit_message(
            content="⚡ **Sofortiger Neustart** wird ausgefuehrt...", view=None
        )
        await self.cog._do_restart_immediate(interaction.channel, interaction.user)

    @discord.ui.button(label="5 Minuten", style=discord.ButtonStyle.primary, emoji="🕐")
    async def restart_5min(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        self.stop()
        await interaction.response.edit_message(
            content="🕐 **Neustart in 5 Minuten** gestartet.", view=None
        )
        await self.cog._do_restart_countdown(interaction.channel, interaction.user, 5)

    @discord.ui.button(label="10 Minuten", style=discord.ButtonStyle.secondary, emoji="🕙")
    async def restart_10min(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        self.stop()
        await interaction.response.edit_message(
            content="🕙 **Neustart in 10 Minuten** gestartet.", view=None
        )
        await self.cog._do_restart_countdown(interaction.channel, interaction.user, 10)

    @discord.ui.button(label="Abbrechen", style=discord.ButtonStyle.secondary, emoji="❌")
    async def cancel_restart(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        self.stop()
        await interaction.response.edit_message(
            content="❌ Neustart abgebrochen.", view=None
        )

    async def on_timeout(self):
        """Remove buttons after timeout"""
        pass


class RestoreConfirmView(discord.ui.View):
    """Confirmation buttons for backup restore"""

    def __init__(self, cog, interaction, backup_name):
        super().__init__(timeout=60)
        self.cog = cog
        self.original_interaction = interaction
        self.backup_name = backup_name

    @discord.ui.button(
        label="Ja, wiederherstellen",
        style=discord.ButtonStyle.danger,
        emoji="\u26a0\ufe0f",
    )
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id != self.original_interaction.user.id:
            await interaction.response.send_message(
                "Nur der Ersteller kann bestaetigen.", ephemeral=True
            )
            return

        await interaction.response.edit_message(
            content="Restore wird durchgefuehrt...", embed=None, view=None
        )

        success, msg = await self.cog.bot.backup_mgr.restore(self.backup_name)

        if success:
            embed = discord.Embed(
                title="Restore erfolgreich",
                description=msg,
                color=0x2ecc71,
            )
            embed.set_footer(text="Starte den Server mit /sat start")
            await interaction.edit_original_response(
                content=None, embed=embed
            )
        else:
            await interaction.edit_original_response(
                content=f"Restore fehlgeschlagen: {msg}"
            )

    @discord.ui.button(
        label="Abbrechen", style=discord.ButtonStyle.secondary
    )
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.edit_message(
            content="Restore abgebrochen.", embed=None, view=None
        )


class LoadConfirmView(discord.ui.View):
    """Confirmation for loading a savegame"""

    def __init__(self, cog, interaction, savename):
        super().__init__(timeout=30)
        self.cog = cog
        self.original_interaction = interaction
        self.savename = savename

    @discord.ui.button(
        label="Ja, laden", style=discord.ButtonStyle.danger
    )
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id != self.original_interaction.user.id:
            await interaction.response.send_message(
                "Nur der Ersteller kann bestaetigen.", ephemeral=True
            )
            return

        await interaction.response.edit_message(
            content=f"Lade Savegame '{self.savename}'...",
            embed=None,
            view=None,
        )

        try:
            await self.cog.api.save_game()
            import re as _re
            safe_name = _re.sub(r'[^\w\-]', '', self.savename)
            result = await self.cog.api.run_command(
                f"LoadGame {safe_name}"
            )

            embed = discord.Embed(
                title="Savegame wird geladen",
                description=(
                    f"**{self.savename}** wird geladen.\n"
                    f"Spieler werden kurzzeitig getrennt."
                ),
                color=0x2ecc71,
            )
            if result:
                embed.add_field(
                    name="Server-Antwort",
                    value=result[:500],
                    inline=False,
                )
            await interaction.edit_original_response(
                content=None, embed=embed
            )
        except Exception as e:
            await interaction.edit_original_response(content=f"Fehler: {e}")

    @discord.ui.button(
        label="Abbrechen", style=discord.ButtonStyle.secondary
    )
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.edit_message(
            content="Laden abgebrochen.", embed=None, view=None
        )


class BlueprintRestartView(discord.ui.View):
    """Ask if server should be restarted after blueprint upload"""

    def __init__(self, cog, interaction):
        super().__init__(timeout=120)
        self.cog = cog
        self.original_user = interaction.user.id

    @discord.ui.button(
        label="Server neustarten (5 Min)",
        style=discord.ButtonStyle.primary,
        emoji="\ud83d\udd04",
    )
    async def restart(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id != self.original_user:
            await interaction.response.send_message(
                "Nur der Uploader kann den Restart ausloesen.",
                ephemeral=True,
            )
            return

        if not is_admin(interaction):
            await interaction.response.send_message(
                "Nur Admins koennen den Server neustarten.",
                ephemeral=True,
            )
            return

        if self.cog.bot.timer_mgr.has_active:
            await interaction.response.send_message(
                "Es laeuft bereits ein Timer. Nutze `/sat cancel` zuerst.",
                ephemeral=True,
            )
            return

        await interaction.response.edit_message(view=None)

        timer = self.cog.bot.timer_mgr.get_or_create(
            "satisfactory",
            api=self.cog.api,
            channel=interaction.channel,
        )
        result = await timer.countdown(
            duration_minutes=5,
            action_name="Neustart (Blueprint-Upload)",
            warnings=[5, 3, 1],
        )

        if result == TimerResult.COMPLETED:
            success, msg = await self.cog.server.restart()
            if success:
                embed = discord.Embed(
                    title="Server neugestartet",
                    description="Blueprints sind jetzt verfuegbar!",
                    color=0x2ecc71,
                )
                await interaction.channel.send(embed=embed)
            else:
                await interaction.channel.send(
                    f"Restart fehlgeschlagen: {msg}"
                )
        elif result == TimerResult.CANCELLED:
            pass
        else:
            await interaction.channel.send(
                "Timer-Fehler. Bitte manuell neustarten."
            )

    @discord.ui.button(
        label="Nein, spaeter", style=discord.ButtonStyle.secondary
    )
    async def skip(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.edit_message(view=None)
        await interaction.followup.send(
            "Blueprints sind nach dem naechsten Server-Restart verfuegbar.",
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(SatisfactoryCog(bot))
