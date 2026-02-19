"""
General Commands Cog
/help, /server, /ping, /reload, /clear
"""

import asyncio

import discord
import psutil
from datetime import datetime, timedelta, timezone
from typing import Optional
from discord import app_commands
from discord.ext import commands
from utils import get_logger, format_uptime, format_bytes, owner_only, admin_only, status_emoji

logger = get_logger("cogs.general")


class GeneralCog(commands.Cog):
    """General bot commands"""

    def __init__(self, bot):
        self.bot = bot

    # ------------------------------------------------------------------
    # /help - Alle
    # ------------------------------------------------------------------

    @app_commands.command(name="help", description="Alle Commands anzeigen")
    async def help_cmd(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="Befehls-Uebersicht",
            color=0x5865F2
        )

        # Satisfactory Core
        core_cmds = (
            "`/sat status` - Server-Status\n"
            "`/sat start` - Server starten (Admin)\n"
            "`/sat stop` - Server stoppen (Admin)\n"
            "`/sat restart` - Neustart mit Countdown (Admin)\n"
            "`/sat cancel` - Vorgang abbrechen (Admin)"
        )
        embed.add_field(name="Satisfactory - Server", value=core_cmds, inline=False)

        # Satisfactory Players
        player_cmds = (
            "`/sat players online` - Online-Spieler\n"
            "`/sat players ban` - Spieler bannen (Admin)\n"
            "`/sat players unban` - Ban aufheben (Admin)\n"
            "`/sat players bans` - Alle Bans anzeigen"
        )
        embed.add_field(name="Spieler", value=player_cmds, inline=False)

        # Backup & Config
        backup_cmds = (
            "`/sat backup create` - Backup erstellen\n"
            "`/sat backup save` - Spiel speichern\n"
            "`/sat backup download` - Savegame laden\n"
            "`/sat backup list` - Backups auflisten\n"
            "`/sat backup restore` - Restore (Owner)"
        )
        embed.add_field(name="Backup", value=backup_cmds, inline=False)

        config_cmds = (
            "`/sat config settings` - Einstellungen\n"
            "`/sat config playerlimit` - Spielerlimit (Admin)\n"
            "`/sat config stats` - Savegame-Statistiken\n"
            "`/sat config update` - Server updaten (Owner)\n"
            "`/sat config console` - Konsole (Owner)"
        )
        embed.add_field(name="Konfiguration", value=config_cmds, inline=False)

        # Blueprints & Lists
        bp_cmds = (
            "`/sat blueprints upload` - Blueprint hochladen\n"
            "`/sat blueprints list` - Blueprints anzeigen\n"
            "`/sat blueprints download` - Blueprint laden\n"
            "`/sat blueprints delete` - Blueprint loeschen"
        )
        embed.add_field(name="Blueprints", value=bp_cmds, inline=False)

        # Timeout
        embed.add_field(
            name="Moderation",
            value="`/timeout [spieler] [min] [grund]` - Game-Kick + Discord-Timeout (Admin)",
            inline=False
        )

        # Monitor
        monitor_cmds = (
            "`/performance` - System-Performance (Spieler)\n"
            "`/stats [spieler]` - Spieler-Statistiken (Spieler)\n"
            "`/report [tage]` - Wochen-/Monatsbericht (Spieler)\n"
            "`/dashboard` - Dashboard aktualisieren (Admin)\n"
            "`/scheduler` - Scheduler-Status (Admin)\n"
            "`/email test|status` - Email-Benachrichtigungen (Admin)\n"
            "`/onedrive status|upload|list` - Cloud-Backup (Admin)"
        )
        embed.add_field(name="Monitor", value=monitor_cmds, inline=False)

        # General
        general_cmds = (
            "`/help` - Diese Uebersicht\n"
            "`/server` - Server-Uebersicht\n"
            "`/clear [anzahl] [stunden] [von] [bis]` - Nachrichten loeschen (Admin)\n"
            "`/ping` - Bot-Latenz (Owner)\n"
            "`/reload` - Cog neuladen (Owner)"
        )
        embed.add_field(name="Allgemein", value=general_cmds, inline=False)

        embed.set_footer(text="Owner > Admin > Spieler > Alle")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /server - Alle
    # ------------------------------------------------------------------

    @app_commands.command(name="server", description="Server-Uebersicht + System-Info")
    async def server_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer()

        embed = discord.Embed(
            title="Server-Uebersicht",
            description="Netcup RS 4000 G12 - Nuernberg, DE",
            color=0x5865F2
        )

        # System info
        cpu = psutil.cpu_percent(interval=1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        uptime = psutil.boot_time()
        import time
        sys_uptime = int(time.time() - uptime)

        embed.add_field(
            name="System",
            value=(
                f"CPU: {cpu}% ({psutil.cpu_count()} Kerne)\n"
                f"RAM: {format_bytes(mem.used)}/{format_bytes(mem.total)} ({mem.percent}%)\n"
                f"Disk: {format_bytes(disk.used)}/{format_bytes(disk.total)} ({disk.percent}%)\n"
                f"Uptime: {format_uptime(sys_uptime)}"
            ),
            inline=False
        )

        # Satisfactory status
        sat_running = await self.bot.sat_server.is_running()
        sat_text = f"{status_emoji(sat_running)} "
        if sat_running:
            try:
                state = await self.bot.sat_api.query_server_state()
                sat_text += (
                    f"Online - {state.num_players}/{state.player_limit} Spieler | "
                    f"Tick: {state.average_tick_rate:.1f}"
                )
            except Exception:
                sat_text += "Online (API nicht erreichbar)"
        else:
            sat_text += "Offline"
        embed.add_field(name="Satisfactory", value=sat_text, inline=False)

        # Minecraft status (placeholder)
        embed.add_field(name="Minecraft", value=f"{status_emoji(False)} Noch nicht eingerichtet", inline=False)

        await interaction.followup.send(embed=embed)

    # ------------------------------------------------------------------
    # /ping - Owner
    # ------------------------------------------------------------------

    @app_commands.command(name="ping", description="Bot-Latenz anzeigen")
    @owner_only()
    async def ping_cmd(self, interaction: discord.Interaction):
        latency = round(self.bot.latency * 1000)
        await interaction.response.send_message(f"Pong! {latency}ms", ephemeral=True)

    # ------------------------------------------------------------------
    # /clear - Admin
    # ------------------------------------------------------------------

    @app_commands.command(name="clear", description="Nachrichten im Channel loeschen")
    @app_commands.describe(
        anzahl="Anzahl der zu loeschenden Nachrichten (max 500)",
        stunden="Nur Nachrichten der letzten X Stunden loeschen",
        von="Nachrichten AB diesem Datum loeschen (Format: TT.MM.JJJJ oder TT.MM.JJJJ-HH:MM)",
        bis="Nachrichten BIS zu diesem Datum loeschen (Format: TT.MM.JJJJ oder TT.MM.JJJJ-HH:MM)",
    )
    @admin_only()
    async def clear_cmd(
        self,
        interaction: discord.Interaction,
        anzahl: Optional[int] = None,
        stunden: Optional[int] = None,
        von: Optional[str] = None,
        bis: Optional[str] = None,
    ):
        """Delete messages in the current channel.

        Handles both bulk deletion (< 14 days) and single deletion (> 14 days)
        automatically. Discord limits bulk deletion to messages under 14 days old,
        so older messages are deleted one by one.
        """
        await interaction.response.defer(ephemeral=True)

        # Defaults
        limit = min(anzahl or 500, 500)

        # Parse date strings
        after = None
        before = None

        if von:
            parsed = self._parse_date(von)
            if not parsed:
                await interaction.edit_original_response(
                    content="Ungueltiges Datum fuer `von`. Format: `TT.MM.JJJJ` oder `TT.MM.JJJJ-HH:MM`"
                )
                return
            after = parsed

        if bis:
            parsed = self._parse_date(bis)
            if not parsed:
                await interaction.edit_original_response(
                    content="Ungueltiges Datum fuer `bis`. Format: `TT.MM.JJJJ` oder `TT.MM.JJJJ-HH:MM`"
                )
                return
            before = parsed

        # stunden-Parameter (ueberschreibt von/bis nicht, ergaenzt)
        if stunden and not after:
            after = datetime.now(timezone.utc) - timedelta(hours=stunden)

        try:
            # Nachrichten sammeln
            messages = []
            async for msg in interaction.channel.history(
                limit=limit, after=after, before=before, oldest_first=False
            ):
                messages.append(msg)

            if not messages:
                await interaction.edit_original_response(
                    content="Keine Nachrichten zum Loeschen gefunden."
                )
                return

            # Initiale Statusmeldung via edit_original_response (nicht followup!)
            total = len(messages)
            await interaction.edit_original_response(
                content=f"Loesche {total} Nachrichten..."
            )

            # Aufteilen in Bulk-faehig (< 14 Tage) und alt (>= 14 Tage)
            cutoff = datetime.now(timezone.utc) - timedelta(days=14)
            bulk_msgs = [m for m in messages if m.created_at > cutoff]
            old_msgs = [m for m in messages if m.created_at <= cutoff]

            deleted_bulk = 0
            deleted_old = 0

            # Bulk-Delete fuer neuere Nachrichten (in 100er-Chunks)
            if bulk_msgs:
                for i in range(0, len(bulk_msgs), 100):
                    chunk = bulk_msgs[i:i + 100]
                    try:
                        await interaction.channel.delete_messages(
                            chunk,
                            reason=f"Cleared by {interaction.user.display_name}",
                        )
                        deleted_bulk += len(chunk)
                    except discord.HTTPException as e:
                        logger.warning(f"Bulk delete error: {e}")
                        # Fallback: einzeln loeschen
                        for msg in chunk:
                            try:
                                await msg.delete()
                                deleted_bulk += 1
                            except Exception:
                                pass

                    # Fortschritt nach jedem Chunk aktualisieren
                    try:
                        await interaction.edit_original_response(
                            content=f"Loesche Nachrichten... {deleted_bulk + deleted_old}/{total}"
                        )
                    except Exception:
                        pass

                    # Rate-Limit-Schutz
                    await asyncio.sleep(1)

            # Alte Nachrichten einzeln loeschen (kein Bulk moeglich)
            if old_msgs:
                for i, msg in enumerate(old_msgs):
                    try:
                        await msg.delete()
                        deleted_old += 1
                    except discord.NotFound:
                        pass  # Bereits geloescht
                    except discord.Forbidden:
                        logger.warning("No permission to delete old message")
                        break
                    except Exception as e:
                        logger.debug(f"Delete error: {e}")

                    # Rate-Limit: ~4 Loeschungen pro Sekunde
                    if (i + 1) % 4 == 0:
                        await asyncio.sleep(1.2)

                    # Fortschritt alle 25 Nachrichten aktualisieren
                    if (i + 1) % 25 == 0:
                        try:
                            await interaction.edit_original_response(
                                content=f"Loesche alte Nachrichten... {deleted_bulk + deleted_old}/{total}"
                            )
                        except Exception:
                            pass

            # Zusammenfassung erstellen
            deleted_count = deleted_bulk + deleted_old
            summary = f"{deleted_count} Nachrichten geloescht"
            if von and bis:
                summary += f" ({von} bis {bis})"
            elif von:
                summary += f" (ab {von})"
            elif bis:
                summary += f" (bis {bis})"
            elif stunden:
                summary += f" (letzte {stunden}h)"

            if bulk_msgs and old_msgs:
                summary += f"\n({deleted_bulk} per Bulk, {deleted_old} einzeln)"

            await interaction.edit_original_response(content=summary)

            logger.info(
                f"Channel {interaction.channel.name} cleared: "
                f"{deleted_count} messages by {interaction.user}"
            )

        except discord.Forbidden:
            try:
                await interaction.edit_original_response(
                    content="Keine Berechtigung. Der Bot braucht 'Nachrichten verwalten'."
                )
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Clear command error: {e}", exc_info=True)
            try:
                await interaction.edit_original_response(
                    content=f"Fehler beim Loeschen: {str(e)[:200]}"
                )
            except Exception:
                pass

    @staticmethod
    def _parse_date(date_str: str) -> Optional[datetime]:
        """Parse German date format: TT.MM.JJJJ or TT.MM.JJJJ-HH:MM"""
        for fmt in ("%d.%m.%Y-%H:%M", "%d.%m.%Y"):
            try:
                dt = datetime.strptime(date_str, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None

    # ------------------------------------------------------------------
    # /reload - Owner
    # ------------------------------------------------------------------

    @app_commands.command(name="reload", description="Cog zur Laufzeit neuladen")
    @owner_only()
    async def reload_cmd(self, interaction: discord.Interaction, cog: str):
        try:
            ext_name = f"cogs.{cog}" if not cog.startswith("cogs.") else cog
            await self.bot.reload_extension(ext_name)
            await interaction.response.send_message(
                f"Cog `{cog}` neu geladen!", ephemeral=True
            )
            logger.info(f"Cog reloaded: {cog} by {interaction.user}")
        except Exception as e:
            await interaction.response.send_message(
                f"Fehler beim Laden von `{cog}`: {e}", ephemeral=True
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
    await bot.add_cog(GeneralCog(bot))
