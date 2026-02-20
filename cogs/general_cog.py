"""
General Commands Cog
/help, /server, /ping, /reload, /clear
"""

import asyncio
import json
import time

import discord
import psutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from discord import app_commands
from discord.ext import commands
from utils import get_logger, format_uptime, format_bytes, owner_only, admin_only, status_emoji
from utils.config import DATA_DIR

logger = get_logger("cogs.general")

# Persistenz-Datei fuer laufende Clear-Tasks
CLEAR_STATE_FILE = DATA_DIR / "clear_tasks.json"


class GeneralCog(commands.Cog):
    """General bot commands"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Aktive Clear-Vorgaenge: {channel_id: task_info}
        self._active_clears: dict[int, dict] = {}
        # Abbruch-Events fuer laufende Clear-Tasks: {channel_id: asyncio.Event}
        self._clear_cancel_events: dict[int, asyncio.Event] = {}

    @commands.Cog.listener()
    async def on_ready(self):
        """Wenn der Bot ready ist: unterbrochene Clear-Tasks fortsetzen"""
        await self._resume_clear_tasks()

    # ------------------------------------------------------------------
    # Clear-Task Persistenz
    # ------------------------------------------------------------------

    def _save_clear_state(self, channel_id: int, state: dict) -> None:
        """Clear-Task-Status in JSON speichern"""
        try:
            data = {}
            if CLEAR_STATE_FILE.exists():
                data = json.loads(CLEAR_STATE_FILE.read_text(encoding="utf-8"))
            data[str(channel_id)] = state
            CLEAR_STATE_FILE.write_text(
                json.dumps(data, indent=2, default=str), encoding="utf-8"
            )
        except Exception as e:
            logger.error(f"Clear-State speichern fehlgeschlagen: {e}")

    def _remove_clear_state(self, channel_id: int) -> None:
        """Clear-Task-Status entfernen (nach Abschluss)"""
        try:
            if not CLEAR_STATE_FILE.exists():
                return
            data = json.loads(CLEAR_STATE_FILE.read_text(encoding="utf-8"))
            data.pop(str(channel_id), None)
            if data:
                CLEAR_STATE_FILE.write_text(
                    json.dumps(data, indent=2, default=str), encoding="utf-8"
                )
            else:
                CLEAR_STATE_FILE.unlink(missing_ok=True)
        except Exception as e:
            logger.error(f"Clear-State entfernen fehlgeschlagen: {e}")

    def _load_clear_states(self) -> dict:
        """Alle gespeicherten Clear-Tasks laden"""
        try:
            if CLEAR_STATE_FILE.exists():
                return json.loads(CLEAR_STATE_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"Clear-State laden fehlgeschlagen: {e}")
        return {}

    async def _resume_clear_tasks(self) -> None:
        """Nach Bot-Neustart unterbrochene Clear-Tasks fortsetzen"""
        states = self._load_clear_states()
        if not states:
            return

        logger.info(f"Setze {len(states)} unterbrochene Clear-Tasks fort...")

        for channel_id_str, state in states.items():
            channel_id = int(channel_id_str)

            guild = self.bot.get_guild(state.get("guild_id", 0))
            if not guild:
                self._remove_clear_state(channel_id)
                continue

            channel = guild.get_channel(channel_id)
            if not channel:
                self._remove_clear_state(channel_id)
                continue

            # Filter-Kriterien wiederherstellen
            after = None
            before = None
            if state.get("after"):
                after = datetime.fromisoformat(state["after"])
            if state.get("before"):
                before = datetime.fromisoformat(state["before"])

            # Wenn ein "last_deleted_at" gespeichert ist, loeschen wir ab dort weiter
            # (nur Nachrichten die aelter sind als die letzte geloeschte)
            if state.get("last_deleted_at"):
                before_resume = datetime.fromisoformat(state["last_deleted_at"])
                if after and before_resume <= after:
                    # Schon fertig
                    self._remove_clear_state(channel_id)
                    continue
                if before is None or before_resume < before:
                    before = before_resume

            # Task im Hintergrund starten
            asyncio.create_task(
                self._execute_clear(
                    channel=channel,
                    guild=guild,
                    limit=state.get("limit"),
                    after=after,
                    before=before,
                    user_name=state.get("user_name", "System (Restart)"),
                    user_id=state.get("user_id", 0),
                    interaction=None,  # Kein Interaction nach Restart
                    is_resume=True,
                    already_deleted=state.get("deleted_count", 0),
                )
            )

    # ------------------------------------------------------------------
    # Clear-Kern-Logik (wiederverwendbar fuer Command und Resume)
    # ------------------------------------------------------------------

    async def _execute_clear(
        self,
        channel: discord.TextChannel,
        guild: discord.Guild,
        limit: Optional[int],
        after: Optional[datetime],
        before: Optional[datetime],
        user_name: str,
        user_id: int,
        interaction: Optional[discord.Interaction],
        is_resume: bool = False,
        already_deleted: int = 0,
    ) -> None:
        """Kern-Loeschlogik — wird vom Command und vom Resume aufgerufen"""
        channel_id = channel.id

        # Channel-Lock pruefen
        if channel_id in self._active_clears:
            if interaction:
                await interaction.edit_original_response(
                    content="⚠️ In diesem Channel laeuft bereits ein Loeschvorgang. "
                    "Bitte warte bis er abgeschlossen ist."
                )
            return

        # Lock setzen + Cancel-Event erstellen
        cancel_event = asyncio.Event()
        self._clear_cancel_events[channel_id] = cancel_event
        self._active_clears[channel_id] = {
            "user": user_name,
            "started": datetime.now(timezone.utc).isoformat(),
        }

        try:
            # Nachrichten sammeln
            messages = []
            async for msg in channel.history(
                limit=limit, after=after, before=before, oldest_first=False
            ):
                messages.append(msg)

            if not messages and not is_resume:
                if interaction:
                    await interaction.edit_original_response(
                        content="Keine Nachrichten zum Loeschen gefunden."
                    )
                self._remove_clear_state(channel_id)
                return

            total = len(messages) + already_deleted

            if interaction:
                prefix = "Fortgesetzt: " if is_resume else ""
                await interaction.edit_original_response(
                    content=f"{prefix}Loesche {len(messages)} Nachrichten..."
                )

            if is_resume:
                logger.info(
                    f"Clear fortgesetzt in #{channel.name}: "
                    f"{len(messages)} verbleibend, {already_deleted} bereits geloescht"
                )

            # State speichern (fuer Crash-Recovery)
            self._save_clear_state(channel_id, {
                "guild_id": guild.id,
                "channel_id": channel_id,
                "channel_name": channel.name,
                "user_name": user_name,
                "user_id": user_id,
                "after": after.isoformat() if after else None,
                "before": before.isoformat() if before else None,
                "limit": limit,
                "deleted_count": already_deleted,
                "total_expected": total,
                "started": datetime.now(timezone.utc).isoformat(),
            })

            # Aufteilen in Bulk-faehig (< 14 Tage) und alt (>= 14 Tage)
            cutoff = datetime.now(timezone.utc) - timedelta(days=14)
            bulk_msgs = [m for m in messages if m.created_at > cutoff]
            old_msgs = [m for m in messages if m.created_at <= cutoff]

            deleted_bulk = 0
            deleted_old = 0
            last_deleted_at = None

            # Bulk-Delete fuer neuere Nachrichten (in 100er-Chunks)
            cancelled = False
            if bulk_msgs:
                for i in range(0, len(bulk_msgs), 100):
                    # Abbruch-Pruefung
                    if cancel_event.is_set():
                        cancelled = True
                        break

                    chunk = bulk_msgs[i:i + 100]
                    try:
                        await channel.delete_messages(
                            chunk,
                            reason=f"Cleared by {user_name}",
                        )
                        deleted_bulk += len(chunk)
                    except discord.HTTPException as e:
                        logger.warning(f"Bulk delete error: {e}")
                        # Fallback: einzeln loeschen
                        for msg in chunk:
                            if cancel_event.is_set():
                                cancelled = True
                                break
                            try:
                                await msg.delete()
                                deleted_bulk += 1
                            except Exception:
                                pass

                    # Letzten Zeitpunkt merken fuer Recovery
                    if chunk:
                        last_deleted_at = chunk[-1].created_at

                    # State aktualisieren
                    self._save_clear_state(channel_id, {
                        "guild_id": guild.id,
                        "channel_id": channel_id,
                        "channel_name": channel.name,
                        "user_name": user_name,
                        "user_id": user_id,
                        "after": after.isoformat() if after else None,
                        "before": before.isoformat() if before else None,
                        "limit": limit,
                        "deleted_count": already_deleted + deleted_bulk + deleted_old,
                        "total_expected": total,
                        "last_deleted_at": last_deleted_at.isoformat() if last_deleted_at else None,
                        "started": datetime.now(timezone.utc).isoformat(),
                    })

                    # Fortschritt aktualisieren
                    if interaction:
                        try:
                            await interaction.edit_original_response(
                                content=f"Loesche Nachrichten... "
                                f"{already_deleted + deleted_bulk + deleted_old}/{total}"
                            )
                        except Exception:
                            pass

                    await asyncio.sleep(1)

            # Alte Nachrichten einzeln loeschen
            if old_msgs and not cancelled:
                for i, msg in enumerate(old_msgs):
                    # Abbruch-Pruefung
                    if cancel_event.is_set():
                        cancelled = True
                        break

                    try:
                        await msg.delete()
                        deleted_old += 1
                        last_deleted_at = msg.created_at
                    except discord.NotFound:
                        pass
                    except discord.Forbidden:
                        logger.warning("No permission to delete old message")
                        break
                    except Exception as e:
                        logger.debug(f"Delete error: {e}")

                    if (i + 1) % 4 == 0:
                        await asyncio.sleep(1.2)

                    # State + Fortschritt alle 25 Nachrichten
                    if (i + 1) % 25 == 0:
                        self._save_clear_state(channel_id, {
                            "guild_id": guild.id,
                            "channel_id": channel_id,
                            "channel_name": channel.name,
                            "user_name": user_name,
                            "user_id": user_id,
                            "after": after.isoformat() if after else None,
                            "before": before.isoformat() if before else None,
                            "limit": limit,
                            "deleted_count": already_deleted + deleted_bulk + deleted_old,
                            "total_expected": total,
                            "last_deleted_at": last_deleted_at.isoformat() if last_deleted_at else None,
                            "started": datetime.now(timezone.utc).isoformat(),
                        })
                        if interaction:
                            try:
                                await interaction.edit_original_response(
                                    content=f"Loesche alte Nachrichten... "
                                    f"{already_deleted + deleted_bulk + deleted_old}/{total}"
                                )
                            except Exception:
                                pass

            # --- Abschluss ---
            deleted_count = already_deleted + deleted_bulk + deleted_old
            failed_count = total - deleted_count
            if cancelled:
                remaining = total - deleted_count
                summary = f"⛔ Abgebrochen: {deleted_count} Nachrichten geloescht, {remaining} uebersprungen"
            else:
                summary = f"{deleted_count} Nachrichten geloescht"

            # Zeitraum-Info (nur bei manuellem Aufruf mit Parametern)
            if is_resume and not cancelled:
                summary = f"🔄 Fortgesetzt: {summary}"

            if interaction:
                try:
                    if failed_count > 0:
                        summary += f"\n⚠️ {failed_count} konnten nicht geloescht werden"
                    await interaction.edit_original_response(content=summary)
                except Exception:
                    pass

            logger.info(
                f"Channel {channel.name} cleared: "
                f"{deleted_count}/{total} messages by {user_name}"
            )

            # Log-Benachrichtigung
            _log_ch = getattr(
                getattr(self.bot, "command_logger", None), "admin_channel", None
            )
            if _log_ch and (total > 100 or failed_count > 0 or is_resume):
                try:
                    if failed_count > 0:
                        title = "⚠️ Clear-Befehl unvollstaendig"
                        color = 0xf39c12
                    elif is_resume:
                        title = "🔄 Clear-Befehl fortgesetzt & abgeschlossen"
                        color = 0x3498db
                    else:
                        title = "✅ Clear-Befehl abgeschlossen"
                        color = 0x2ecc71
                    embed = discord.Embed(
                        title=title,
                        color=color,
                        timestamp=datetime.now(timezone.utc),
                    )
                    embed.add_field(
                        name="Channel",
                        value=f"#{channel.name}",
                        inline=True,
                    )
                    embed.add_field(
                        name="Ausgefuehrt von",
                        value=user_name,
                        inline=True,
                    )
                    result_text = f"{deleted_count}/{total} geloescht"
                    if failed_count > 0:
                        result_text += f", {failed_count} fehlgeschlagen"
                    embed.add_field(
                        name="Ergebnis",
                        value=result_text,
                        inline=False,
                    )
                    await _log_ch.send(embed=embed)
                except Exception:
                    pass

            # State aufraumen
            self._remove_clear_state(channel_id)

        except discord.Forbidden:
            self._send_error_log(channel, user_name, "Keine Berechtigung zum Loeschen")
            if interaction:
                try:
                    await interaction.edit_original_response(
                        content="Keine Berechtigung. Der Bot braucht 'Nachrichten verwalten'."
                    )
                except Exception:
                    pass
            self._remove_clear_state(channel_id)

        except Exception as e:
            logger.error(f"Clear command error: {e}", exc_info=True)
            self._send_error_log(channel, user_name, str(e)[:200])
            if interaction:
                try:
                    await interaction.edit_original_response(
                        content=f"Fehler beim Loeschen: {str(e)[:200]}"
                    )
                except Exception:
                    pass
            # State NICHT entfernen bei unerwartetem Fehler — wird beim naechsten
            # Restart fortgesetzt

        finally:
            # Lock + Cancel-Event freigeben
            self._active_clears.pop(channel_id, None)
            self._clear_cancel_events.pop(channel_id, None)

    def _send_error_log(self, channel, user_name: str, error_msg: str) -> None:
        """Fehler-Embed an Log-Channel senden (fire-and-forget)"""
        _log_ch = getattr(
            getattr(self.bot, "command_logger", None), "admin_channel", None
        )
        if _log_ch:
            async def _send():
                try:
                    embed = discord.Embed(
                        title="❌ Clear-Befehl fehlgeschlagen",
                        description=error_msg,
                        color=0xe74c3c,
                        timestamp=datetime.now(timezone.utc),
                    )
                    embed.add_field(
                        name="Channel", value=f"#{channel.name}", inline=True
                    )
                    embed.add_field(
                        name="User", value=user_name, inline=True
                    )
                    await _log_ch.send(embed=embed)
                except Exception:
                    pass
            asyncio.create_task(_send())

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
            "`/clear [anzahl] [stunden] [von] [bis]` - Nachrichten loeschen, nochmal = Abbruch (Admin)\n"
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

        # System info (in Executor um Event-Loop nicht zu blockieren)
        loop = asyncio.get_running_loop()
        cpu = await loop.run_in_executor(None, psutil.cpu_percent, 1)
        mem = await loop.run_in_executor(None, psutil.virtual_memory)
        disk = await loop.run_in_executor(None, psutil.disk_usage, "/")
        uptime = await loop.run_in_executor(None, psutil.boot_time)
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
        anzahl="Anzahl der zu loeschenden Nachrichten (Standard: 500, bei Datum: unbegrenzt)",
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

        # Pruefen ob schon ein Clear in diesem Channel laeuft
        if interaction.channel.id in self._active_clears:
            # Wenn ohne Parameter aufgerufen: Abbruch des laufenden Vorgangs
            if not anzahl and not stunden and not von and not bis:
                cancel_ev = self._clear_cancel_events.get(interaction.channel.id)
                if cancel_ev:
                    cancel_ev.set()
                    await interaction.edit_original_response(
                        content="⛔ Loeschvorgang wird abgebrochen..."
                    )
                    return
            await interaction.edit_original_response(
                content="⚠️ In diesem Channel laeuft bereits ein Loeschvorgang. "
                "Verwende `/clear` ohne Parameter um ihn abzubrechen."
            )
            return

        # Defaults — bei Datumsangabe kein Limit (None = alle), sonst max 500
        if anzahl:
            limit = anzahl
        elif von or bis or stunden:
            limit = None  # Alle Nachrichten im Zeitraum
        else:
            limit = 500

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

        # Clear-Vorgang starten
        await self._execute_clear(
            channel=interaction.channel,
            guild=interaction.guild,
            limit=limit,
            after=after,
            before=before,
            user_name=interaction.user.display_name,
            user_id=interaction.user.id,
            interaction=interaction,
        )

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
