"""
Minecraft Unified Cog - Alle /mc Befehle mit Multi-Server-Support

Command-Struktur (Phase 14: Admin-Commands ins Dashboard migriert):
  /mc status [server]                        (Server-Status - Alle)
  /mc players list|kick|ban|pardon [server]  (Spieler-Verwaltung - Spieler/Admin)
  /mc backup create|list|restore|download    (Backup-Verwaltung - Spieler/Owner)
  /mc whitelist add|remove|list [server]     (Whitelist - Admin)
  /mc config settings|stats|modpack_check    (Server-Konfiguration, nur Lesen)
  /mc blacklist add|remove|list|history       (Blacklist - Admin/Spieler)
  /mc world stats [server]                   (Welt-Analyse - Alle)
  /mc command <cmd> [server]                 (RCON ausführen - Owner)
  /mc say [banner] [repeat]                  (Ankuendigungs-Banner - Admin)

Ins Dashboard migriert (F25):
  start, stop, restart, cancel, config set, config backup,
  config restore, config update, config autosave

Server-Auswahl: Autocomplete zeigt nur aktivierte Server an.
Bei nur einem Server wird dieser automatisch gewaehlt.
"""

import asyncio
import json
import re as _re
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional, Dict

from utils import get_logger, format_uptime, format_bytes, status_emoji, DATA_DIR
from utils.permissions import admin_only, spieler_only, owner_only


def _sanitize_rcon_input(text: str, max_length: int = 100) -> str:
    """Sanitisiert User-Input für RCON-Befehle. Erlaubt nur ASCII-sichere Zeichen."""
    sanitized = _re.sub(r'[^a-zA-Z0-9_\s\-]', '', text)
    return sanitized[:max_length].strip()
from modules.minecraft.server import MinecraftServer
from modules.minecraft.backup import MinecraftBackupManager
from modules.minecraft.blacklist import MinecraftBlacklist
from modules.minecraft.world_analyzer import WorldAnalyzer

logger = get_logger("cogs.minecraft")


