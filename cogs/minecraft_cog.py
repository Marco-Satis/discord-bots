"""
Minecraft Unified Cog - Alle /mc Befehle mit Multi-Server-Support

Command-Struktur:
  /mc status [server]                        (Server-Status - Alle)
  /mc start|stop|restart|cancel [server]     (Server-Steuerung - Admin)
  /mc players list|kick|ban [server]         (Spieler-Verwaltung - Spieler/Admin)
  /mc backup create|list|restore|download    (Backup-Verwaltung - Spieler/Owner)
  /mc whitelist add|remove|list [server]     (Whitelist - Admin)
  /mc config settings|set|backup|restore     (Server-Konfiguration)
  /mc config update|stats                    (Update-Pruefer/Statistiken)
  /mc command <cmd> [server]                 (RCON ausfuehren - Owner)
  /mc say|difficulty|weather|time|gamemode   (Admin-Befehle)

Server-Auswahl: Autocomplete zeigt nur aktivierte Server an.
Bei nur einem Server wird dieser automatisch gewaehlt.
"""

import asyncio
import shutil
import tempfile
import zipfile
from pathlib import Path
import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional, Dict

from utils import get_logger, format_uptime, format_bytes, status_emoji
from utils.permissions import admin_only, spieler_only, owner_only
from modules.restart_timer import RestartTimer, TimerResult
from modules.minecraft.server import MinecraftServer
from modules.minecraft.backup import MinecraftBackupManager
from modules.minecraft.settings_backup import MinecraftSettingsBackup
from modules.minecraft.update_checker import MinecraftUpdateChecker

logger = get_logger("cogs.minecraft")


