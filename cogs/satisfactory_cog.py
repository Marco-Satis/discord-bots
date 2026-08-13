"""
Satisfactory Unified Cog - Alle /sat Befehle mit Sub-Gruppen

Phase 14 (F25): Server-Steuerung (start/stop/restart/cancel) und Admin-Config
ins Dashboard migriert. Backup umbenannt in Savegame (/sat sav).

Command-Struktur:
  /sat status                                     (Server-Status - Alle)
  /sat players online|ban|unban|bans              (Spieler-Verwaltung)
  /sat sav save|download|upload|list|restore|load|stats  (Savegame-Verwaltung)
  /sat config settings                            (Einstellungen anzeigen)
  /sat blueprints upload|list|download|delete     (Blueprint-Manager)
  /sat whitelist add|remove|list                  (Whitelist)
  /sat blacklist add|remove|list                  (Blacklist)
"""

import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional  # noqa: F401 (genutzt in Subklassen)
from pathlib import Path

from utils import get_logger, format_uptime, format_bytes, status_emoji
from utils.permissions import admin_only, spieler_only, owner_only, is_admin, server_online_required
from modules.restart_timer import TimerResult
from utils.embeds import COLOR_ERROR, COLOR_INFO, COLOR_SUCCESS, COLOR_WARNING

logger = get_logger("cogs.satisfactory")


