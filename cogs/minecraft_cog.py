"""
Minecraft Unified Cog - Alle /mc Befehle mit Multi-Server-Support

Command-Struktur:
  /mc status [server]                        (Server-Status - Alle)
  /mc start|stop|restart|cancel [server]     (Server-Steuerung - Admin)
  /mc players list|kick|ban [server]         (Spieler-Verwaltung - Spieler/Admin)
  /mc backup create|list|restore|download    (Backup-Verwaltung - Spieler/Owner)
  /mc whitelist add|remove|list [server]     (Whitelist - Admin)
  /mc config settings|set|backup|restore     (Server-Konfiguration)
  /mc config autosave|update|stats|modpack_check (Autosave/Update/Statistiken)
  /mc blacklist add|remove|list|history       (Blacklist - Admin/Spieler)
  /mc command <cmd> [server]                 (RCON ausfuehren - Owner)
  /mc say|difficulty|weather|time|gamemode   (Admin-Befehle)

Server-Auswahl: Autocomplete zeigt nur aktivierte Server an.
Bei nur einem Server wird dieser automatisch gewaehlt.
"""

import asyncio
import json
import re as _re
import tempfile
import zipfile
from pathlib import Path
import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional, Dict

from utils import get_logger, format_uptime, format_bytes, status_emoji, DATA_DIR
from utils.permissions import admin_only, spieler_only, owner_only


def _sanitize_rcon_input(text: str, max_length: int = 100) -> str:
    """Sanitisiert User-Input fuer RCON-Befehle. Erlaubt nur sichere Zeichen."""
    sanitized = _re.sub(r'[^\w\s\-]', '', text, flags=_re.UNICODE)
    return sanitized[:max_length].strip()