class MinecraftCog(commands.Cog):
    """Alle Minecraft-Server-Befehle unter /mc (Multi-Server)"""

    # Erlaubte Keys fuer /mc config set (Sicherheits-Whitelist)
    ALLOWED_CONFIG_KEYS = {
        "difficulty", "pvp", "max-players", "view-distance",
        "simulation-distance", "motd", "white-list", "spawn-protection",
        "gamemode", "hardcore", "enable-command-block", "max-world-size",
        "player-idle-timeout", "allow-flight", "level-name",
        "spawn-npcs", "spawn-animals", "spawn-monsters",
    }

    # ==================================================================
    # Group & Sub-Group Definitionen
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
    config_grp = app_commands.Group(
        name="config", parent=mc, description="Server-Konfiguration"
    )

    # ==================================================================
    # Init
    # ==================================================================

    def __init__(self, bot):
        self.bot = bot
        self.servers: Dict[str, MinecraftServer] = bot.mc_servers
        self.backup_mgrs: Dict[str, MinecraftBackupManager] = getattr(
            bot, 'mc_backup_mgrs', {}
        )
        self.settings_mgrs: Dict[str, MinecraftSettingsBackup] = getattr(
            bot, 'mc_settings_mgrs', {}
        )
        self.update_checkers: Dict[str, MinecraftUpdateChecker] = getattr(
            bot, 'mc_update_checkers', {}
        )
        self.timer_mgr = bot.timer_mgr

    # ==================================================================
    # Hilfsfunktionen
    # ==================================================================

    async def _server_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete: Zeigt nur aktivierte MC-Server"""
        return [
            app_commands.Choice(name=srv.display_name, value=sid)
            for sid, srv in self.servers.items()
            if current.lower() in sid.lower()
            or current.lower() in srv.display_name.lower()
        ]

    def _resolve_server(self, server_id: Optional[str]) -> Optional[MinecraftServer]:
        """Server-Instanz anhand ID ermitteln. Bei None: Standard-Server."""
        if not self.servers:
            return None

        if server_id:
            return self.servers.get(server_id.upper())

        # Bei einem einzigen Server automatisch waehlen
        if len(self.servers) == 1:
            return next(iter(self.servers.values()))

        return None

    def _resolve_backup_mgr(self, server_id: Optional[str]) -> Optional[MinecraftBackupManager]:
        """Backup-Manager fuer Server ermitteln"""
        if not self.backup_mgrs:
            return None

        if server_id:
            return self.backup_mgrs.get(server_id.upper())

        if len(self.backup_mgrs) == 1:
            return next(iter(self.backup_mgrs.values()))

        return None

    def _resolve_settings_mgr(self, server_id: Optional[str]) -> Optional[MinecraftSettingsBackup]:
        """Settings-Backup-Manager fuer Server ermitteln"""
        if not self.settings_mgrs:
            return None
        if server_id:
            return self.settings_mgrs.get(server_id.upper())
        if len(self.settings_mgrs) == 1:
            return next(iter(self.settings_mgrs.values()))
        return None

    def _resolve_update_checker(self, server_id: Optional[str]) -> Optional[MinecraftUpdateChecker]:
        """Update-Checker fuer Server ermitteln"""
        if not self.update_checkers:
            return None
        if server_id:
            return self.update_checkers.get(server_id.upper())
        if len(self.update_checkers) == 1:
            return next(iter(self.update_checkers.values()))
        return None

    async def _require_server(
        self, interaction: discord.Interaction, server_id: Optional[str]
    ) -> Optional[MinecraftServer]:
        """Server ermitteln oder Fehlermeldung senden. Gibt None zurueck bei Fehler."""
        server = self._resolve_server(server_id)
        if not server:
            if not self.servers:
                await interaction.followup.send(
                    "Kein Minecraft-Server konfiguriert.", ephemeral=True
                )
            elif not server_id:
                namen = ", ".join(
                    f"`{sid}` ({srv.display_name})"
                    for sid, srv in self.servers.items()
                )
                await interaction.followup.send(
                    f"Mehrere Server verfuegbar. Bitte Server angeben: {namen}",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    f"Server `{server_id}` nicht gefunden.", ephemeral=True
                )
        return server

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  CORE: /mc status | start | stop | restart | cancel           ║
    # ╚════════════════════════════════════════════════════════════════╝

    @mc.command(name="status", description="Server-Status anzeigen")
    @app_commands.autocomplete(server=_server_autocomplete)
    async def mc_status(self, interaction: discord.Interaction,
                        server: Optional[str] = None):
        await interaction.response.defer()

        srv = await self._require_server(interaction, server)
        if not srv:
            return

        status = await srv.get_status()
        online = status["running"]

        embed = discord.Embed(
            title=f"{status_emoji(online)} {srv.display_name}",
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
                name="PID",
                value=str(status.get("pid", "—")),
                inline=True,
            )
        else:
            embed.description = "Server ist offline."

        # Aktiver Timer?
        timer_key = f"mc_{srv.server_id.lower()}"
        timer = self.timer_mgr._timers.get(timer_key)
        if timer and timer.is_active:
            embed.add_field(
                name="\u23f0 Geplant",
                value=f"{timer.action_name} laeuft...",
                inline=False,
            )

        await interaction.followup.send(embed=embed)

    @mc.command(name="start", description="Server starten")
    @admin_only()
    @app_commands.autocomplete(server=_server_autocomplete)
    async def mc_start(self, interaction: discord.Interaction,
                       server: Optional[str] = None):
        await interaction.response.defer()

        srv = await self._require_server(interaction, server)
        if not srv:
            return

        if await srv.is_running():
            await interaction.followup.send(
                f"{srv.display_name} laeuft bereits!"
            )
            return

        await interaction.followup.send(
            f"{srv.display_name} wird gestartet..."
        )
        success, msg = await srv.start()

        if success:
            embed = discord.Embed(
                title=f"{status_emoji(True)} {srv.display_name} gestartet",
                description=msg,
                color=0x00ff00,
            )
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.edit_original_response(content=None, embed=embed)
            logger.info(f"[{srv.server_id}] Server gestartet von {interaction.user}")
        else:
            embed = discord.Embed(
                title="Start fehlgeschlagen",
                description=msg,
                color=0xff0000,
            )
            await interaction.edit_original_response(content=None, embed=embed)

    @mc.command(name="stop", description="Server mit Countdown stoppen")
    @admin_only()
    @app_commands.autocomplete(server=_server_autocomplete)
    async def mc_stop(self, interaction: discord.Interaction,
                      countdown: int = 10,
                      server: Optional[str] = None):
        await interaction.response.defer()

        srv = await self._require_server(interaction, server)
        if not srv:
            return

        if not await srv.is_running():
            await interaction.followup.send(
                f"{srv.display_name} ist nicht gestartet."
            )
            return

        # Timer fuer diesen Server pruefen (nur eigenen Timer-Key)
        timer_key = f"mc_{srv.server_id.lower()}"
        existing_timer = self.timer_mgr._timers.get(timer_key)
        if existing_timer and existing_timer.is_active:
            await interaction.followup.send(
                f"Ein Timer fuer {srv.display_name} laeuft bereits. "
                "Nutze `/mc cancel` zum Abbrechen."
            )
            return

        await interaction.followup.send(
            f"{srv.display_name} wird in {countdown} Minute(n) gestoppt!"
        )

        # Timer mit Server als API (fuer In-Game-Nachrichten via run_command)
        timer = self.timer_mgr.get_or_create(
            timer_key, api=srv, channel=interaction.channel
        )

        async def perform_stop():
            success, msg = await srv.stop()
            embed = discord.Embed(
                title=(f"{status_emoji(False)} {srv.display_name} gestoppt"
                       if success else "Stop fehlgeschlagen"),
                description=msg,
                color=0xff0000 if success else 0xff9900,
            )
            try:
                await interaction.followup.send(embed=embed)
            except Exception:
                pass

        # B1-Fix: on_complete fuehrt perform_stop aus, KEIN zweiter Aufruf danach
        result = await timer.countdown(
            duration_minutes=countdown,
            action_name="Server Stop",
            warnings=[10, 5, 3, 1],
            on_complete=perform_stop
        )

        if result == TimerResult.CANCELLED:
            try:
                await interaction.followup.send(
                    f"{srv.display_name} Stop abgebrochen!"
                )
            except Exception:
                pass

    @mc.command(name="restart", description="Server mit Countdown neustarten")
    @admin_only()
    @app_commands.autocomplete(server=_server_autocomplete)
    async def mc_restart(self, interaction: discord.Interaction,
                         countdown: int = 10,
                         server: Optional[str] = None):
        await interaction.response.defer()

        srv = await self._require_server(interaction, server)
        if not srv:
            return

        if not await srv.is_running():
            await interaction.followup.send(
                f"{srv.display_name} ist nicht gestartet."
            )
            return

        timer_key = f"mc_{srv.server_id.lower()}"
        existing_timer = self.timer_mgr._timers.get(timer_key)
        if existing_timer and existing_timer.is_active:
            await interaction.followup.send(
                f"Ein Timer fuer {srv.display_name} laeuft bereits. "
                "Nutze `/mc cancel` zum Abbrechen."
            )
            return

        await interaction.followup.send(
            f"{srv.display_name} wird in {countdown} Minute(n) neugestartet!"
        )

        timer = self.timer_mgr.get_or_create(
            timer_key, api=srv, channel=interaction.channel
        )

        async def perform_restart():
            success, msg = await srv.restart()
            embed = discord.Embed(
                title=(f"{status_emoji(True)} {srv.display_name} neugestartet"
                       if success else "Restart fehlgeschlagen"),
                description=msg,
                color=0x00ff00 if success else 0xff9900,
            )
            try:
                await interaction.followup.send(embed=embed)
            except Exception:
                pass

        # B1-Fix: on_complete fuehrt perform_restart aus, KEIN zweiter Aufruf
        result = await timer.countdown(
            duration_minutes=countdown,
            action_name="Server Restart",
            warnings=[10, 5, 3, 1],
            on_complete=perform_restart
        )

        if result == TimerResult.CANCELLED:
            try:
                await interaction.followup.send(
                    f"{srv.display_name} Restart abgebrochen!"
                )
            except Exception:
                pass

    @mc.command(name="cancel", description="Aktiven Timer abbrechen")
    @admin_only()
    async def mc_cancel(self, interaction: discord.Interaction):
        active = self.timer_mgr.get_active()
        if not active:
            await interaction.response.send_message(
                "Kein aktiver Timer.", ephemeral=True
            )
            return

        active.cancel()
        await interaction.response.send_message(
            f"Timer abgebrochen: {active.action_name}", ephemeral=True
        )

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  PLAYERS: /mc players list | kick | ban | pardon               ║
    # ╚════════════════════════════════════════════════════════════════╝

    @players_grp.command(name="list", description="Online Spieler anzeigen")
    @app_commands.autocomplete(server=_server_autocomplete)
    async def mc_players_list(self, interaction: discord.Interaction,
                              server: Optional[str] = None):
        await interaction.response.defer()

        srv = await self._require_server(interaction, server)
        if not srv:
            return

        if not await srv.is_running():
            await interaction.followup.send(
                f"{srv.display_name} ist offline."
            )
            return

        try:
            players = await srv.get_players()
            online, max_players = await srv.get_player_count()

            embed = discord.Embed(
                title=f"Online Spieler — {srv.display_name}",
                color=0x0099ff,
            )
            embed.add_field(
                name="Spieleranzahl",
                value=f"{online}/{max_players}",
                inline=True,
            )
            embed.add_field(
                name="Spieler",
                value=("\n".join(players) if players else "Keine Spieler online"),
                inline=False,
            )

            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(
                f"Fehler beim Abrufen der Spielerliste: {e}",
                ephemeral=True
            )
            logger.error(f"[{srv.server_id}] Spielerliste fehlgeschlagen: {e}")

    @players_grp.command(name="kick", description="Spieler kicken")
    @admin_only()
    @app_commands.autocomplete(server=_server_autocomplete)
    async def mc_kick(self, interaction: discord.Interaction,
                      player: str,
                      reason: str = "Gekickt vom Admin",
                      server: Optional[str] = None):
        await interaction.response.defer()

        srv = await self._require_server(interaction, server)
        if not srv:
            return

        if not await srv.is_running():
            await interaction.followup.send(f"{srv.display_name} ist offline.")
            return

        try:
            await srv.rcon_command(f"kick {player} {reason}")
            embed = discord.Embed(
                title="Spieler gekickt",
                description=f"**{player}** wurde von {srv.display_name} gekickt.",
                color=0xff9900,
            )
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.followup.send(embed=embed)
            logger.info(f"[{srv.server_id}] {player} gekickt von {interaction.user}")
        except Exception as e:
            await interaction.followup.send(
                f"Fehler beim Kicken: {e}", ephemeral=True
            )

    @players_grp.command(name="ban", description="Spieler bannen")
    @admin_only()
    @app_commands.autocomplete(server=_server_autocomplete)
    async def mc_ban(self, interaction: discord.Interaction,
                     player: str,
                     reason: str = "Gebannt vom Admin",
                     server: Optional[str] = None):
        await interaction.response.defer()

        srv = await self._require_server(interaction, server)
        if not srv:
            return

        if not await srv.is_running():
            await interaction.followup.send(f"{srv.display_name} ist offline.")
            return

        try:
            await srv.rcon_command(f"ban {player} {reason}")
            embed = discord.Embed(
                title="Spieler gebannt",
                description=f"**{player}** wurde auf {srv.display_name} gebannt.\n"
                           f"Grund: {reason}",
                color=0xff0000,
            )
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.followup.send(embed=embed)
            logger.info(f"[{srv.server_id}] {player} gebannt von {interaction.user}")
        except Exception as e:
            await interaction.followup.send(
                f"Fehler beim Bannen: {e}", ephemeral=True
            )

    @players_grp.command(name="pardon", description="Spieler entbannen")
    @admin_only()
    @app_commands.autocomplete(server=_server_autocomplete)
    async def mc_pardon(self, interaction: discord.Interaction,
                        player: str,
                        server: Optional[str] = None):
        await interaction.response.defer()

        srv = await self._require_server(interaction, server)
        if not srv:
            return

        if not await srv.is_running():
            await interaction.followup.send(f"{srv.display_name} ist offline.")
            return

        try:
            await srv.rcon_command(f"pardon {player}")
            embed = discord.Embed(
                title="Spieler entbannt",
                description=f"**{player}** wurde auf {srv.display_name} entbannt.",
                color=0x00ff00,
            )
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.followup.send(embed=embed)
            logger.info(f"[{srv.server_id}] {player} entbannt von {interaction.user}")
        except Exception as e:
            await interaction.followup.send(
                f"Fehler beim Entbannen: {e}", ephemeral=True
            )

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  WHITELIST: /mc whitelist add | remove | list                  ║
    # ╚════════════════════════════════════════════════════════════════╝

    @whitelist_grp.command(name="add", description="Spieler zur Whitelist hinzufuegen")
    @admin_only()
    @app_commands.autocomplete(server=_server_autocomplete)
    async def mc_whitelist_add(self, interaction: discord.Interaction,
                               player: str,
                               server: Optional[str] = None):
        await interaction.response.defer()

        srv = await self._require_server(interaction, server)
        if not srv:
            return

        if not await srv.is_running():
            await interaction.followup.send(f"{srv.display_name} ist offline.")
            return

        try:
            response = await srv.rcon_command(f"whitelist add {player}")
            embed = discord.Embed(
                title="Whitelist aktualisiert",
                description=f"`{player}` hinzugefuegt.\nAntwort: {response}",
                color=0x00ff00,
            )
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.followup.send(embed=embed)
            logger.info(f"[{srv.server_id}] Whitelist add: {player} von {interaction.user}")
        except Exception as e:
            await interaction.followup.send(
                f"Fehler: {e}", ephemeral=True
            )

    @whitelist_grp.command(name="remove", description="Spieler von Whitelist entfernen")
    @admin_only()
    @app_commands.autocomplete(server=_server_autocomplete)
    async def mc_whitelist_remove(self, interaction: discord.Interaction,
                                  player: str,
                                  server: Optional[str] = None):
        await interaction.response.defer()

        srv = await self._require_server(interaction, server)
        if not srv:
            return

        if not await srv.is_running():
            await interaction.followup.send(f"{srv.display_name} ist offline.")
            return

        try:
            response = await srv.rcon_command(f"whitelist remove {player}")
            embed = discord.Embed(
                title="Whitelist aktualisiert",
                description=f"`{player}` entfernt.\nAntwort: {response}",
                color=0xff9900,
            )
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.followup.send(embed=embed)
            logger.info(f"[{srv.server_id}] Whitelist remove: {player} von {interaction.user}")
        except Exception as e:
            await interaction.followup.send(
                f"Fehler: {e}", ephemeral=True
            )

    @whitelist_grp.command(name="list", description="Whitelist anzeigen")
    @app_commands.autocomplete(server=_server_autocomplete)
    async def mc_whitelist_list(self, interaction: discord.Interaction,
                                server: Optional[str] = None):
        await interaction.response.defer()

        srv = await self._require_server(interaction, server)
        if not srv:
            return

        if not await srv.is_running():
            await interaction.followup.send(f"{srv.display_name} ist offline.")
            return

        try:
            response = await srv.rcon_command("whitelist list")
            embed = discord.Embed(
                title=f"Whitelist — {srv.display_name}",
                description=response or "Whitelist ist leer.",
                color=0x0099ff,
            )
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(
                f"Fehler: {e}", ephemeral=True
            )

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  BACKUP: /mc backup create | list | restore                    ║
    # ╚════════════════════════════════════════════════════════════════╝

    @backup_grp.command(name="create", description="Welt-Backup erstellen")
    @spieler_only()
    @app_commands.autocomplete(server=_server_autocomplete)
    async def mc_backup_create(self, interaction: discord.Interaction,
                               name: Optional[str] = None,
                               server: Optional[str] = None):
        await interaction.response.defer()

        srv = await self._require_server(interaction, server)
        if not srv:
            return

        mgr = self._resolve_backup_mgr(srv.server_id)
        if not mgr:
            await interaction.followup.send(
                "Kein Backup-Manager fuer diesen Server.", ephemeral=True
            )
            return

        await interaction.followup.send("Backup wird erstellt...")

        try:
            success, msg, backup_path = await mgr.create_backup(
                name=name,
                created_by=str(interaction.user)
            )

            embed = discord.Embed(
                title=("Backup erstellt" if success else "Backup fehlgeschlagen"),
                description=f"**{srv.display_name}**\n{msg}",
                color=0x00ff00 if success else 0xff0000,
            )
            if success:
                embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.edit_original_response(content=None, embed=embed)
            if success:
                logger.info(f"[{srv.server_id}] Backup erstellt von {interaction.user}")
        except Exception as e:
            await interaction.followup.send(
                f"Fehler beim Backup: {e}", ephemeral=True
            )
            logger.error(f"[{srv.server_id}] Backup fehlgeschlagen: {e}")

    @backup_grp.command(name="list", description="Verfuegbare Backups anzeigen")
    @spieler_only()
    @app_commands.autocomplete(server=_server_autocomplete)
    async def mc_backup_list(self, interaction: discord.Interaction,
                             server: Optional[str] = None):
        await interaction.response.defer()

        srv = await self._require_server(interaction, server)
        if not srv:
            return

        mgr = self._resolve_backup_mgr(srv.server_id)
        if not mgr:
            await interaction.followup.send(
                "Kein Backup-Manager fuer diesen Server.", ephemeral=True
            )
            return

        backups = await mgr.list_backups(max_results=20)

        if not backups:
            await interaction.followup.send("Keine Backups verfuegbar.")
            return

        embed = discord.Embed(
            title=f"Backups — {srv.display_name}",
            color=0x0099ff,
        )

        for backup in backups[:10]:
            embed.add_field(
                name=backup["name"],
                value=f"**Groesse:** {backup['size_mb']:.1f} MB\n"
                      f"**Erstellt:** {backup['created']}\n"
                      f"**Von:** {backup['created_by']}",
                inline=False,
            )

        if len(backups) > 10:
            embed.set_footer(text=f"... und {len(backups) - 10} weitere")

        await interaction.followup.send(embed=embed)

    @backup_grp.command(name="restore", description="Backup wiederherstellen")
    @owner_only()
    @app_commands.autocomplete(server=_server_autocomplete)
    async def mc_backup_restore(self, interaction: discord.Interaction,
                                name: str,
                                server: Optional[str] = None):
        await interaction.response.defer()

        srv = await self._require_server(interaction, server)
        if not srv:
            return

        mgr = self._resolve_backup_mgr(srv.server_id)
        if not mgr:
            await interaction.followup.send(
                "Kein Backup-Manager fuer diesen Server.", ephemeral=True
            )
            return

        # Sicherheitsabfrage
        if await srv.is_running():
            await interaction.followup.send(
                f"**Warnung:** {srv.display_name} laeuft noch! "
                "Bitte Server vorher stoppen.",
                ephemeral=True
            )
            return

        await interaction.followup.send(
            f"Stelle Backup **{name}** fuer {srv.display_name} wieder her..."
        )

        try:
            success, msg = await mgr.restore(name)
            embed = discord.Embed(
                title=("Wiederhergestellt" if success
                       else "Wiederherstellung fehlgeschlagen"),
                description=msg,
                color=0x00ff00 if success else 0xff0000,
            )
            await interaction.followup.send(embed=embed)
            logger.info(
                f"[{srv.server_id}] Backup {name} wiederhergestellt von {interaction.user}"
            )
        except Exception as e:
            await interaction.followup.send(
                f"Fehler bei der Wiederherstellung: {e}", ephemeral=True
            )
            logger.error(f"[{srv.server_id}] Restore fehlgeschlagen: {e}")

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  ADMIN BEFEHLE: say, difficulty, weather, time, gamemode       ║
    # ╚════════════════════════════════════════════════════════════════╝

    @mc.command(name="say", description="Nachricht im Spiel ansagen")
    @admin_only()
    @app_commands.autocomplete(server=_server_autocomplete)
    async def mc_say(self, interaction: discord.Interaction,
                     message: str,
                     server: Optional[str] = None):
        await interaction.response.defer()

        srv = await self._require_server(interaction, server)
        if not srv:
            return

        if not await srv.is_running():
            await interaction.followup.send(f"{srv.display_name} ist offline.")
            return

        try:
            await srv.rcon_command(f"say {message}")
            embed = discord.Embed(
                title=f"Nachricht gesendet — {srv.display_name}",
                description=message,
                color=0x00ff00,
            )
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(
                f"Fehler beim Senden: {e}", ephemeral=True
            )

    @mc.command(name="difficulty", description="Schwierigkeitsgrad einstellen")
    @admin_only()
    @app_commands.autocomplete(server=_server_autocomplete)
    async def mc_difficulty(self, interaction: discord.Interaction,
                            level: str,
                            server: Optional[str] = None):
        valid_levels = ["peaceful", "easy", "normal", "hard"]
        if level.lower() not in valid_levels:
            await interaction.response.send_message(
                f"Ungueltig. Valide: {', '.join(valid_levels)}",
                ephemeral=True
            )
            return

        await interaction.response.defer()
        srv = await self._require_server(interaction, server)
        if not srv:
            return

        if not await srv.is_running():
            await interaction.followup.send(f"{srv.display_name} ist offline.")
            return

        try:
            await srv.rcon_command(f"difficulty {level.lower()}")
            embed = discord.Embed(
                title=f"Schwierigkeit geaendert — {srv.display_name}",
                description=f"Neue Schwierigkeit: **{level}**",
                color=0x00ff00,
            )
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"Fehler: {e}", ephemeral=True)

    @mc.command(name="weather", description="Wetter einstellen")
    @admin_only()
    @app_commands.autocomplete(server=_server_autocomplete)
    async def mc_weather(self, interaction: discord.Interaction,
                         weather_type: str,
                         server: Optional[str] = None):
        valid_types = ["clear", "rain", "thunder"]
        if weather_type.lower() not in valid_types:
            await interaction.response.send_message(
                f"Ungueltig. Valide: {', '.join(valid_types)}",
                ephemeral=True
            )
            return

        await interaction.response.defer()
        srv = await self._require_server(interaction, server)
        if not srv:
            return

        if not await srv.is_running():
            await interaction.followup.send(f"{srv.display_name} ist offline.")
            return

        try:
            await srv.rcon_command(f"weather {weather_type.lower()}")
            embed = discord.Embed(
                title=f"Wetter geaendert — {srv.display_name}",
                description=f"Neues Wetter: **{weather_type}**",
                color=0x00ff00,
            )
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"Fehler: {e}", ephemeral=True)

    @mc.command(name="time", description="Tageszeit einstellen")
    @admin_only()
    @app_commands.autocomplete(server=_server_autocomplete)
    async def mc_time(self, interaction: discord.Interaction,
                      value: str,
                      server: Optional[str] = None):
        valid_presets = ["day", "night", "noon", "midnight"]
        if value.lower() not in valid_presets:
            try:
                time_val = int(value)
                if not (0 <= time_val <= 24000):
                    await interaction.response.send_message(
                        "Zeit muss zwischen 0 und 24000 liegen.",
                        ephemeral=True
                    )
                    return
            except ValueError:
                await interaction.response.send_message(
                    f"Ungueltig. Presets: {', '.join(valid_presets)} oder 0-24000",
                    ephemeral=True
                )
                return

        await interaction.response.defer()
        srv = await self._require_server(interaction, server)
        if not srv:
            return

        if not await srv.is_running():
            await interaction.followup.send(f"{srv.display_name} ist offline.")
            return

        try:
            await srv.rcon_command(f"time set {value}")
            embed = discord.Embed(
                title=f"Zeit geaendert — {srv.display_name}",
                description=f"Neue Zeit: **{value}**",
                color=0x00ff00,
            )
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"Fehler: {e}", ephemeral=True)

    @mc.command(name="gamemode", description="Spielmodus setzen")
    @admin_only()
    @app_commands.autocomplete(server=_server_autocomplete)
    async def mc_gamemode(self, interaction: discord.Interaction,
                          mode: str,
                          player: Optional[str] = None,
                          server: Optional[str] = None):
        valid_modes = ["survival", "creative", "adventure", "spectator"]
        if mode.lower() not in valid_modes:
            await interaction.response.send_message(
                f"Ungueltig. Valide: {', '.join(valid_modes)}",
                ephemeral=True
            )
            return

        await interaction.response.defer()
        srv = await self._require_server(interaction, server)
        if not srv:
            return

        if not await srv.is_running():
            await interaction.followup.send(f"{srv.display_name} ist offline.")
            return

        try:
            cmd = f"gamemode {mode.lower()} {player}" if player else f"gamemode {mode.lower()}"
            await srv.rcon_command(cmd)

            target = player or "Alle"
            embed = discord.Embed(
                title=f"Spielmodus geaendert — {srv.display_name}",
                description=f"**{target}**: {mode}",
                color=0x00ff00,
            )
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"Fehler: {e}", ephemeral=True)

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  CONFIG: /mc config settings | set | backup | restore | ...    ║
    # ╚════════════════════════════════════════════════════════════════╝

    @config_grp.command(name="settings", description="Server-Einstellungen anzeigen")
    @app_commands.autocomplete(server=_server_autocomplete)
    async def config_settings(self, interaction: discord.Interaction,
                              server: Optional[str] = None):
        """Zeigt die wichtigsten server.properties Werte"""
        await interaction.response.defer()

        srv = await self._require_server(interaction, server)
        if not srv:
            return

        try:
            props = await srv.get_properties()
            if not props:
                await interaction.followup.send(
                    "server.properties nicht lesbar.", ephemeral=True
                )
                return

            # Wichtigste Einstellungen gruppiert anzeigen
            embed = discord.Embed(
                title=f"Server-Einstellungen — {srv.display_name}",
                color=0x0099ff,
            )

            # Spiel-Einstellungen
            game_lines = []
            for key in ["difficulty", "gamemode", "hardcore", "pvp",
                        "max-players", "motd", "level-name"]:
                if key in props:
                    game_lines.append(f"**{key}:** {props[key]}")
            if game_lines:
                embed.add_field(
                    name="Spiel",
                    value="\n".join(game_lines),
                    inline=True,
                )

            # Performance
            perf_lines = []
            for key in ["view-distance", "simulation-distance",
                        "max-world-size", "player-idle-timeout"]:
                if key in props:
                    perf_lines.append(f"**{key}:** {props[key]}")
            if perf_lines:
                embed.add_field(
                    name="Performance",
                    value="\n".join(perf_lines),
                    inline=True,
                )

            # Netzwerk/Sicherheit
            net_lines = []
            for key in ["server-port", "online-mode", "white-list",
                        "spawn-protection", "enable-command-block",
                        "allow-flight"]:
                if key in props:
                    net_lines.append(f"**{key}:** {props[key]}")
            if net_lines:
                embed.add_field(
                    name="Netzwerk/Sicherheit",
                    value="\n".join(net_lines),
                    inline=False,
                )

            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(
                f"Fehler beim Lesen der Einstellungen: {e}", ephemeral=True
            )

    @config_grp.command(name="set", description="Server-Einstellung aendern")
    @admin_only()
    @app_commands.autocomplete(server=_server_autocomplete)
    async def config_set(self, interaction: discord.Interaction,
                         key: str, value: str,
                         server: Optional[str] = None):
        """Aendert einen Wert in server.properties (erfordert Neustart)"""
        await interaction.response.defer()

        srv = await self._require_server(interaction, server)
        if not srv:
            return

        # Sicherheits-Whitelist pruefen
        if key.lower() not in self.ALLOWED_CONFIG_KEYS:
            allowed = ", ".join(sorted(self.ALLOWED_CONFIG_KEYS))
            await interaction.followup.send(
                f"Key `{key}` ist nicht erlaubt.\n"
                f"Erlaubte Keys: `{allowed}`",
                ephemeral=True
            )
            return

        try:
            success = await srv.set_property(key.lower(), value)
            if success:
                embed = discord.Embed(
                    title=f"Einstellung geaendert — {srv.display_name}",
                    description=(
                        f"**{key}** = `{value}`\n\n"
                        f"⚠️ Server-Neustart erforderlich!"
                    ),
                    color=0x00ff00,
                )
                embed.set_footer(text=f"von {interaction.user.display_name}")
                await interaction.followup.send(embed=embed)
                logger.info(
                    f"[{srv.server_id}] Config geaendert: {key}={value} "
                    f"von {interaction.user}"
                )
            else:
                await interaction.followup.send(
                    f"Fehler beim Setzen von `{key}`.", ephemeral=True
                )
        except Exception as e:
            await interaction.followup.send(
                f"Fehler: {e}", ephemeral=True
            )

    @config_set.autocomplete("key")
    async def _config_key_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete fuer Config-Keys"""
        return [
            app_commands.Choice(name=k, value=k)
            for k in sorted(self.ALLOWED_CONFIG_KEYS)
            if current.lower() in k.lower()
        ][:25]

    @config_grp.command(name="backup", description="Server-Einstellungen sichern")
    @admin_only()
    @app_commands.autocomplete(server=_server_autocomplete)
    async def config_backup(self, interaction: discord.Interaction,
                            name: Optional[str] = None,
                            server: Optional[str] = None):
        """Erstellt ein Backup der server.properties"""
        await interaction.response.defer()

        srv = await self._require_server(interaction, server)
        if not srv:
            return

        mgr = self._resolve_settings_mgr(srv.server_id)
        if not mgr:
            await interaction.followup.send(
                "Kein Settings-Manager fuer diesen Server.", ephemeral=True
            )
            return

        success, msg, _ = await mgr.save_settings(
            name=name, created_by=str(interaction.user)
        )
        embed = discord.Embed(
            title=("Settings gesichert" if success else "Fehler"),
            description=f"**{srv.display_name}**\n{msg}",
            color=0x00ff00 if success else 0xff0000,
        )
        if success:
            embed.set_footer(text=f"von {interaction.user.display_name}")
        await interaction.followup.send(embed=embed)

    @config_grp.command(name="restore", description="Server-Einstellungen wiederherstellen")
    @owner_only()
    @app_commands.autocomplete(server=_server_autocomplete)
    async def config_restore(self, interaction: discord.Interaction,
                             filename: str,
                             server: Optional[str] = None):
        """Stellt server.properties aus Backup wieder her"""
        await interaction.response.defer()

        srv = await self._require_server(interaction, server)
        if not srv:
            return

        mgr = self._resolve_settings_mgr(srv.server_id)
        if not mgr:
            await interaction.followup.send(
                "Kein Settings-Manager fuer diesen Server.", ephemeral=True
            )
            return

        success, msg = await mgr.restore_settings(filename)
        embed = discord.Embed(
            title=("Settings wiederhergestellt" if success else "Fehler"),
            description=f"**{srv.display_name}**\n{msg}",
            color=0x00ff00 if success else 0xff0000,
        )
        if success:
            embed.set_footer(text=f"von {interaction.user.display_name}")
        await interaction.followup.send(embed=embed)

    @config_restore.autocomplete("filename")
    async def _config_restore_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete fuer Settings-Backup-Dateien"""
        # Server aus bisherigen Parametern ermitteln
        server_id = None
        if interaction.namespace and hasattr(interaction.namespace, 'server'):
            server_id = interaction.namespace.server
        mgr = self._resolve_settings_mgr(server_id)
        if not mgr:
            return []
        backups = mgr.list_backups()
        return [
            app_commands.Choice(name=b["filename"][:100], value=b["filename"])
            for b in backups
            if current.lower() in b["filename"].lower()
        ][:25]

    @config_grp.command(name="update", description="Server-Update pruefen/installieren")
    @owner_only()
    @app_commands.autocomplete(server=_server_autocomplete)
    async def config_update(self, interaction: discord.Interaction,
                            install: bool = False,
                            server: Optional[str] = None):
        """Prueft auf Paper-Updates und installiert optional"""
        await interaction.response.defer()

        srv = await self._require_server(interaction, server)
        if not srv:
            return

        checker = self._resolve_update_checker(srv.server_id)
        if not checker:
            await interaction.followup.send(
                f"Kein Update-Checker fuer {srv.display_name} verfuegbar.\n"
                "(Nur fuer Paper-Server, nicht fuer Fabric/Forge)",
                ephemeral=True
            )
            return

        # Update pruefen
        available, info = await checker.check()

        if not info.get("current_version"):
            await interaction.followup.send(
                "MC-Version konnte nicht ermittelt werden.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"Update-Status — {srv.display_name}",
            color=0xff9900 if available else 0x00ff00,
        )
        embed.add_field(
            name="MC-Version",
            value=info["current_version"],
            inline=True,
        )
        embed.add_field(
            name="Aktueller Build",
            value=str(info.get("current_build", "?")),
            inline=True,
        )
        embed.add_field(
            name="Neuester Build",
            value=str(info.get("latest_build", "?")),
            inline=True,
        )

        if available and install:
            # Update installieren (Server muss gestoppt sein)
            if await srv.is_running():
                embed.add_field(
                    name="Installation",
                    value="⚠️ Server muss zuerst gestoppt werden!",
                    inline=False,
                )
            else:
                embed.add_field(
                    name="Installation",
                    value="Update wird heruntergeladen...",
                    inline=False,
                )
                await interaction.followup.send(embed=embed)

                success, msg = await checker.perform_update()
                result_embed = discord.Embed(
                    title=("Update installiert" if success else "Update fehlgeschlagen"),
                    description=msg,
                    color=0x00ff00 if success else 0xff0000,
                )
                result_embed.set_footer(text=f"von {interaction.user.display_name}")
                await interaction.followup.send(embed=result_embed)
                return
        elif available:
            embed.add_field(
                name="Update verfuegbar!",
                value="Nutze `/mc config update install:True` zum Installieren.",
                inline=False,
            )
        else:
            embed.description = "Server ist auf dem neuesten Stand."

        await interaction.followup.send(embed=embed)

    @config_grp.command(name="stats", description="World-Statistiken anzeigen")
    @app_commands.autocomplete(server=_server_autocomplete)
    async def config_stats(self, interaction: discord.Interaction,
                           server: Optional[str] = None):
        """Zeigt World-Groesse, Spielerzahl und Uptime"""
        await interaction.response.defer()

        srv = await self._require_server(interaction, server)
        if not srv:
            return

        embed = discord.Embed(
            title=f"Statistiken — {srv.display_name}",
            color=0x0099ff,
        )

        # World-Groesse
        try:
            world_bytes = await srv.get_world_size()
            embed.add_field(
                name="World-Groesse",
                value=format_bytes(world_bytes),
                inline=True,
            )
        except Exception:
            embed.add_field(name="World-Groesse", value="Nicht verfuegbar", inline=True)

        # Server-Status
        status = await srv.get_status()
        if status["running"]:
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
        else:
            embed.add_field(name="Status", value="Offline", inline=True)

        # Backup-Info
        mgr = self._resolve_backup_mgr(srv.server_id)
        if mgr:
            backups = await mgr.list_backups(max_results=1)
            if backups:
                latest = backups[0]
                embed.add_field(
                    name="Letztes Backup",
                    value=f"{latest['name']} ({latest['size_mb']:.1f} MB)",
                    inline=False,
                )

        await interaction.followup.send(embed=embed)

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  BACKUP DOWNLOAD: /mc backup download                          ║
    # ╚════════════════════════════════════════════════════════════════╝

    @backup_grp.command(name="download", description="Backup als ZIP herunterladen")
    @spieler_only()
    @app_commands.autocomplete(server=_server_autocomplete)
    async def mc_backup_download(self, interaction: discord.Interaction,
                                 name: Optional[str] = None,
                                 server: Optional[str] = None):
        """Sendet ein Backup als ZIP-Datei per Discord (max 25 MB)"""
        await interaction.response.defer()

        srv = await self._require_server(interaction, server)
        if not srv:
            return

        mgr = self._resolve_backup_mgr(srv.server_id)
        if not mgr:
            await interaction.followup.send(
                "Kein Backup-Manager fuer diesen Server.", ephemeral=True
            )
            return

        # Backup ermitteln
        backups = await mgr.list_backups(max_results=20)
        if not backups:
            await interaction.followup.send("Keine Backups verfuegbar.")
            return

        if name:
            target = next((b for b in backups if b["name"] == name), None)
            if not target:
                await interaction.followup.send(
                    f"Backup `{name}` nicht gefunden.", ephemeral=True
                )
                return
        else:
            target = backups[0]  # Neuestes Backup

        # Groesse pruefen
        size_mb = target["size_mb"]
        if size_mb > 24:
            await interaction.followup.send(
                f"Backup `{target['name']}` ist zu gross ({size_mb:.1f} MB).\n"
                "Discord-Limit: 25 MB. Bitte direkt vom Server herunterladen."
            )
            return

        backup_path = target["path"]
        if not backup_path or not Path(backup_path).exists():
            await interaction.followup.send(
                "Backup-Pfad nicht verfuegbar.", ephemeral=True
            )
            return

        await interaction.followup.send(
            f"Erstelle ZIP von `{target['name']}`..."
        )

        loop = asyncio.get_running_loop()

        try:
            # ZIP erstellen (im Executor)
            def _create_zip() -> Path:
                tmp = tempfile.NamedTemporaryFile(
                    suffix=".zip", delete=False,
                    prefix=f"mc_backup_{srv.server_id.lower()}_"
                )
                tmp.close()
                zip_path = Path(tmp.name)
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                    base = Path(backup_path)
                    for entry in base.rglob("*"):
                        if entry.is_file():
                            arcname = entry.relative_to(base)
                            zf.write(entry, arcname)
                return zip_path

            zip_path = await loop.run_in_executor(None, _create_zip)

            # ZIP-Groesse pruefen
            zip_size = zip_path.stat().st_size
            if zip_size > 25 * 1024 * 1024:
                zip_path.unlink()
                await interaction.followup.send(
                    f"ZIP-Datei ist zu gross ({zip_size / (1024*1024):.1f} MB)."
                )
                return

            # Per Discord senden
            await interaction.followup.send(
                file=discord.File(
                    zip_path,
                    filename=f"{target['name']}.zip"
                )
            )

            # Temporaere Datei aufraeumen
            zip_path.unlink(missing_ok=True)

        except Exception as e:
            await interaction.followup.send(
                f"Fehler beim Erstellen der ZIP-Datei: {e}", ephemeral=True
            )
            logger.error(f"[{srv.server_id}] Backup-Download fehlgeschlagen: {e}")

    @mc_backup_download.autocomplete("name")
    async def _backup_download_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete fuer Backup-Namen"""
        server_id = None
        if interaction.namespace and hasattr(interaction.namespace, 'server'):
            server_id = interaction.namespace.server
        mgr = self._resolve_backup_mgr(server_id)
        if not mgr:
            return []
        # Synchroner Wrapper — list_backups ist async
        # Fuer Autocomplete muessen wir einen Workaround nutzen
        try:
            backups = await mgr.list_backups(max_results=10)
            return [
                app_commands.Choice(name=b["name"][:100], value=b["name"])
                for b in backups
                if current.lower() in b["name"].lower()
            ][:25]
        except Exception:
            return []

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  OWNER: RCON Raw Command                                       ║
    # ╚════════════════════════════════════════════════════════════════╝

    @mc.command(name="command", description="RCON Befehl ausfuehren")
    @owner_only()
    @app_commands.autocomplete(server=_server_autocomplete)
    async def mc_command(self, interaction: discord.Interaction,
                         cmd: str,
                         server: Optional[str] = None):
        await interaction.response.defer()

        srv = await self._require_server(interaction, server)
        if not srv:
            return

        if not await srv.is_running():
            await interaction.followup.send(f"{srv.display_name} ist offline.")
            return

        try:
            response = await srv.rcon_command(cmd)
            if len(response) > 1900:
                response = response[:1900] + "... (gekuerzt)"

            embed = discord.Embed(
                title=f"RCON — {srv.display_name}",
                description=f"**Befehl:** `{cmd}`\n\n**Antwort:**\n```\n{response}\n```",
                color=0x0099ff,
            )
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.followup.send(embed=embed)
            logger.info(f"[{srv.server_id}] RCON von {interaction.user}: {cmd}")
        except Exception as e:
            embed = discord.Embed(
                title=f"RCON Fehler — {srv.display_name}",
                description=f"**Befehl:** `{cmd}`\n**Fehler:** {e}",
                color=0xff0000,
            )
            await interaction.followup.send(embed=embed)

    # ==================================================================
    # Fehlerbehandlung
    # ==================================================================

    async def cog_app_command_error(
        self, interaction: discord.Interaction,
        error: app_commands.AppCommandError
    ) -> None:
        """Zentrale Fehlerbehandlung fuer alle Commands in dieser Cog"""
        if isinstance(error, app_commands.CheckFailure):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Keine Berechtigung fuer diesen Befehl.",
                    ephemeral=True
                )
            return

        cmd_name = interaction.command.name if interaction.command else "unknown"
        logger.error(f"Command-Fehler in {cmd_name}: {error}", exc_info=True)
        try:
            msg = f"Ein Fehler ist aufgetreten: {error}"
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass


async def setup(bot):
    """Cog laden"""
    await bot.add_cog(MinecraftCog(bot))