def _md(text) -> str:
    """escape_markdown fuer user-kontrollierte Strings (Spielernamen, Session-/
    Save-/Ban-Namen, Grund) in Embeds — verhindert dass *, _, ~, `, | das
    Embed-Format brechen oder Markdown-Injection erlauben."""
    return discord.utils.escape_markdown(str(text))


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
    sav_grp = app_commands.Group(
        name="sav", parent=sat, description="Savegame-Verwaltung"
    )
    config_grp = app_commands.Group(
        name="config", parent=sat, description="Server-Einstellungen (nur Lesen)"
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

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.server = bot.sat_server
        self.api = bot.sat_api
        self.timer_mgr = bot.timer_mgr

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  CORE: /sat status                                           ║
    # ╚════════════════════════════════════════════════════════════════╝

    @sat.command(name="status", description="Server-Status anzeigen")
    async def sat_status(self, interaction: discord.Interaction):
        await interaction.response.defer()

        status = await self.server.get_status()
        online = status["running"]

        embed = discord.Embed(
            title=f"{status_emoji(online)} Satisfactory Server",
            color=COLOR_SUCCESS if online else 0xe74c3c,
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
                        name="Session", value=_md(state.active_session), inline=True
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
                value=f"{active_timer.action_name} läuft...",
                inline=False,
            )

        await interaction.followup.send(embed=embed)

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
                color=COLOR_INFO,
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
                            f"*(Detaillierte Spielerliste nicht verfügbar)*"
                        )
                except Exception:
                    embed.description = (
                        f"{state.num_players} Spieler online.\n"
                        f"*(Spielerliste über API nicht verfügbar)*"
                    )

            embed.add_field(
                name="Session", value=_md(state.active_session) if state.active_session else "\u2014", inline=True
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
    @app_commands.describe(player="Name des Spielers", reason="Grund für den Ban")
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

        # Phase 1b: SaveGame vor Ban — Returnwert auswerten. save_game() faengt
        # Fehler intern ab und gibt bei Timeout/Fehler False zurueck (raised nicht),
        # darum reicht das try/except allein nicht. Ban wird NICHT blockiert, aber
        # bei fehlgeschlagenem Save im Embed sichtbar markiert (sonst Ban evtl.
        # nicht im aktuellen Save persistiert).
        save_ok = False
        try:
            save_ok = await self.api.save_game()
            await asyncio.sleep(3)
        except Exception as e:
            logger.warning(f"Save before ban failed (continuing): {e}")
        if not save_ok:
            logger.warning(
                f"SaveGame vor Ban von {player} fehlgeschlagen/Timeout — Ban laeuft trotzdem"
            )

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
                color=COLOR_ERROR,
            )
            if not save_ok:
                embed.add_field(
                    name="⚠️ Hinweis",
                    value="Server-Save vor dem Ban schlug fehl/Timeout — Ban evtl. nicht im aktuellen Save.",
                    inline=False,
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
                color=COLOR_SUCCESS,
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
            color=COLOR_ERROR,
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
                    f"\u2022 **{_md(name)}** ({ip})\n"
                    f"   Grund: {_md(reason)}\n"
                    f"   Datum: {date} | gebannt von: {_md(banned_by)}"
                )
            embed.description = "\n".join(entries)

        await interaction.followup.send(embed=embed)

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  SAVEGAME: /sat sav save|download|list|restore|load|stats    ║
    # ╚════════════════════════════════════════════════════════════════╝

    @sav_grp.command(name="save", description="Spiel speichern via API")
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
                    color=COLOR_SUCCESS,
                )
                embed.set_footer(text=f"von {interaction.user.display_name}")
                await interaction.followup.send(embed=embed)
            else:
                await interaction.followup.send("Speichern fehlgeschlagen!")
        except Exception as e:
            await interaction.followup.send(f"Fehler: {e}")

    @sav_grp.command(
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
                    f"Savegame zu gross für Discord ({latest['size_human']}). "
                    f"Max. 25 MB.\nDatei: `{save_path.name}`"
                )
                return

            file = discord.File(save_path, filename=save_path.name)
            embed = discord.Embed(
                title="Savegame Download",
                description=f"**{_md(latest['name'])}**",
                color=COLOR_INFO,
            )
            embed.add_field(
                name="Größe", value=latest["size_human"], inline=True
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

    @sav_grp.command(name="list", description="Alle Backups auflisten")
    @spieler_only()
    async def backup_list(self, interaction: discord.Interaction):
        await interaction.response.defer()

        backups = self.bot.backup_mgr.list_backups(max_results=20)

        if not backups:
            await interaction.followup.send("Keine Backups vorhanden.")
            return

        embed = discord.Embed(
            title=f"Backups ({len(backups)})", color=COLOR_INFO
        )

        entries = []
        for i, bp in enumerate(backups[:15], 1):
            created = bp.get("created_at", "?")[:16].replace("T", " ")
            typ = "Auto" if bp.get("type") == "auto" else "Manuell"
            entries.append(
                f"`{i}.` **{_md(bp['name'])}**\n"
                f"   {bp.get('size_human', '?')} | {created} | {typ} | {bp.get('created_by', '?')}"
            )

        embed.description = "\n".join(entries)
        total = format_bytes(self.bot.backup_mgr.total_size())
        embed.set_footer(
            text=f"Gesamt: {total} | Max: {self.bot.backup_mgr.max_backups}"
        )
        await interaction.followup.send(embed=embed)

    @sav_grp.command(
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
                "Server muss offline sein für ein Restore!\n"
                "Stoppe den Server zuerst über das Dashboard."
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
                f"Größe: {bp.get('size_human', '?')}\n"
                f"Erstellt: {bp.get('created_at', '?')[:16]}\n\n"
                f"**ACHTUNG: Aktuelle Savegames werden überschrieben!**\n"
                f"*(Ein Pre-Restore Backup wird automatisch erstellt)*"
            ),
            color=COLOR_ERROR,
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
    # ║  CONFIG: /sat config settings (nur Lesen)                    ║
    # ╚════════════════════════════════════════════════════════════════╝

    @config_grp.command(
        name="settings", description="Servereinstellungen anzeigen"
    )
    @spieler_only()
    async def config_settings(self, interaction: discord.Interaction):
        await interaction.response.defer()

        embed = discord.Embed(
            title="Satisfactory Servereinstellungen", color=COLOR_INFO
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
                name="Session", value=_md(state.active_session) if state.active_session else "\u2014", inline=True
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

    @sav_grp.command(
        name="load", description="Savegame laden (Owner)"
    )
    @app_commands.describe(savename="Name des Savegames")
    @owner_only()
    @server_online_required("server")
    async def sav_load(
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
            color=COLOR_WARNING,
        )
        await interaction.followup.send(embed=embed, view=view)

    @sav_load.autocomplete("savename")
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

    @sav_grp.command(
        name="stats", description="Savegame-Statistiken anzeigen"
    )
    @spieler_only()
    async def sav_stats(self, interaction: discord.Interaction):
        await interaction.response.defer()

        try:
            stats_mgr = self.bot.savegame_stats
            stats = await stats_mgr.analyze()

            if stats.get("error"):
                await interaction.followup.send(stats["error"])
                return

            embed = discord.Embed(
                title=f"Savegame: {_md(stats.get('name', 'Unbekannt'))}",
                color=COLOR_INFO,
            )
            embed.add_field(
                name="Größe", value=stats.get("size", "?"), inline=True
            )
            embed.add_field(
                name="Letzte Aenderung",
                value=stats.get("last_modified", "?"),
                inline=True,
            )

            if stats.get("session_name"):
                embed.add_field(
                    name="Session", value=_md(stats["session_name"]), inline=True
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

    @sav_grp.command(
        name="upload",
        description="Savegame hochladen (.sav Datei)",
    )
    @app_commands.describe(
        datei="Savegame-Datei (.sav)",
    )
    @owner_only()
    async def sav_upload(
        self,
        interaction: discord.Interaction,
        datei: discord.Attachment,
    ):
        await interaction.response.defer()

        # Nur .sav Dateien erlauben
        if not datei.filename.lower().endswith(".sav"):
            await interaction.followup.send(
                "Nur `.sav` Dateien sind erlaubt!"
            )
            return

        # Größen-Check (max. 500 MB)
        max_size = 500 * 1024 * 1024
        if datei.size > max_size:
            await interaction.followup.send(
                f"Datei zu gross ({format_bytes(datei.size)})! "
                f"Maximale Größe: {format_bytes(max_size)}"
            )
            return

        try:
            savegame_dir = self.bot.savegame_stats.savegame_path / "server"
            savegame_dir.mkdir(parents=True, exist_ok=True)
            target_path = savegame_dir / datei.filename

            # Pruefen ob Datei bereits existiert
            exists = target_path.exists()

            # Bestaetigungs-View anzeigen
            view = UploadConfirmView(
                self, interaction, datei, target_path, exists
            )
            # escape_markdown: Filename koennte *, _, ~, ` enthalten -> Embed-Format brechen.
            safe_filename = discord.utils.escape_markdown(datei.filename)
            desc = f"Savegame **{safe_filename}** hochladen?\n\n"
            desc += f"Größe: {format_bytes(datei.size)}\n"
            desc += f"Ziel: `{savegame_dir.name}/{safe_filename}`\n"
            if exists:
                existing_size = target_path.stat().st_size
                desc += (
                    f"\n**Datei existiert bereits** "
                    f"({format_bytes(existing_size)}) — wird überschrieben!"
                )

            embed = discord.Embed(
                title="Savegame Upload bestaetigen",
                description=desc,
                color=COLOR_WARNING if exists else 0x3498db,
            )
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.followup.send(embed=embed, view=view)

        except Exception as e:
            logger.error(f"Savegame upload prep failed: {e}")
            await interaction.followup.send(f"Upload fehlgeschlagen: {e}")

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  BLUEPRINTS: /sat blueprints upload|list|download|delete     ║
    # ╚════════════════════════════════════════════════════════════════╝

    @blueprints_grp.command(
        name="upload",
        description="Blueprint(s) hochladen (.sbp+.sbpcfg oder .zip)",
    )
    @app_commands.describe(
        kategorie="Kategorie für den Blueprint",
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
                description="\n".join(f"\u2022 {_md(n)}" for n in added),
                color=COLOR_SUCCESS,
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
            description="\n".join(f"\u2022 **{_md(n)}**" for n in added_names),
            color=COLOR_SUCCESS,
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
        name="list", description="Alle Blueprints auf dem Server anzeigen"
    )
    @spieler_only()
    async def blueprints_list(
        self,
        interaction: discord.Interaction,
    ):
        await interaction.response.defer()

        mgr = self.bot.blueprint_mgr
        blueprints = mgr.list_from_filesystem()

        if not blueprints:
            await interaction.followup.send("Keine Blueprints auf dem Server vorhanden.")
            return

        active_world = mgr._active_world or "?"

        # Seiten erstellen
        pages = []
        page_size = 15
        for page_start in range(0, len(blueprints), page_size):
            page_bps = blueprints[page_start:page_start + page_size]
            entries = []
            for i, bp in enumerate(page_bps, page_start + 1):
                size = format_bytes(bp.get("size_bytes", 0))
                cfg_ok = "\u2705" if bp["has_cfg"] else "\u274c"
                entries.append(
                    f"`{i}.` {cfg_ok} **{bp['name']}**  \u2014  {size}"
                )
            pages.append("\n".join(entries))

        view = BlueprintListView(pages, len(blueprints), active_world, interaction.user.id)
        embed = view.build_embed(0)
        await interaction.followup.send(embed=embed, view=view)

    @blueprints_grp.command(
        name="download",
        description="Blueprint(s) herunterladen — Einzel, Mehrfach (1,3,5), Bereich (1-5) oder Name",
    )
    @app_commands.describe(
        blueprint="Nummern: 3 | 1,3,5 | 1-5 | 1,3-7 | oder Name (lädt automatisch .sbp + .sbpcfg)"
    )
    @spieler_only()
    async def blueprints_download(
        self, interaction: discord.Interaction, blueprint: str
    ):
        await interaction.response.defer()

        all_bps = self.bot.blueprint_mgr.list_from_filesystem()
        input_str = blueprint.strip()

        # Auswahl aufloesen (gleiche Logik wie delete): Nummern/Bereiche/Name
        names_to_get: list[str] = []
        has_numbers = any(c.isdigit() for c in input_str) and not input_str.endswith(".sbp")

        if has_numbers and (
            "," in input_str or "-" in input_str or input_str.isdigit()
        ):
            indices: set[int] = set()
            for part in input_str.split(","):
                part = part.strip()
                if "-" in part:
                    try:
                        start, end = part.split("-", 1)
                        for n in range(int(start), int(end) + 1):
                            indices.add(n)
                    except ValueError:
                        pass
                elif part.isdigit():
                    indices.add(int(part))

            invalid = []
            for idx in sorted(indices):
                if 1 <= idx <= len(all_bps):
                    names_to_get.append(all_bps[idx - 1]["name"])
                else:
                    invalid.append(str(idx))

            if invalid:
                await interaction.followup.send(
                    f"Ungueltige Nummern: {', '.join(invalid)} "
                    f"(es gibt {len(all_bps)} Blueprints)."
                )
                if not names_to_get:
                    return
        else:
            # Komma-getrennte Namen oder einzelner Name
            for part in input_str.split(","):
                part = part.strip()
                if part:
                    names_to_get.append(part)

        if not names_to_get:
            await interaction.followup.send("Keine Blueprints zum Download angegeben.")
            return

        # Dateien sammeln (jeweils .sbp + .sbpcfg)
        collected: list[tuple[str, Path, Path]] = []  # (name, sbp, cfg)
        not_found: list[str] = []
        for name in names_to_get:
            sbp_path, cfg_path = self.bot.blueprint_mgr.get_files(name)
            if not sbp_path:
                not_found.append(name)
                continue
            collected.append((name, sbp_path, cfg_path))

        if not collected:
            await interaction.followup.send(
                f"Keine Blueprints gefunden: {', '.join(not_found)}"
            )
            return

        # Discord-Limit: max 10 Dateien pro Nachricht. Jeder Blueprint = 2 Dateien
        # → max 5 Blueprints pro Nachricht. Bei mehr: in Batches senden.
        BP_PER_MSG = 5
        total_size = 0
        sent_names: list[str] = []

        try:
            for batch_start in range(0, len(collected), BP_PER_MSG):
                batch = collected[batch_start:batch_start + BP_PER_MSG]
                files = []
                batch_names = []
                batch_size = 0
                for name, sbp_path, cfg_path in batch:
                    files.append(discord.File(sbp_path, filename=sbp_path.name))
                    batch_size += sbp_path.stat().st_size
                    if cfg_path and cfg_path.exists():
                        files.append(discord.File(cfg_path, filename=cfg_path.name))
                        batch_size += cfg_path.stat().st_size
                    batch_names.append(name)

                total_size += batch_size
                sent_names.extend(batch_names)

                title = (
                    f"Blueprint: {batch_names[0]}"
                    if len(batch_names) == 1
                    else f"{len(batch_names)} Blueprints"
                )
                embed = discord.Embed(title=title, color=COLOR_INFO)
                embed.add_field(
                    name="Enthalten",
                    value="\n".join(f"• {n}" for n in batch_names)[:1024],
                    inline=False,
                )
                embed.add_field(
                    name="Größe", value=format_bytes(batch_size), inline=True
                )
                embed.add_field(
                    name="Dateien", value=str(len(files)), inline=True
                )
                embed.set_footer(
                    text="Je .sbp + .sbpcfg in den Blueprint-Ordner kopieren"
                )
                await interaction.followup.send(embed=embed, files=files)

            if not_found:
                await interaction.followup.send(
                    f"Nicht gefunden: {', '.join(not_found)}", ephemeral=True
                )

            logger.info(
                f"Blueprints downloaded: {', '.join(sent_names)} "
                f"({format_bytes(total_size)}) by {interaction.user}"
            )
        except Exception as e:
            await interaction.followup.send(f"Download fehlgeschlagen: {e}")

    @blueprints_download.autocomplete("blueprint")
    async def bp_download_autocomplete(
        self, interaction: discord.Interaction, current: str
    ):
        bps = self.bot.blueprint_mgr.list_from_filesystem()
        choices = []
        # Letztes Komma-Segment fuer Filter nutzen (Multi-Select-Tippen)
        prefix = ""
        filter_term = current
        if "," in current:
            prefix = current.rsplit(",", 1)[0] + ","
            filter_term = current.rsplit(",", 1)[1].strip()

        for i, bp in enumerate(bps, 1):
            name = bp["name"]
            if filter_term and filter_term.lower() not in name.lower() and filter_term != str(i):
                continue
            # Nur Name anzeigen; Value behaelt bestehende Auswahl + neue Nummer
            value = f"{prefix}{i}" if prefix else str(i)
            choices.append(
                app_commands.Choice(
                    name=name[:100],
                    value=value[:100],
                )
            )
        return choices[:25]

    @blueprints_grp.command(
        name="delete", description="Blueprints löschen (Einzel, Mehrfach, Bereich)"
    )
    @app_commands.describe(
        blueprint="Nummern: 3 | 1,3,5 | 1-5 | 1,3-7,12 | oder Name"
    )
    @spieler_only()
    async def blueprints_delete(
        self, interaction: discord.Interaction, blueprint: str
    ):
        await interaction.response.defer()

        all_bps = self.bot.blueprint_mgr.list_from_filesystem()
        input_str = blueprint.strip()

        # Namen aufloesen
        names_to_delete = []
        has_numbers = any(c.isdigit() for c in input_str) and not input_str.endswith(".sbp")

        if has_numbers and (
            "," in input_str or "-" in input_str or input_str.isdigit()
        ):
            # Nummern-Eingabe parsen: 1,3,5 oder 1-5 oder 1,3-7,12
            indices = set()
            for part in input_str.split(","):
                part = part.strip()
                if "-" in part:
                    try:
                        start, end = part.split("-", 1)
                        for n in range(int(start), int(end) + 1):
                            indices.add(n)
                    except ValueError:
                        pass
                elif part.isdigit():
                    indices.add(int(part))

            invalid = []
            for idx in sorted(indices):
                if 1 <= idx <= len(all_bps):
                    names_to_delete.append(all_bps[idx - 1]["name"])
                else:
                    invalid.append(str(idx))

            if invalid:
                await interaction.followup.send(
                    f"Ungueltige Nummern: {', '.join(invalid)} "
                    f"(es gibt {len(all_bps)} Blueprints)."
                )
                if not names_to_delete:
                    return
        else:
            # Einzelner Name
            names_to_delete.append(input_str)

        if not names_to_delete:
            await interaction.followup.send("Keine Blueprints zum Loeschen angegeben.")
            return

        # Loeschen mit Bestaetigungs-View bei Mehrfachauswahl
        if len(names_to_delete) > 1:
            view = BlueprintDeleteConfirmView(
                self, interaction, names_to_delete
            )
            desc = f"**{len(names_to_delete)} Blueprints** löschen?\n\n"
            desc += "\n".join(
                f"\u2022 {n}" for n in names_to_delete[:30]
            )
            if len(names_to_delete) > 30:
                desc += f"\n... und {len(names_to_delete) - 30} weitere"

            embed = discord.Embed(
                title="Loeschen bestaetigen",
                description=desc,
                color=COLOR_ERROR,
            )
            await interaction.followup.send(embed=embed, view=view)
        else:
            # Einzeln direkt löschen (mit Rechte-Pruefung)
            name = names_to_delete[0]
            success, msg = await self.bot.blueprint_mgr.delete(
                name, interaction.user.id, is_admin(interaction)
            )
            if success:
                embed = discord.Embed(
                    title="Blueprint gelöscht",
                    description=f"**{_md(name)}** wurde gelöscht (.sbp + .sbpcfg).",
                    color=COLOR_ERROR,
                )
                embed.set_footer(text=f"von {interaction.user.display_name}")
                await interaction.followup.send(embed=embed)
                logger.info(
                    f"Blueprint deleted: {name} by {interaction.user}"
                )
            else:
                await interaction.followup.send(msg)

    @blueprints_delete.autocomplete("blueprint")
    async def bp_delete_autocomplete(
        self, interaction: discord.Interaction, current: str
    ):
        bps = self.bot.blueprint_mgr.list_from_filesystem()
        choices = []
        for i, bp in enumerate(bps, 1):
            label = f"{i}. {bp['name']}"
            if current and current.lower() not in label.lower() and current != str(i):
                continue
            choices.append(
                app_commands.Choice(
                    name=label[:100],
                    value=str(i),
                )
            )
        return choices[:25]

    @blueprints_grp.command(
        name="migrate",
        description="[Owner] Alle Blueprints von einer Welt in eine andere migrieren + Server-Neustart",
    )
    @app_commands.describe(
        von="Quell-Welt (alte Welt)",
        nach="Ziel-Welt (neue Welt)",
    )
    @owner_only()
    async def blueprints_migrate(
        self,
        interaction: discord.Interaction,
        von: str,
        nach: str,
    ):
        await interaction.response.defer()

        von = von.strip()
        nach = nach.strip()

        if von == nach:
            await interaction.followup.send("Quell- und Ziel-Welt sind identisch.")
            return

        # Kategorisierung: new / identical (Hash gleich) / conflict (Hash anders)
        cat = self.bot.blueprint_mgr.categorize_migration(von, nach)
        if cat.get("error"):
            await interaction.followup.send(f"Migration nicht möglich: {cat['error']}")
            return

        new_bps = cat["new"]
        identical = cat["identical"]
        conflict = cat["conflict"]

        if not new_bps and not identical and not conflict:
            await interaction.followup.send(
                f"Keine Blueprints von '{von}' nach '{nach}' gefunden "
                f"(Quelle leer)."
            )
            return

        # NEUE Blueprints sofort kopieren
        copied: list[str] = []
        copy_errors: list[str] = []
        if new_bps:
            copied, _, copy_errors = self.bot.blueprint_mgr.migrate_world(
                von, nach, overwrite=False, only_names=new_bps
            )

        embed = discord.Embed(
            title="Blueprint-Migration",
            description=f"**{von}** → **{nach}**",
            color=COLOR_SUCCESS if not (conflict or copy_errors) else 0xf39c12,
        )
        if copied:
            embed.add_field(
                name=f"Neu kopiert ({len(copied)})",
                value="\n".join(f"• {n}" for n in copied[:20])[:1024]
                + ("\n…" if len(copied) > 20 else ""),
                inline=False,
            )
        if identical:
            embed.add_field(
                name=f"Identisch — übersprungen ({len(identical)})",
                value="\n".join(f"• {n}" for n in identical[:20])[:1024]
                + ("\n…" if len(identical) > 20 else ""),
                inline=False,
            )
        if conflict:
            embed.add_field(
                name=f"⚠ Konflikt — gleicher Name, anderer Inhalt ({len(conflict)})",
                value="\n".join(f"• {n}" for n in conflict[:20])[:1024]
                + ("\n…" if len(conflict) > 20 else ""),
                inline=False,
            )
        if copy_errors:
            embed.add_field(
                name=f"Fehler ({len(copy_errors)})",
                value="\n".join(copy_errors[:5])[:1024],
                inline=False,
            )

        logger.info(
            f"Blueprint-Migration durch {interaction.user}: {von} -> {nach} "
            f"({len(copied)} neu, {len(identical)} identisch, "
            f"{len(conflict)} Konflikt, {len(copy_errors)} Fehler)"
        )

        # Bei Konflikten: Button-Entscheidung (Überschreiben / Löschen / Behalten)
        if conflict:
            embed.set_footer(
                text="Konflikte: gleicher Name, anderer Inhalt. "
                "Überschreiben, aus Ziel löschen, oder behalten?"
            )
            view = MigrateConflictView(self, interaction, von, nach, conflict)
            await interaction.followup.send(embed=embed, view=view)
            return

        # Keine Konflikte → direkt Restart (Save-vor-Restart)
        embed.set_footer(text="Server wird jetzt direkt neugestartet …")
        await interaction.followup.send(embed=embed)
        await self._sat_save_restart(
            interaction.channel,
            f"Migrierte Blueprints aus '{nach}' sind jetzt verfügbar!",
        )

    async def _sat_save_restart(self, channel, success_desc: str) -> None:
        """Save-vor-Restart + Discord-Feedback. Shared von migrate + View."""
        har = getattr(self.bot, "health_auto_restart", None)
        if har:
            har.suppress("sat", "main", duration_seconds=300)
        success, msg = await self.server.restart(api=self.api)
        if channel is None:
            return
        if success:
            await channel.send(
                embed=discord.Embed(
                    title="Server neugestartet",
                    description=success_desc,
                    color=COLOR_SUCCESS,
                )
            )
        else:
            await channel.send(f"Restart fehlgeschlagen: {msg}")

    @blueprints_migrate.autocomplete("von")
    async def bp_migrate_von_autocomplete(
        self, interaction: discord.Interaction, current: str
    ):
        return self._world_choices(current)

    @blueprints_migrate.autocomplete("nach")
    async def bp_migrate_nach_autocomplete(
        self, interaction: discord.Interaction, current: str
    ):
        return self._world_choices(current)

    def _world_choices(self, current: str) -> list:
        """Autocomplete-Choices fuer Welt-Ordner (mit Blueprint-Count)."""
        worlds = self.bot.blueprint_mgr.get_worlds()
        choices = []
        for w in worlds:
            label = f"{w['name']} ({w['blueprint_count']} BP)"
            if w.get("is_active"):
                label += " — aktiv"
            if current and current.lower() not in w["name"].lower():
                continue
            choices.append(
                app_commands.Choice(name=label[:100], value=w["name"][:100])
            )
        return choices[:25]

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
            title=f"Whitelist ({len(players)} Spieler)", color=COLOR_SUCCESS
        )
        embed.add_field(
            name="Status",
            value="Aktiviert" if self.bot.whitelist_mgr.enabled else "Deaktiviert",
            inline=False,
        )

        if players:
            entries = []
            for p in players[:25]:
                entries.append(f"\u2022 **{_md(p['name'])}** \u2014 von {_md(p['added_by'])}")
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
        player="Name des Spielers", reason="Grund für den Ban"
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
                title="Spieler zur Blacklist hinzugefuegt", color=COLOR_ERROR
            )
            embed.add_field(name="Spieler", value=_md(player), inline=True)
            embed.add_field(name="Grund", value=_md(reason), inline=True)
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
            title=f"Blacklist ({len(players)} Spieler)", color=COLOR_ERROR
        )

        if players:
            entries = []
            for p in players[:25]:
                entries.append(
                    f"\u2022 **{_md(p['name'])}** \u2014 {_md(p.get('reason', 'N/A'))} "
                    f"(von {_md(p['banned_by'])})"
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
                    "Keine Berechtigung für diesen Befehl.", ephemeral=True
                )
            return
        logger.error(f"Command error in {interaction.command.name if interaction.command else 'unknown'}: {error}", exc_info=True)
        try:
            msg = f"Ein Fehler ist aufgetreten: {error}"
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception as e:
            logger.debug(f"Exception swallowed (B110-refactor 3.1): {e}")