from modules.restart_timer import RestartTimer, TimerResult
from modules.minecraft.server import MinecraftServer
from modules.minecraft.backup import MinecraftBackupManager
from modules.minecraft.settings_backup import MinecraftSettingsBackup
from modules.minecraft.update_checker import MinecraftUpdateChecker
from modules.minecraft.blacklist import MinecraftBlacklist

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
    blacklist_grp = app_commands.Group(
        name="blacklist", parent=mc, description="Serveruebergreifendes Ban-System"
    )

    # ==================================================================
    # Init
    # ==================================================================

    def __init__(self, bot: commands.Bot):
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

        # Blacklist-System (Phase 8e)
        self.blacklist = getattr(bot, 'mc_blacklist', None)

        # Autosave-System (Phase 8b)
        self._autosave_file = DATA_DIR / "mc_autosave.json"
        self._autosave_intervals: Dict[str, int] = {}  # server_id -> Minuten
        self._autosave_tasks: Dict[str, asyncio.Task] = {}
        self._load_autosave_config()

    async def cog_load(self) -> None:
        """Wird beim Laden des Cogs aufgerufen — startet Autosave-Tasks"""
        for server_id, minutes in self._autosave_intervals.items():
            if minutes > 0 and server_id in self.servers:
                self._autosave_tasks[server_id] = asyncio.create_task(
                    self._autosave_loop(server_id, minutes)
                )
                logger.info(f"[{server_id}] Autosave-Task gestartet: alle {minutes} Min")

    async def cog_unload(self) -> None:
        """Wird beim Entladen des Cogs aufgerufen — stoppt Autosave-Tasks"""
        for server_id in list(self._autosave_tasks):
            self._stop_autosave_task(server_id)
        logger.info("Alle Autosave-Tasks gestoppt")

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

    async def _require_online_server(
        self, interaction: discord.Interaction, server_id: Optional[str]
    ) -> Optional[MinecraftServer]:
        """Server ermitteln UND pruefen ob er laeuft.

        Kombiniert _require_server() + is_running()-Check in einer Methode.
        Gibt None zurueck und sendet Fehlermeldung wenn Server nicht gefunden
        oder offline ist. (Phase 8a: Server-Offline-Decorator Refactoring)
        """
        srv = await self._require_server(interaction, server_id)
        if srv is None:
            return None
        if not await srv.is_running():
            await interaction.followup.send(f"{srv.display_name} ist offline.")
            return None
        return srv

    # ==================================================================
    # Autosave-Hilfsmethoden (Phase 8b)
    # ==================================================================

    def _load_autosave_config(self) -> None:
        """Laedt Autosave-Intervalle aus JSON-Datei"""
        try:
            if self._autosave_file.exists():
                with open(self._autosave_file, "r", encoding="utf-8") as f:
                    self._autosave_intervals = json.load(f)
                logger.info(f"Autosave-Config geladen: {self._autosave_intervals}")
        except Exception as e:
            logger.error(f"Autosave-Config laden fehlgeschlagen: {e}")
            self._autosave_intervals = {}

    def _save_autosave_config(self) -> None:
        """Speichert Autosave-Intervalle in JSON-Datei"""
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(self._autosave_file, "w", encoding="utf-8") as f:
                json.dump(self._autosave_intervals, f, indent=2)
        except Exception as e:
            logger.error(f"Autosave-Config speichern fehlgeschlagen: {e}")

    async def _autosave_loop(self, server_id: str, interval_minutes: int) -> None:
        """Periodischer save-all Aufruf fuer einen Server"""
        try:
            while True:
                await asyncio.sleep(interval_minutes * 60)
                srv = self.servers.get(server_id)
                if srv and await srv.is_running():
                    try:
                        await srv.rcon_command("save-all")
                        logger.info(f"[{server_id}] Autosave: save-all ausgefuehrt")
                    except Exception as e:
                        logger.debug(f"[{server_id}] Autosave fehlgeschlagen: {e}")
        except asyncio.CancelledError:
            pass

    def _stop_autosave_task(self, server_id: str) -> None:
        """Stoppt den Autosave-Task fuer einen Server"""
        task = self._autosave_tasks.pop(server_id, None)
        if task and not task.done():
            task.cancel()

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

        srv = await self._require_online_server(interaction, server)
        if not srv:
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

        srv = await self._require_online_server(interaction, server)
        if not srv:
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

        srv = await self._require_online_server(interaction, server)
        if not srv:
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

        srv = await self._require_online_server(interaction, server)
        if not srv:
            return

        try:
            safe_player = _sanitize_rcon_input(player)
            safe_reason = _sanitize_rcon_input(reason, 200)
            await srv.rcon_command(f"kick {safe_player} {safe_reason}")
            embed = discord.Embed(
                title="Spieler gekickt",
                description=f"**{safe_player}** wurde von {srv.display_name} gekickt.",
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

        srv = await self._require_online_server(interaction, server)
        if not srv:
            return

        try:
            safe_player = _sanitize_rcon_input(player)
            safe_reason = _sanitize_rcon_input(reason, 200)
            await srv.rcon_command(f"ban {safe_player} {safe_reason}")

            # Automatisch Blacklist-Eintrag erstellen (Phase 8e)
            if self.blacklist:
                await self.blacklist.add(
                    safe_player, safe_reason,
                    str(interaction.user), servers=[srv.server_id],
                )

            embed = discord.Embed(
                title="Spieler gebannt",
                description=f"**{safe_player}** wurde auf {srv.display_name} gebannt.\n"
                           f"Grund: {safe_reason}",
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

        srv = await self._require_online_server(interaction, server)
        if not srv:
            return

        try:
            safe_player = _sanitize_rcon_input(player)
            await srv.rcon_command(f"pardon {safe_player}")
            embed = discord.Embed(
                title="Spieler entbannt",
                description=f"**{safe_player}** wurde auf {srv.display_name} entbannt.",
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

        srv = await self._require_online_server(interaction, server)
        if not srv:
            return

        try:
            safe_player = _sanitize_rcon_input(player)
            response = await srv.rcon_command(f"whitelist add {safe_player}")
            embed = discord.Embed(
                title="Whitelist aktualisiert",
                description=f"`{safe_player}` hinzugefuegt.\nAntwort: {response}",
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

        srv = await self._require_online_server(interaction, server)
        if not srv:
            return

        try:
            safe_player = _sanitize_rcon_input(player)
            response = await srv.rcon_command(f"whitelist remove {safe_player}")
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

        srv = await self._require_online_server(interaction, server)
        if not srv:
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
    # ║  ADMIN BEFEHLE: say                                            ║
    # ║  (difficulty, weather, time, gamemode entfernt — nur In-Game)  ║
    # ╚════════════════════════════════════════════════════════════════╝

    @mc.command(name="say", description="Nachricht im Spiel ansagen")
    @admin_only()
    @app_commands.autocomplete(server=_server_autocomplete)
    async def mc_say(self, interaction: discord.Interaction,
                     message: str,
                     server: Optional[str] = None):
        await interaction.response.defer()

        srv = await self._require_online_server(interaction, server)
        if not srv:
            return

        try:
            safe_message = _sanitize_rcon_input(message, 200)
            await srv.rcon_command(f"say {safe_message}")
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

    # --- F22: difficulty, weather, time, gamemode entfernt (v3.2.0) ---
    # Diese Gameplay-Commands werden direkt In-Game verwendet.
    # Entfernt: mc_difficulty, mc_weather, mc_time, mc_gamemode

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

    @config_grp.command(name="autosave", description="Autosave-Intervall konfigurieren (save-all)")
    @app_commands.describe(
        intervall="Intervall in Minuten (0 = deaktivieren, min. 1)",
        server="Server-Auswahl"
    )
    @admin_only()
    @app_commands.autocomplete(server=_server_autocomplete)
    async def config_autosave(self, interaction: discord.Interaction,
                              intervall: int,
                              server: Optional[str] = None):
        """Konfiguriert periodisches save-all via RCON.

        Sendet sofort save-all und startet einen Timer der regelmaessig
        save-all ausfuehrt. Aendert NICHT server.properties.
        """
        await interaction.response.defer()

        srv = await self._require_server(interaction, server)
        if not srv:
            return

        if intervall < 0:
            await interaction.followup.send(
                "Intervall muss >= 0 sein.", ephemeral=True
            )
            return

        if 0 < intervall < 1:
            await interaction.followup.send(
                "Minimum ist 1 Minute.", ephemeral=True
            )
            return

        old_interval = self._autosave_intervals.get(srv.server_id, 0)

        # Bestehenden Task stoppen
        self._stop_autosave_task(srv.server_id)

        if intervall > 0:
            # Neuen Intervall setzen und Task starten
            self._autosave_intervals[srv.server_id] = intervall
            self._autosave_tasks[srv.server_id] = asyncio.create_task(
                self._autosave_loop(srv.server_id, intervall)
            )

            # Sofort save-all ausfuehren wenn Server online
            if await srv.is_running():
                try:
                    await srv.rcon_command("save-all")
                except Exception:
                    pass
        else:
            # Deaktivieren
            self._autosave_intervals.pop(srv.server_id, None)

        self._save_autosave_config()

        # Bestaetigungs-Embed
        embed = discord.Embed(
            title=f"Autosave — {srv.display_name}",
            color=0x2ecc71 if intervall > 0 else 0xff9900,
        )

        if intervall > 0:
            embed.description = f"Autosave alle **{intervall} Minuten** aktiviert."
            if old_interval > 0:
                embed.add_field(name="Vorher", value=f"{old_interval} Min", inline=True)
            embed.add_field(name="Neu", value=f"{intervall} Min", inline=True)
        else:
            embed.description = "Autosave **deaktiviert**."
            if old_interval > 0:
                embed.add_field(name="Vorher", value=f"{old_interval} Min", inline=True)

        embed.set_footer(text=f"von {interaction.user.display_name}")
        await interaction.followup.send(embed=embed)
        logger.info(
            f"[{srv.server_id}] Autosave geaendert: {old_interval} -> {intervall} Min "
            f"von {interaction.user}"
        )

    @config_grp.command(
        name="modpack_check",
        description="Manuell auf Modpack-Updates pruefen"
    )
    @admin_only()
    async def config_modpack_check(self, interaction: discord.Interaction):
        """Manueller Modpack-Update-Check via Modrinth/CurseForge"""
        await interaction.response.defer()

        updater = getattr(self.bot, 'modpack_updater', None)
        if not updater or not updater.enabled:
            await interaction.followup.send(
                "Modpack-Updater nicht konfiguriert.\n"
                "Setze `MC_BMC_MODPACK_ID` und `MC_BMC_MODPACK_VERSION` in der .env",
                ephemeral=True,
            )
            return

        try:
            available, info = await updater.check()

            if "error" in info:
                embed = discord.Embed(
                    title="Modpack-Check Fehler",
                    description=info["error"],
                    color=0xff0000,
                )
            elif available:
                embed = discord.Embed(
                    title="Modpack-Update verfuegbar!",
                    color=0xffa500,
                )
                embed.add_field(
                    name="Aktuell", value=info.get("current", "?"), inline=True
                )
                embed.add_field(
                    name="Neu", value=info.get("latest", "?"), inline=True
                )
                embed.add_field(
                    name="Quelle", value=info.get("source", "?"), inline=True
                )
                if info.get("changelog_url"):
                    embed.add_field(
                        name="Changelog",
                        value=info["changelog_url"],
                        inline=False,
                    )
            else:
                embed = discord.Embed(
                    title="Modpack ist aktuell",
                    description=f"Version: {info.get('current', '?')}",
                    color=0x00ff00,
                )
                embed.add_field(
                    name="Quelle", value=info.get("source", "?"), inline=True
                )

            embed.set_footer(text=f"Geprueft: {info.get('checked_at', '?')[:16]}")
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(
                f"Modpack-Check fehlgeschlagen: {e}", ephemeral=True
            )

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

        srv = await self._require_online_server(interaction, server)
        if not srv:
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

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  BLACKLIST: /mc blacklist add | remove | list | history        ║
    # ╚════════════════════════════════════════════════════════════════╝

    @blacklist_grp.command(
        name="add", description="Spieler auf die Blacklist setzen (serveruebergreifend)"
    )
    @admin_only()
    async def mc_blacklist_add(self, interaction: discord.Interaction,
                               spieler: str,
                               grund: str,
                               server: Optional[str] = None):
        """Ban auf allen oder einem bestimmten Server"""
        await interaction.response.defer()

        if not self.blacklist:
            await interaction.followup.send(
                "Blacklist-System nicht initialisiert.", ephemeral=True
            )
            return

        safe_player = _sanitize_rcon_input(spieler)
        safe_reason = _sanitize_rcon_input(grund, 200)

        if not safe_player:
            await interaction.followup.send(
                "Ungueltiger Spielername.", ephemeral=True
            )
            return

        # Server-Liste bestimmen
        server_ids = [server.upper()] if server else None

        added = await self.blacklist.add(
            safe_player, safe_reason,
            str(interaction.user), servers=server_ids,
        )

        if not added:
            await interaction.followup.send(
                f"**{safe_player}** ist bereits auf der Blacklist.", ephemeral=True
            )
            return

        # Ban via RCON an Server senden
        results = await self.blacklist.sync_ban_to_servers(
            safe_player, safe_reason, self.servers, server_ids,
        )

        # Ergebnis-Embed
        server_status = []
        for sid, success in results.items():
            name = self.servers[sid].display_name if sid in self.servers else sid
            server_status.append(f"{'✅' if success else '⚠️'} {name}")

        embed = discord.Embed(
            title="Spieler auf Blacklist gesetzt",
            description=(
                f"**Spieler:** {discord.utils.escape_mentions(safe_player)}\n"
                f"**Grund:** {discord.utils.escape_mentions(safe_reason)}\n"
                f"**Server:** {', '.join(server_ids) if server_ids else 'ALLE'}"
            ),
            color=0xff0000,
        )
        if server_status:
            embed.add_field(
                name="RCON-Status", value="\n".join(server_status), inline=False
            )
        embed.set_footer(text=f"von {interaction.user.display_name}")
        await interaction.followup.send(embed=embed)
        logger.info(f"Blacklist: {safe_player} hinzugefuegt von {interaction.user}")

    @blacklist_grp.command(
        name="remove", description="Spieler von der Blacklist entfernen"
    )
    @admin_only()
    async def mc_blacklist_remove(self, interaction: discord.Interaction,
                                  spieler: str):
        """Entbannt auf allen Servern"""
        await interaction.response.defer()

        if not self.blacklist:
            await interaction.followup.send(
                "Blacklist-System nicht initialisiert.", ephemeral=True
            )
            return

        safe_player = _sanitize_rcon_input(spieler)
        removed = await self.blacklist.remove(safe_player)

        if not removed:
            await interaction.followup.send(
                f"**{safe_player}** ist nicht auf der Blacklist.", ephemeral=True
            )
            return

        # Pardon via RCON an alle Server
        results = await self.blacklist.sync_unban_to_servers(
            safe_player, self.servers
        )

        server_status = []
        for sid, success in results.items():
            name = self.servers[sid].display_name if sid in self.servers else sid
            server_status.append(f"{'✅' if success else '⚠️'} {name}")

        embed = discord.Embed(
            title="Spieler von Blacklist entfernt",
            description=f"**{discord.utils.escape_mentions(safe_player)}** wurde entbannt.",
            color=0x00ff00,
        )
        if server_status:
            embed.add_field(
                name="RCON-Status", value="\n".join(server_status), inline=False
            )
        embed.set_footer(text=f"von {interaction.user.display_name}")
        await interaction.followup.send(embed=embed)
        logger.info(f"Blacklist: {safe_player} entfernt von {interaction.user}")

    @blacklist_grp.command(
        name="list", description="Alle aktiven Bans anzeigen"
    )
    @spieler_only()
    async def mc_blacklist_list(self, interaction: discord.Interaction):
        """Zeigt alle aktiven Blacklist-Eintraege"""
        await interaction.response.defer(ephemeral=True)

        if not self.blacklist:
            await interaction.followup.send(
                "Blacklist-System nicht initialisiert.", ephemeral=True
            )
            return

        active = self.blacklist.get_active_list()
        if not active:
            await interaction.followup.send("Keine aktiven Bans.", ephemeral=True)
            return

        lines = []
        for ban in active:
            name = discord.utils.escape_mentions(ban["player_name"])
            reason = discord.utils.escape_mentions(ban.get("reason", "—"))
            ts = ban.get("timestamp", "?")[:10]
            servers = ", ".join(ban.get("servers", ["ALL"]))
            lines.append(f"**{name}** — {reason} (Server: {servers}, {ts})")

        # Discord Embed max. 4096 Zeichen — bei vielen Bans aufteilen
        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:4000] + "\n…"

        embed = discord.Embed(
            title=f"MC Blacklist ({len(active)} aktive Bans)",
            description=text,
            color=0xff5555,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @blacklist_grp.command(
        name="history", description="Ban-Historie eines Spielers anzeigen"
    )
    @spieler_only()
    async def mc_blacklist_history(self, interaction: discord.Interaction,
                                   spieler: str):
        """Zeigt alle Bans (aktiv + inaktiv) eines Spielers"""
        await interaction.response.defer(ephemeral=True)

        if not self.blacklist:
            await interaction.followup.send(
                "Blacklist-System nicht initialisiert.", ephemeral=True
            )
            return

        safe_player = _sanitize_rcon_input(spieler)
        history = self.blacklist.get_history(safe_player)

        if not history:
            await interaction.followup.send(
                f"Keine Ban-Historie fuer **{discord.utils.escape_mentions(safe_player)}**.",
                ephemeral=True,
            )
            return

        lines = []
        for entry in history:
            status = "🔴 Aktiv" if entry.get("active", True) else "⚪ Aufgehoben"
            reason = discord.utils.escape_mentions(entry.get("reason", "—"))
            banned_by = discord.utils.escape_mentions(entry.get("banned_by", "?"))
            ts = entry.get("timestamp", "?")[:16].replace("T", " ")
            servers = ", ".join(entry.get("servers", ["ALL"]))
            line = (
                f"{status}\n"
                f"  Grund: {reason}\n"
                f"  Von: {banned_by}\n"
                f"  Server: {servers}\n"
                f"  Datum: {ts}"
            )
            if not entry.get("active", True) and entry.get("unbanned_at"):
                unban_ts = entry["unbanned_at"][:16].replace("T", " ")
                line += f"\n  Entbannt: {unban_ts}"
            lines.append(line)

        text = "\n\n".join(lines)
        if len(text) > 4000:
            text = text[:4000] + "\n…"

        embed = discord.Embed(
            title=f"Ban-Historie: {discord.utils.escape_mentions(safe_player)}",
            description=text,
            color=0xff9900,
        )
        embed.set_footer(text=f"{len(history)} Eintraege gesamt")
        await interaction.followup.send(embed=embed, ephemeral=True)

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