class MinecraftCog(commands.Cog):
    """Alle Minecraft-Server-Befehle unter /mc (Multi-Server)"""

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
        name="config", parent=mc, description="Server-Konfiguration (nur Lesen)"
    )
    blacklist_grp = app_commands.Group(
        name="blacklist", parent=mc, description="Serverübergreifendes Ban-System"
    )
    world_grp = app_commands.Group(
        name="world", parent=mc, description="Welt-Analyse & Statistiken"
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
        self.timer_mgr = bot.timer_mgr

        # Blacklist-System (Phase 8e)
        self.blacklist = getattr(bot, 'mc_blacklist', None)

        # IP-Tracker für UFW-Bans (Phase 10c: MC IP-Ban wie SAT)
        self.ip_trackers: Dict[str, object] = getattr(bot, 'mc_ip_trackers', {})

        # Announcement-Tasks (Phase 10b)
        self._active_announcements: Dict[str, asyncio.Task] = {}

        # Autosave-System (Phase 8b)
        self._autosave_file = DATA_DIR / "mc_autosave.json"
        self._autosave_intervals: Dict[str, int] = {}  # server_id -> Minuten
        self._autosave_tasks: Dict[str, asyncio.Task] = {}
        self._load_autosave_config()

    async def cog_load(self) -> None:
        """Wird beim Laden des Cogs aufgerufen — startet Autosave-Tasks"""
        for server_id, minutes in self._autosave_intervals.items():
            if minutes > 0 and server_id in self.servers:
                task = asyncio.create_task(
                    self._autosave_loop(server_id, minutes)
                )
                # M24-Fix: done-callback analog Announcement-Task (poppt aus Dict + loggt Exception)
                def _on_done(t: asyncio.Task, sid: str = server_id) -> None:
                    self._autosave_tasks.pop(sid, None)
                    if not t.cancelled() and t.exception():
                        logger.error(f"Autosave-Task {sid} fehlgeschlagen: {t.exception()}")
                task.add_done_callback(_on_done)
                self._autosave_tasks[server_id] = task
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
        """Backup-Manager für Server ermitteln"""
        if not self.backup_mgrs:
            return None

        if server_id:
            return self.backup_mgrs.get(server_id.upper())

        if len(self.backup_mgrs) == 1:
            return next(iter(self.backup_mgrs.values()))

        return None

    async def _require_server(
        self, interaction: discord.Interaction, server_id: Optional[str]
    ) -> Optional[MinecraftServer]:
        """Server ermitteln oder Fehlermeldung senden. Gibt None zurück bei Fehler."""
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
                    f"Mehrere Server verfügbar. Bitte Server angeben: {namen}",
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
        """Server ermitteln UND prüfen ob er läuft.

        Kombiniert _require_server() + is_running()-Check in einer Methode.
        Gibt None zurück und sendet Fehlermeldung wenn Server nicht gefunden
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
        """Periodischer save-all Aufruf für einen Server"""
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
        """Stoppt den Autosave-Task für einen Server"""
        task = self._autosave_tasks.pop(server_id, None)
        if task and not task.done():
            task.cancel()

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  CORE: /mc status (start|stop|restart|cancel -> Dashboard)    ║
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
            color=0x2ecc71 if online else 0xe74c3c,
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
                value=f"{timer.action_name} läuft...",
                inline=False,
            )

        await interaction.followup.send(embed=embed)

    # --- F25: start, stop, restart, cancel ins Web-Dashboard migriert ---

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
                color=0xf39c12,
            )
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.followup.send(embed=embed)
            logger.info(f"[{srv.server_id}] {player} gekickt von {interaction.user}")
        except Exception as e:
            await interaction.followup.send(
                f"Fehler beim Kicken: {e}", ephemeral=True
            )

    @players_grp.command(name="ban", description="Spieler bannen (RCON + IP-Sperre)")
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

            # 1. RCON-Ban (MC-interner Name/UUID-Ban)
            rcon_ok = False
            try:
                await srv.rcon_command(f"ban {safe_player} {safe_reason}")
                rcon_ok = True
            except Exception as rcon_err:
                logger.warning(f"[{srv.server_id}] RCON-Ban fehlgeschlagen: {rcon_err}")

            # 2. IP-Ban via UFW (Phase 10c: wie SAT)
            ip_ok = False
            ip_msg = ""
            ip_tracker = self.ip_trackers.get(srv.server_id)
            if ip_tracker:
                ip_success, ip_msg = await ip_tracker.ban_player(
                    safe_player, safe_reason,
                    str(interaction.user), api=srv
                )
                ip_ok = ip_success
            else:
                ip_msg = "IP-Tracker nicht verfügbar"

            # 3. Automatisch Blacklist-Eintrag erstellen (Phase 8e)
            if self.blacklist:
                # IP-Feld im Blacklist-Eintrag ergaenzen
                ip_address = ip_tracker.get_ip(safe_player) if ip_tracker else None
                await self.blacklist.add(
                    safe_player, safe_reason,
                    str(interaction.user), servers=[srv.server_id],
                    ip=ip_address,
                )

            embed = discord.Embed(
                title="Spieler gebannt",
                description=f"**{safe_player}** wurde auf {srv.display_name} gebannt.\n"
                           f"Grund: {safe_reason}",
                color=0xe74c3c,
            )
            embed.add_field(
                name="RCON-Ban", value="✅ Aktiv" if rcon_ok else "❌ Fehler", inline=True
            )
            embed.add_field(
                name="IP-Sperre (UFW)",
                value=f"✅ {ip_msg}" if ip_ok else f"⚠️ {ip_msg}",
                inline=True,
            )
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.followup.send(embed=embed)
            logger.info(
                f"[{srv.server_id}] {player} gebannt von {interaction.user} "
                f"(RCON: {rcon_ok}, IP: {ip_ok})"
            )
        except Exception as e:
            await interaction.followup.send(
                f"Fehler beim Bannen: {e}", ephemeral=True
            )

    @players_grp.command(name="pardon", description="Spieler entbannen (RCON + IP-Sperre aufheben)")
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

            # 1. RCON-Pardon (MC-interner Ban aufheben)
            rcon_ok = False
            try:
                await srv.rcon_command(f"pardon {safe_player}")
                rcon_ok = True
            except Exception as rcon_err:
                logger.warning(f"[{srv.server_id}] RCON-Pardon fehlgeschlagen: {rcon_err}")

            # 2. IP-Ban via UFW aufheben (Phase 10c)
            ip_ok = False
            ip_msg = ""
            ip_tracker = self.ip_trackers.get(srv.server_id)
            if ip_tracker:
                ip_success, ip_msg = await ip_tracker.unban_player(safe_player)
                ip_ok = ip_success
                if not ip_success and "nicht gebannt" in ip_msg.lower():
                    # Kein IP-Ban vorhanden — kein Fehler
                    ip_msg = "Kein IP-Ban vorhanden"
                    ip_ok = True
            else:
                ip_msg = "IP-Tracker nicht verfügbar"

            embed = discord.Embed(
                title="Spieler entbannt",
                description=f"**{safe_player}** wurde auf {srv.display_name} entbannt.",
                color=0x2ecc71,
            )
            embed.add_field(
                name="RCON-Pardon", value="✅ Aktiv" if rcon_ok else "❌ Fehler", inline=True
            )
            embed.add_field(
                name="IP-Sperre (UFW)",
                value=f"✅ {ip_msg}" if ip_ok else f"⚠️ {ip_msg}",
                inline=True,
            )
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.followup.send(embed=embed)
            logger.info(
                f"[{srv.server_id}] {player} entbannt von {interaction.user} "
                f"(RCON: {rcon_ok}, IP: {ip_ok})"
            )
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
                color=0x2ecc71,
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
                color=0xf39c12,
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
                "Kein Backup-Manager für diesen Server.", ephemeral=True
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
                color=0x2ecc71 if success else 0xe74c3c,
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
                "Kein Backup-Manager für diesen Server.", ephemeral=True
            )
            return

        backups = await mgr.list_backups(max_results=20)

        if not backups:
            await interaction.followup.send("Keine Backups verfügbar.")
            return

        embed = discord.Embed(
            title=f"Backups — {srv.display_name}",
            color=0x0099ff,
        )

        for backup in backups[:10]:
            embed.add_field(
                name=backup["name"],
                value=f"**Größe:** {backup['size_mb']:.1f} MB\n"
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
                "Kein Backup-Manager für diesen Server.", ephemeral=True
            )
            return

        # Sicherheitsabfrage
        if await srv.is_running():
            await interaction.followup.send(
                f"**Warnung:** {srv.display_name} läuft noch! "
                "Bitte Server vorher stoppen.",
                ephemeral=True
            )
            return

        await interaction.followup.send(
            f"Stelle Backup **{name}** für {srv.display_name} wieder her..."
        )

        try:
            success, msg = await mgr.restore(name)
            embed = discord.Embed(
                title=("Wiederhergestellt" if success
                       else "Wiederherstellung fehlgeschlagen"),
                description=msg,
                color=0x2ecc71 if success else 0xe74c3c,
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

    @mc.command(name="say", description="Ankuendigung im Spiel senden (Banner + Chat)")
    @admin_only()
    @app_commands.describe(
        message="Nachricht an alle Spieler",
        banner="Title-Banner auf dem Bildschirm anzeigen (Standard: true)",
        repeat="Nachricht X-mal wiederholen (alle 30s, für Restart-Warnungen)",
        server="Server-Auswahl"
    )
    @app_commands.autocomplete(server=_server_autocomplete)
    async def mc_say(self, interaction: discord.Interaction,
                     message: str,
                     banner: bool = True,
                     repeat: Optional[int] = None,
                     server: Optional[str] = None):
        """Sendet eine Ankuendigung an alle Spieler.

        Mit banner=True (Standard) wird ein grosser Title-Banner auf dem
        Bildschirm angezeigt. Mit repeat=X wird die Nachricht als Countdown
        wiederholt (z.B. für Restart-Warnungen).
        """
        await interaction.response.defer()

        srv = await self._require_online_server(interaction, server)
        if not srv:
            return

        safe_message = _sanitize_rcon_input(message, 200)
        if not safe_message:
            await interaction.followup.send(
                "Nachricht darf nicht leer sein.", ephemeral=True
            )
            return

        # Repeat-Modus: Wiederholte Ankuendigungen (z.B. Restart-Countdown)
        if repeat and repeat > 0:
            repeat = min(repeat, 30)  # Max 30 Wiederholungen (15 Min)
            await interaction.followup.send(
                f"Ankuendigung wird {repeat}x alle 30s gesendet auf {srv.display_name}..."
            )
            # Background-Task starten
            task = asyncio.create_task(
                self._repeat_announcement(srv, safe_message, repeat, banner)
            )
            # Task-Referenz speichern damit er nicht garbage-collected wird
            def _on_done(t: asyncio.Task, sid: str = srv.server_id) -> None:
                self._active_announcements.pop(sid, None)
                if not t.cancelled() and t.exception():
                    logger.error(f"Announcement-Task {sid} fehlgeschlagen: {t.exception()}")
            task.add_done_callback(_on_done)
            self._active_announcements[srv.server_id] = task

            embed = discord.Embed(
                title=f"Wiederholte Ankuendigung gestartet — {srv.display_name}",
                description=(
                    f"**Nachricht:** {safe_message}\n"
                    f"**Wiederholungen:** {repeat}x (alle 30s)\n"
                    f"**Banner:** {'Ja (erste Nachricht)' if banner else 'Nein'}"
                ),
                color=0xf39c12,
            )
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.edit_original_response(content=None, embed=embed)
            return

        # Einzelne Ankuendigung senden
        try:
            await self._send_announcement(srv, safe_message, banner)

            embed = discord.Embed(
                title=f"Ankuendigung gesendet — {srv.display_name}",
                description=f"**Nachricht:** {safe_message}",
                color=0x2ecc71,
            )
            if banner:
                embed.add_field(
                    name="Banner", value="Title + Subtitle + Actionbar + Chat", inline=False
                )
            else:
                embed.add_field(name="Banner", value="Nur Chat", inline=False)
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(
                f"Fehler beim Senden: {e}", ephemeral=True
            )
            logger.error(f"[{srv.server_id}] Ankuendigung fehlgeschlagen: {e}")

    async def _send_announcement(
        self, srv: MinecraftServer, message: str, banner: bool
    ) -> None:
        """Sendet eine Ankuendigung an alle Spieler eines Servers.

        Bei banner=True werden Title, Subtitle, Actionbar und Chat gesendet.
        Bei banner=False wird nur die Chat-Nachricht gesendet.
        """
        if banner:
            # Fade-In 1s (20 Ticks), Anzeige 5s (100 Ticks), Fade-Out 2s (40 Ticks)
            await srv.rcon_command('title @a times 20 100 40')
            # Grosser Title-Text
            await srv.rcon_command(
                'title @a title {"text":"Ankuendigung","color":"gold","bold":true}'
            )
            # Untertitel mit eigentlicher Nachricht
            title_json = json.dumps({"text": message, "color": "white"})
            await srv.rcon_command(f'title @a subtitle {title_json}')
            # Actionbar (bleibt laenger sichtbar)
            actionbar_json = json.dumps({"text": message, "color": "yellow"})
            await srv.rcon_command(f'title @a actionbar {actionbar_json}')

        # Chat-Nachricht (immer)
        prefix = "[Ankuendigung] " if banner else ""
        await srv.rcon_command(f'say {prefix}{message}')

    async def _repeat_announcement(
        self, srv: MinecraftServer, message: str,
        count: int, banner_first: bool
    ) -> None:
        """Wiederholt eine Ankuendigung alle 30 Sekunden.

        Die erste Nachricht kann einen Banner enthalten, alle weiteren
        verwenden nur Actionbar + Chat um nicht zu nerven.
        Zählt als Countdown herunter wenn die Nachricht 'Restart' oder
        'Neustart' enthaelt.
        """
        try:
            is_restart = any(
                w in message.lower()
                for w in ["restart", "neustart", "wartung", "maintenance"]
            )

            for i in range(count):
                # Server-Check: Abbrechen wenn offline
                if not await srv.is_running():
                    logger.info(
                        f"[{srv.server_id}] Ankuendigungs-Wiederholung abgebrochen "
                        f"(Server offline)"
                    )
                    break

                remaining = count - i
                if is_restart:
                    # Countdown-Nachricht (Minuten berechnen: remaining * 30s)
                    minutes_left = (remaining * 30) / 60
                    if minutes_left >= 1:
                        countdown_msg = f"{message} (noch {minutes_left:.0f} Min)"
                    else:
                        seconds_left = remaining * 30
                        countdown_msg = f"{message} (noch {seconds_left}s)"
                else:
                    countdown_msg = message

                if i == 0 and banner_first:
                    # Erste Nachricht: Voller Banner
                    await self._send_announcement(srv, countdown_msg, banner=True)
                else:
                    # Folgende: Nur Actionbar + Chat
                    actionbar_json = json.dumps(
                        {"text": countdown_msg, "color": "yellow"}
                    )
                    await srv.rcon_command(f'title @a actionbar {actionbar_json}')
                    await srv.rcon_command(f'say [Ankuendigung] {countdown_msg}')

                # Warten (ausser nach letzter Nachricht)
                if i < count - 1:
                    await asyncio.sleep(30)

            logger.info(
                f"[{srv.server_id}] Wiederholte Ankuendigung abgeschlossen ({count}x)"
            )
        except asyncio.CancelledError:
            logger.info(f"[{srv.server_id}] Ankuendigungs-Wiederholung abgebrochen")
        except Exception as e:
            logger.error(
                f"[{srv.server_id}] Fehler bei Ankuendigungs-Wiederholung: {e}"
            )

    # --- F22: difficulty, weather, time, gamemode entfernt (v3.2.0) ---
    # Diese Gameplay-Commands werden direkt In-Game verwendet.
    # Entfernt: mc_difficulty, mc_weather, mc_time, mc_gamemode

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  CONFIG: /mc config settings | stats | modpack_check (nur Lesen) ║
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

    # --- F25: config set, backup, restore, update ins Web-Dashboard migriert ---

    @config_grp.command(name="stats", description="World-Statistiken anzeigen")
    @app_commands.autocomplete(server=_server_autocomplete)
    async def config_stats(self, interaction: discord.Interaction,
                           server: Optional[str] = None):
        """Zeigt World-Größe, Spielerzahl und Uptime"""
        await interaction.response.defer()

        srv = await self._require_server(interaction, server)
        if not srv:
            return

        embed = discord.Embed(
            title=f"Statistiken — {srv.display_name}",
            color=0x0099ff,
        )

        # World-Größe
        try:
            world_bytes = await srv.get_world_size()
            embed.add_field(
                name="World-Größe",
                value=format_bytes(world_bytes),
                inline=True,
            )
        except Exception:
            embed.add_field(name="World-Größe", value="Nicht verfügbar", inline=True)

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

    # --- F25: config autosave Command ins Web-Dashboard migriert ---
    # (Autosave-Hintergrund-Loop bleibt aktiv, siehe _autosave_loop)

    @config_grp.command(
        name="modpack_check",
        description="Manuell auf Modpack-Updates prüfen"
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
                    color=0xe74c3c,
                )
            elif available:
                embed = discord.Embed(
                    title="Modpack-Update verfügbar!",
                    color=0xf39c12,
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
                    color=0x2ecc71,
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
                "Kein Backup-Manager für diesen Server.", ephemeral=True
            )
            return

        # Backup ermitteln
        backups = await mgr.list_backups(max_results=20)
        if not backups:
            await interaction.followup.send("Keine Backups verfügbar.")
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

        # Größe prüfen
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
                "Backup-Pfad nicht verfügbar.", ephemeral=True
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

            try:
                # ZIP-Größe prüfen
                zip_size = zip_path.stat().st_size
                if zip_size > 25 * 1024 * 1024:
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
            finally:
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
        """Autocomplete für Backup-Namen"""
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

    @mc.command(name="command", description="RCON Befehl ausführen")
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
                color=0xe74c3c,
            )
            await interaction.followup.send(embed=embed)

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  BLACKLIST: /mc blacklist add | remove | list | history        ║
    # ╚════════════════════════════════════════════════════════════════╝

    @blacklist_grp.command(
        name="add", description="Spieler auf die Blacklist setzen (serverübergreifend)"
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
            color=0xe74c3c,
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
            color=0x2ecc71,
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
                f"Keine Ban-Historie für **{discord.utils.escape_mentions(safe_player)}**.",
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
            color=0xf39c12,
        )
        embed.set_footer(text=f"{len(history)} Eintraege gesamt")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  WORLD: /mc world stats                                        ║
    # ╚════════════════════════════════════════════════════════════════╝

    @world_grp.command(name="stats", description="Detaillierte Welt-Analyse anzeigen")
    @app_commands.autocomplete(server=_server_autocomplete)
    async def mc_world_stats(self, interaction: discord.Interaction,
                             server: Optional[str] = None):
        """Analysiert die Minecraft-Welt: level.dat, Spieler-Stats,
        Advancements und Region-Files.
        """
        await interaction.response.defer()

        srv = await self._require_server(interaction, server)
        if not srv:
            return

        await interaction.followup.send(
            f"Analysiere Welt von {srv.display_name}... (kann einige Sekunden dauern)"
        )

        analyzer = WorldAnalyzer(
            world_path=srv.world_path,
            server_path=srv.server_path,
        )

        try:
            results = await analyzer.analyze()
        except Exception as e:
            await interaction.edit_original_response(
                content=f"Analyse fehlgeschlagen: {e}"
            )
            logger.error(f"[{srv.server_id}] World-Analyse fehlgeschlagen: {e}")
            return

        # Ergebnis-Embed erstellen
        embed = discord.Embed(
            title=f"Welt-Analyse — {srv.display_name}",
            color=0x2ecc71,
        )

        # Welt-Info (level.dat)
        world_info = results.get("world_info", {})
        if world_info:
            info_lines = []
            if "mc_version" in world_info:
                info_lines.append(f"**MC-Version:** {world_info['mc_version']}")
            if "level_name" in world_info:
                info_lines.append(f"**Weltname:** {world_info['level_name']}")
            if "seed" in world_info:
                info_lines.append(f"**Seed:** `{world_info['seed']}`")
            if "world_age_days" in world_info:
                info_lines.append(
                    f"**Welt-Alter:** {world_info['world_age_days']} Tage "
                    f"({world_info.get('world_age_hours', '?')} Stunden)"
                )
            if "spawn" in world_info:
                s = world_info["spawn"]
                info_lines.append(
                    f"**Spawn:** X={s.get('x', '?')} Y={s.get('y', '?')} Z={s.get('z', '?')}"
                )
            if "difficulty" in world_info:
                info_lines.append(f"**Schwierigkeit:** {world_info['difficulty']}")
            if "gamemode" in world_info:
                info_lines.append(f"**Spielmodus:** {world_info['gamemode']}")
            if "hardcore" in world_info:
                info_lines.append(
                    f"**Hardcore:** {'Ja' if world_info['hardcore'] else 'Nein'}"
                )
            if info_lines:
                embed.add_field(
                    name="Welt-Info",
                    value="\n".join(info_lines),
                    inline=False,
                )

        # Welt-Größe
        world_size = results.get("world_size", {})
        total = world_size.get("total", {})
        if total:
            size_lines = [f"**Gesamt:** {total.get('size_mb', 0)} MB"]
            for dim_name in ["overworld", "nether", "end"]:
                dim = world_size.get(dim_name, {})
                if dim:
                    dim_label = {
                        "overworld": "Oberwelt",
                        "nether": "Nether",
                        "end": "End"
                    }.get(dim_name, dim_name)
                    size_lines.append(
                        f"**{dim_label}:** {dim['size_mb']} MB — "
                        f"{dim['regions']} Regionen ({dim['explored_km2']} km²)"
                    )
            size_lines.append(
                f"**Erkundete Flaeche:** {total.get('total_explored_km2', 0)} km²"
            )
            embed.add_field(
                name="Welt-Größe",
                value="\n".join(size_lines),
                inline=False,
            )

        # Spieler-Statistiken (Top 5)
        player_stats = results.get("player_stats", [])
        if player_stats:
            stat_lines = []
            for i, p in enumerate(player_stats[:5], 1):
                uuid_short = p["uuid"][:8]
                hours = p.get("play_hours", 0)
                deaths = p.get("deaths", 0)
                kills = p.get("mob_kills", 0)
                dist = p.get("distance_km", {})
                walk_km = dist.get("walk", 0)
                stat_lines.append(
                    f"**{i}.** `{uuid_short}` — "
                    f"{hours}h Spielzeit, {deaths} Tode, "
                    f"{kills} Mob-Kills, {walk_km} km gelaufen"
                )
            embed.add_field(
                name=f"Spieler-Statistiken (Top {min(5, len(player_stats))})",
                value="\n".join(stat_lines),
                inline=False,
            )

        # Advancements (Top 5)
        advancements = results.get("advancements", [])
        if advancements:
            adv_lines = []
            for i, a in enumerate(advancements[:5], 1):
                uuid_short = a["uuid"][:8]
                progress = a.get("progress_percent", 0)
                completed = a.get("completed", 0)
                total_adv = a.get("total", 0)
                last = a.get("last_advancement", "?")
                adv_lines.append(
                    f"**{i}.** `{uuid_short}` — "
                    f"{progress}% ({completed}/{total_adv})"
                )
                if last:
                    adv_lines[-1] += f" — Letztes: {last}"
            embed.add_field(
                name=f"Advancements (Top {min(5, len(advancements))})",
                value="\n".join(adv_lines),
                inline=False,
            )

        # Fehler anzeigen
        errors = results.get("errors", [])
        if errors:
            embed.add_field(
                name="Hinweise",
                value="\n".join(f"⚠️ {e}" for e in errors),
                inline=False,
            )

        embed.set_footer(
            text=f"Analysiert am {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        await interaction.edit_original_response(content=None, embed=embed)

    # ==================================================================
    # Fehlerbehandlung
    # ==================================================================

    async def cog_app_command_error(
        self, interaction: discord.Interaction,
        error: app_commands.AppCommandError
    ) -> None:
        """Zentrale Fehlerbehandlung für alle Commands in dieser Cog"""
        if isinstance(error, app_commands.CheckFailure):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Keine Berechtigung für diesen Befehl.",
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
        except Exception as e:
            logger.debug(f"Exception swallowed (B110-refactor 3.1): {e}")


async def setup(bot):
    """Cog laden"""
    await bot.add_cog(MinecraftCog(bot))