# ══════════════════════════════════════════════════════════════════════
# UI Views
# ══════════════════════════════════════════════════════════════════════


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
                color=COLOR_SUCCESS,
            )
            embed.set_footer(text="Starte den Server über das Dashboard")
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
                    f"**{_md(self.savename)}** wird geladen.\n"
                    f"Spieler werden kurzzeitig getrennt."
                ),
                color=COLOR_SUCCESS,
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
                "Nur Admins können den Server neustarten.",
                ephemeral=True,
            )
            return

        if self.cog.bot.timer_mgr.has_active:
            await interaction.response.send_message(
                "Es läuft bereits ein Timer.",
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
            # Health-Check unterdruecken waehrend Restart
            har = getattr(self.cog.bot, "health_auto_restart", None)
            if har:
                har.suppress("sat", "main", duration_seconds=300)
            # Save-vor-Restart: api durchreichen → SaveGame + Flush vor Restart
            success, msg = await self.cog.server.restart(api=self.cog.api)
            if success:
                embed = discord.Embed(
                    title="Server neugestartet",
                    description="Blueprints sind jetzt verfügbar!",
                    color=COLOR_SUCCESS,
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
            "Blueprints sind nach dem nächsten Server-Restart verfügbar.",
            ephemeral=True,
        )


class MigrateConflictView(discord.ui.View):
    """Entscheidung bei Migrations-Konflikten (gleicher Name, anderer Inhalt).
    Drei Wege: Überschreiben (Quelle gewinnt), Löschen (aus Ziel entfernen),
    Behalten (Ziel bleibt). Alle lösen anschliessend Save-vor-Restart aus."""

    def __init__(self, cog, interaction, von: str, nach: str, conflict: list):
        super().__init__(timeout=180)
        self.cog = cog
        self.original_user = interaction.user.id
        self.von = von
        self.nach = nach
        self.conflict = conflict

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_user:
            await interaction.response.send_message(
                "Nur der Auslöser kann entscheiden.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(
        label="Überschreiben + Restart",
        style=discord.ButtonStyle.danger,
        emoji="♻️",
    )
    async def overwrite(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not await self._guard(interaction):
            return
        await interaction.response.edit_message(view=None)

        copied, _, errors = self.cog.bot.blueprint_mgr.migrate_world(
            self.von, self.nach, overwrite=True, only_names=self.conflict
        )
        desc = f"**{len(copied)}** Konflikte mit Quell-Version überschrieben."
        if errors:
            desc += f"\n{len(errors)} Fehler: " + "; ".join(errors[:3])
        await interaction.followup.send(
            embed=discord.Embed(
                title="Überschrieben", description=desc, color=COLOR_WARNING
            )
        )
        logger.info(
            f"Migration OVERWRITE durch {interaction.user}: "
            f"{self.von} -> {self.nach} ({len(copied)} überschrieben)"
        )
        await self.cog._sat_save_restart(
            interaction.channel,
            f"Überschriebene Blueprints aus '{self.nach}' sind jetzt verfügbar!",
        )

    @discord.ui.button(
        label="Aus Ziel löschen + Restart",
        style=discord.ButtonStyle.danger,
        emoji="🗑",
    )
    async def delete(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not await self._guard(interaction):
            return
        await interaction.response.edit_message(view=None)

        deleted, errors = self.cog.bot.blueprint_mgr.delete_from_world(
            self.nach, self.conflict
        )
        desc = f"**{deleted}** Konflikt-Blueprints aus '{self.nach}' gelöscht."
        if errors:
            desc += f"\n{len(errors)} Fehler: " + "; ".join(errors[:3])
        await interaction.followup.send(
            embed=discord.Embed(
                title="Gelöscht", description=desc, color=COLOR_ERROR
            )
        )
        logger.info(
            f"Migration DELETE durch {interaction.user}: "
            f"{deleted} aus '{self.nach}' gelöscht"
        )
        await self.cog._sat_save_restart(
            interaction.channel,
            f"Konflikte aus '{self.nach}' entfernt — Server neugestartet.",
        )

    @discord.ui.button(
        label="Behalten + Restart",
        style=discord.ButtonStyle.secondary,
        emoji="\U0001f512",
    )
    async def keep(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not await self._guard(interaction):
            return
        await interaction.response.edit_message(view=None)
        await interaction.followup.send(
            embed=discord.Embed(
                title="Behalten",
                description="Ziel-Versionen bleiben unverändert.",
                color=COLOR_SUCCESS,
            )
        )
        logger.info(
            f"Migration KEEP durch {interaction.user}: {self.von} -> {self.nach}"
        )
        await self.cog._sat_save_restart(
            interaction.channel,
            f"Blueprints in '{self.nach}' sind jetzt verfügbar!",
        )


class UploadConfirmView(discord.ui.View):
    """Confirmation for uploading a savegame file"""

    def __init__(self, cog, interaction, attachment, target_path, exists):
        super().__init__(timeout=60)
        self.cog = cog
        self.original_interaction = interaction
        self.attachment = attachment
        self.target_path = target_path
        self.exists = exists

    @discord.ui.button(
        label="Ja, hochladen", style=discord.ButtonStyle.danger
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
            content=f"Lade `{self.attachment.filename}` hoch...",
            embed=None,
            view=None,
        )

        try:
            import shutil

            # Datei von Discord herunterladen
            file_data = await self.attachment.read()

            # Backup der existierenden Datei erstellen
            backed_up = False
            if self.exists:
                backup_path = self.target_path.with_suffix(".sav.bak")
                shutil.copy2(self.target_path, backup_path)
                backed_up = True
                logger.info(f"Backup erstellt: {backup_path.name}")

            # Savegame direkt schreiben
            self.target_path.write_bytes(file_data)

            embed = discord.Embed(
                title="Savegame hochgeladen",
                description=(
                    f"**{self.attachment.filename}** wurde erfolgreich hochgeladen.\n\n"
                    f"Größe: {format_bytes(len(file_data))}\n"
                    f"Ziel: `{self.target_path.parent.name}/{self.target_path.name}`"
                ),
                color=COLOR_SUCCESS,
            )
            if backed_up:
                embed.add_field(
                    name="Backup",
                    value=f"Vorherige Version gesichert als `{self.target_path.stem}.sav.bak`",
                    inline=False,
                )
            embed.set_footer(
                text=f"von {interaction.user.display_name}"
            )

            await interaction.edit_original_response(
                content=None, embed=embed
            )
            logger.info(
                f"Savegame uploaded by {interaction.user}: "
                f"{self.attachment.filename} ({len(file_data)} bytes)"
            )

        except Exception as e:
            logger.error(f"Savegame upload failed: {e}")
            await interaction.edit_original_response(
                content=f"Upload fehlgeschlagen: {e}"
            )

    @discord.ui.button(
        label="Abbrechen", style=discord.ButtonStyle.secondary
    )
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.edit_message(
            content="Upload abgebrochen.", embed=None, view=None
        )


class BlueprintListView(discord.ui.View):
    """Paginierte Blueprint-Liste mit Vor/Zurueck-Buttons"""

    def __init__(self, pages, total_count, active_world, user_id):
        super().__init__(timeout=120)
        self.pages = pages
        self.total_count = total_count
        self.active_world = active_world
        self.user_id = user_id
        self.current_page = 0
        self._update_buttons()

    def build_embed(self, page: int) -> discord.Embed:
        embed = discord.Embed(
            title=f"Blueprints ({self.total_count}) \u2014 Welt: {self.active_world}",
            description=self.pages[page],
            color=COLOR_INFO,
        )
        embed.set_footer(
            text=f"Seite {page + 1}/{len(self.pages)} | "
                 f"Gesamt: {self.total_count} Blueprints"
        )
        return embed

    def _update_buttons(self):
        self.prev_btn.disabled = self.current_page == 0
        self.next_btn.disabled = self.current_page >= len(self.pages) - 1

    @discord.ui.button(label="\u25c0 Zurueck", style=discord.ButtonStyle.secondary)
    async def prev_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Nur der Ersteller kann blaettern.", ephemeral=True
            )
            return
        self.current_page = max(0, self.current_page - 1)
        self._update_buttons()
        await interaction.response.edit_message(
            embed=self.build_embed(self.current_page), view=self
        )

    @discord.ui.button(label="Weiter \u25b6", style=discord.ButtonStyle.secondary)
    async def next_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Nur der Ersteller kann blaettern.", ephemeral=True
            )
            return
        self.current_page = min(len(self.pages) - 1, self.current_page + 1)
        self._update_buttons()
        await interaction.response.edit_message(
            embed=self.build_embed(self.current_page), view=self
        )


class BlueprintDeleteConfirmView(discord.ui.View):
    """Bestaetigungs-View für Mehrfach-Blueprint-Loeschung"""

    def __init__(self, cog, interaction: discord.Interaction, names: list[str]):
        super().__init__(timeout=60)
        self.cog = cog
        self.user_id = interaction.user.id
        self.names = names

    @discord.ui.button(label="Ja, löschen", style=discord.ButtonStyle.danger, emoji="\U0001f5d1")
    async def confirm_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Nur der Ersteller kann bestaetigen.", ephemeral=True
            )
            return

        await interaction.response.defer()
        deleted = []
        failed = []

        for name in self.names:
            success, msg = await self.cog.bot.blueprint_mgr.delete(
                name, interaction.user.id, is_admin(interaction)
            )
            if success:
                deleted.append(name)
            else:
                failed.append(f"{name}: {msg}")

        # Ergebnis-Embed
        desc = ""
        if deleted:
            desc += f"**{len(deleted)} gelöscht:**\n"
            desc += "\n".join(f"\u2705 {n}" for n in deleted[:30])
            if len(deleted) > 30:
                desc += f"\n... und {len(deleted) - 30} weitere"
        if failed:
            if desc:
                desc += "\n\n"
            desc += f"**{len(failed)} fehlgeschlagen:**\n"
            desc += "\n".join(f"\u274c {f}" for f in failed[:10])

        embed = discord.Embed(
            title="Blueprints gelöscht",
            description=desc,
            color=COLOR_ERROR if not failed else 0xe67e22,
        )
        embed.set_footer(text=f"von {interaction.user.display_name}")

        logger.info(
            f"Bulk blueprint delete: {len(deleted)} OK, {len(failed)} failed "
            f"by {interaction.user}"
        )

        self.stop()
        await interaction.followup.edit_message(
            message_id=interaction.message.id,
            embed=embed, view=None
        )

    @discord.ui.button(label="Abbrechen", style=discord.ButtonStyle.secondary)
    async def cancel_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Nur der Ersteller kann abbrechen.", ephemeral=True
            )
            return
        self.stop()
        await interaction.response.edit_message(
            content="Loeschen abgebrochen.", embed=None, view=None
        )


async def setup(bot):
    await bot.add_cog(SatisfactoryCog(bot))
