"""
General Commands Cog
/help, /reload, /clear

Phase 14 (F25): /server und /ping entfernt (→ Dashboard).
"""

import asyncio
import json

import discord
from utils.async_tasks import track_task
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from discord import app_commands
from discord.ext import commands
from utils import (
    get_logger,
    owner_only, admin_only,
    is_owner, is_admin, is_spieler,
)
from utils.config import DATA_DIR

logger = get_logger("cogs.general")

# Persistenz-Datei für laufende Clear-Tasks
CLEAR_STATE_FILE = DATA_DIR / "clear_tasks.json"

# Discord-API-Limit: Bulk-Delete funktioniert nur fuer Nachrichten < 14 Tage.
# Aeltere muessen einzeln geloescht werden.
BULK_DELETE_MAX_AGE_DAYS = 14


class GeneralCog(commands.Cog):
    """General bot commands"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Aktive Clear-Vorgaenge: {channel_id: task_info}
        self._active_clears: dict[int, dict] = {}
        # Abbruch-Events für laufende Clear-Tasks: {channel_id: asyncio.Event}
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

            # Wenn ein "last_deleted_at" gespeichert ist, löschen wir ab dort weiter
            # (nur Nachrichten die aelter sind als die letzte gelöschte)
            if state.get("last_deleted_at"):
                before_resume = datetime.fromisoformat(state["last_deleted_at"])
                if after and before_resume <= after:
                    # Schon fertig
                    self._remove_clear_state(channel_id)
                    continue
                if before is None or before_resume < before:
                    before = before_resume

            # Task im Hintergrund starten — mit Reference-Tracking (audit-fix)
            track_task(
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
    # Clear-Kern-Logik (wiederverwendbar für Command und Resume)
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

        # Channel-Lock prüfen
        if channel_id in self._active_clears:
            if interaction:
                await interaction.edit_original_response(
                    content="⚠️ In diesem Channel läuft bereits ein Loeschvorgang. "
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
                    f"{len(messages)} verbleibend, {already_deleted} bereits gelöscht"
                )

            # State speichern (für Crash-Recovery)
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
            cutoff = datetime.now(timezone.utc) - timedelta(days=BULK_DELETE_MAX_AGE_DAYS)
            bulk_msgs = [m for m in messages if m.created_at > cutoff]
            old_msgs = [m for m in messages if m.created_at <= cutoff]

            deleted_bulk = 0
            deleted_old = 0
            last_deleted_at = None

            # Bulk-Delete für neuere Nachrichten (in 100er-Chunks)
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
                        # Fallback: einzeln löschen
                        for msg in chunk:
                            if cancel_event.is_set():
                                cancelled = True
                                break
                            try:
                                await msg.delete()
                                deleted_bulk += 1
                            except Exception as e:
                                logger.debug(f"Exception swallowed (B110-refactor 3.1): {e}")

                    # Letzten Zeitpunkt merken für Recovery
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
                        except Exception as e:
                            logger.debug(f"Exception swallowed (B110-refactor 3.1): {e}")

                    await asyncio.sleep(1)

            # Alte Nachrichten einzeln löschen
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
                            except Exception as e:
                                logger.debug(f"Exception swallowed (B110-refactor 3.1): {e}")

            # --- Abschluss ---
            deleted_count = already_deleted + deleted_bulk + deleted_old
            failed_count = total - deleted_count
            if cancelled:
                remaining = total - deleted_count
                summary = f"⛔ Abgebrochen: {deleted_count} Nachrichten gelöscht, {remaining} übersprungen"
            else:
                summary = f"{deleted_count} Nachrichten gelöscht"

            # Zeitraum-Info (nur bei manuellem Aufruf mit Parametern)
            if is_resume and not cancelled:
                summary = f"🔄 Fortgesetzt: {summary}"

            if interaction:
                try:
                    if failed_count > 0:
                        summary += f"\n⚠️ {failed_count} konnten nicht gelöscht werden"
                    await interaction.edit_original_response(content=summary)
                except Exception as e:
                    logger.debug(f"Exception swallowed (B110-refactor 3.1): {e}")

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
                    result_text = f"{deleted_count}/{total} gelöscht"
                    if failed_count > 0:
                        result_text += f", {failed_count} fehlgeschlagen"
                    embed.add_field(
                        name="Ergebnis",
                        value=result_text,
                        inline=False,
                    )
                    await _log_ch.send(embed=embed)
                except Exception as e:
                    logger.debug(f"Exception swallowed (B110-refactor 3.1): {e}")

            # State aufraumen
            self._remove_clear_state(channel_id)

        except discord.Forbidden:
            self._send_error_log(channel, user_name, "Keine Berechtigung zum Loeschen")
            if interaction:
                try:
                    await interaction.edit_original_response(
                        content="Keine Berechtigung. Der Bot braucht 'Nachrichten verwalten'."
                    )
                except Exception as e:
                    logger.debug(f"Exception swallowed (B110-refactor 3.1): {e}")
            self._remove_clear_state(channel_id)

        except Exception as e:
            logger.error(f"Clear command error: {e}", exc_info=True)
            self._send_error_log(channel, user_name, str(e)[:200])
            if interaction:
                try:
                    await interaction.edit_original_response(
                        content=f"Fehler beim Loeschen: {str(e)[:200]}"
                    )
                except Exception as e:
                    logger.debug(f"Exception swallowed (B110-refactor 3.1): {e}")
            # State NICHT entfernen bei unerwartetem Fehler — wird beim nächsten
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
                except Exception as e:
                    logger.debug(f"Exception swallowed (B110-refactor 3.1): {e}")
            track_task(_send(), name="general_cog.send_audit_log")

    # ------------------------------------------------------------------
    # /help - Alle (rollenbasiert)
    # ------------------------------------------------------------------

    # Berechtigungsstufen für Command-Filterung
    _LEVEL_ALL = 0
    _LEVEL_SPIELER = 1
    _LEVEL_ADMIN = 2
    _LEVEL_OWNER = 3

    # Command-Datenstruktur: (command, beschreibung, level)
    # Wird dynamisch gefiltert basierend auf User-Rolle
    _HELP_COMMANDS: dict[str, list[tuple[str, str, int]]] = {
        "Satisfactory — Server": [
            ("/sat status", "Server-Status", _LEVEL_ALL),
        ],
        "Satisfactory — Spieler": [
            ("/sat players online", "Online-Spieler", _LEVEL_ALL),
            ("/sat players ban", "Spieler bannen", _LEVEL_ADMIN),
            ("/sat players unban", "Ban aufheben", _LEVEL_ADMIN),
            ("/sat players bans", "Alle Bans anzeigen", _LEVEL_SPIELER),
        ],
        "Satisfactory — Savegames": [
            ("/sat sav save", "Spiel speichern", _LEVEL_SPIELER),
            ("/sat sav download", "Savegame laden", _LEVEL_SPIELER),
            ("/sat sav list", "Savegames auflisten", _LEVEL_SPIELER),
            ("/sat sav restore", "Savegame wiederherstellen", _LEVEL_OWNER),
            ("/sat sav load", "Savegame laden", _LEVEL_OWNER),
            ("/sat sav stats", "Savegame-Statistiken", _LEVEL_SPIELER),
        ],
        "Satisfactory — Config": [
            ("/sat config settings", "Einstellungen anzeigen", _LEVEL_SPIELER),
        ],
        "Satisfactory — Blueprints": [
            ("/sat blueprints upload", "Blueprint hochladen", _LEVEL_SPIELER),
            ("/sat blueprints list", "Blueprints anzeigen", _LEVEL_SPIELER),
            ("/sat blueprints download", "Blueprint laden", _LEVEL_SPIELER),
            ("/sat blueprints delete", "Blueprint löschen", _LEVEL_SPIELER),
        ],
        "Satisfactory — Listen": [
            ("/sat whitelist add|remove|list", "Whitelist verwalten", _LEVEL_ADMIN),
            ("/sat blacklist add|remove|list", "Blacklist verwalten", _LEVEL_ADMIN),
        ],
        "Minecraft — Server": [
            ("/mc status [server]", "Server-Status", _LEVEL_ALL),
        ],
        "Minecraft — Spieler": [
            ("/mc players list [server]", "Online-Spieler", _LEVEL_ALL),
            ("/mc players kick", "Spieler kicken", _LEVEL_ADMIN),
            ("/mc players ban", "Spieler bannen (RCON + IP)", _LEVEL_ADMIN),
            ("/mc players pardon", "Spieler entbannen", _LEVEL_ADMIN),
        ],
        "Minecraft — Backup & Welt": [
            ("/mc backup create [server]", "Welt-Backup erstellen", _LEVEL_SPIELER),
            ("/mc backup list [server]", "Backups auflisten", _LEVEL_SPIELER),
            ("/mc backup download [server]", "Backup herunterladen", _LEVEL_SPIELER),
            ("/mc backup restore [server]", "Backup wiederherstellen", _LEVEL_OWNER),
            ("/mc world stats [server]", "Detaillierte Welt-Analyse", _LEVEL_ALL),
        ],
        "Minecraft — Config": [
            ("/mc config settings [server]", "Einstellungen anzeigen", _LEVEL_ALL),
            ("/mc config stats [server]", "World-Statistiken", _LEVEL_ALL),
            ("/mc config modpack_check", "Modpack-Updates prüfen", _LEVEL_ADMIN),
        ],
        "Minecraft — Listen": [
            ("/mc whitelist add|remove|list", "Whitelist verwalten", _LEVEL_ADMIN),
            ("/mc blacklist add|remove|list", "Blacklist verwalten", _LEVEL_ADMIN),
            ("/mc blacklist history", "Ban-Historie einsehen", _LEVEL_SPIELER),
        ],
        "Minecraft — Admin": [
            ("/mc say <nachricht>", "Ankuendigung senden (Banner)", _LEVEL_ADMIN),
            ("/mc command <cmd>", "RCON-Befehl ausführen", _LEVEL_OWNER),
        ],
        "Moderation": [
            ("/timeout <spieler> <dauer> [grund]", "Multi-Server Temp-Ban", _LEVEL_ADMIN),
            ("/timeout status", "Eigene Timeout-Info", _LEVEL_ALL),
            ("/timeout aufheben <spieler>", "Timeout aufheben", _LEVEL_ADMIN),
            ("/timeout list", "Alle aktiven Timeouts", _LEVEL_ADMIN),
            ("/timeout history <spieler>", "Timeout-Historie", _LEVEL_ADMIN),
        ],
        "Mods": [
            ("/mod list [game]", "Installierte Mods anzeigen", _LEVEL_SPIELER),
            ("/mod info <name>", "Mod-Details anzeigen", _LEVEL_SPIELER),
        ],
        "Minecraft — Monitor": [
            ("/mcstats [server]", "Minecraft Server-Statistiken", _LEVEL_SPIELER),
            ("/mcreport [tage] [server]", "Minecraft Wochenbericht", _LEVEL_SPIELER),
            ("/mccrashlog [nummer] [server]", "MC Crash-Replays", _LEVEL_ADMIN),
        ],
        "Monitor": [
            ("/performance", "System-Performance", _LEVEL_SPIELER),
            ("/stats [spieler]", "Spieler-Statistiken", _LEVEL_SPIELER),
            ("/report [tage]", "Wochen-/Monatsbericht", _LEVEL_SPIELER),
            ("/dashboard", "Dashboard aktualisieren", _LEVEL_ADMIN),
            ("/scheduler", "Scheduler-Status", _LEVEL_ADMIN),
            ("/selftest", "Alle Bot-Systeme prüfen", _LEVEL_ADMIN),
            ("/commandlog [anzahl]", "Letzte Bot-Commands", _LEVEL_ADMIN),
            ("/crashlog [nummer]", "SAT Crash-Replays", _LEVEL_ADMIN),
            ("/configbackup", "Config-Backup erstellen", _LEVEL_ADMIN),
            ("/mail test|status", "Email-Benachrichtigungen", _LEVEL_ADMIN),
            ("/onedrive status|upload|list", "Cloud-Backup", _LEVEL_ADMIN),
            ("/update_check", "Manuell nach Updates suchen", _LEVEL_ADMIN),
            ("/rollback", "Rollback-Verwaltung", _LEVEL_OWNER),
        ],
        "Geplante Nachrichten": [
            ("/schedule add <nachricht> <zeit>", "Nachricht planen", _LEVEL_ADMIN),
            ("/schedule list", "Geplante Nachrichten anzeigen", _LEVEL_ADMIN),
            ("/schedule cancel <id>", "Geplante Nachricht abbrechen", _LEVEL_ADMIN),
        ],
        "Allgemein": [
            ("/help", "Diese Übersicht", _LEVEL_ALL),
            ("/clear [anzahl] [stunden]", "Nachrichten löschen", _LEVEL_ADMIN),
            ("/reload <cog>", "Cog neuladen", _LEVEL_OWNER),
        ],
    }

    # Beschriftungen für Berechtigungsstufen im Embed
    _LEVEL_LABELS = {
        _LEVEL_SPIELER: "(Spieler)",
        _LEVEL_ADMIN: "(Admin)",
        _LEVEL_OWNER: "(Owner)",
    }

    def _get_user_level(self, interaction: discord.Interaction) -> int:
        """Ermittelt die hoechste Berechtigungsstufe des Users"""
        if is_owner(interaction):
            return self._LEVEL_OWNER
        if is_admin(interaction):
            return self._LEVEL_ADMIN
        if is_spieler(interaction):
            return self._LEVEL_SPIELER
        return self._LEVEL_ALL

    @app_commands.command(name="help", description="Verfuegbare Commands anzeigen")
    async def help_cmd(self, interaction: discord.Interaction):
        """Zeigt nur Commands an, für die der User die Berechtigung hat."""
        user_level = self._get_user_level(interaction)

        # Rollenlabel für Titel
        level_names = {
            self._LEVEL_ALL: "Alle",
            self._LEVEL_SPIELER: "Spieler",
            self._LEVEL_ADMIN: "Admin",
            self._LEVEL_OWNER: "Owner",
        }
        role_name = level_names.get(user_level, "Alle")

        embed = discord.Embed(
            title=f"Befehls-Übersicht — {role_name}",
            description="Nur Commands die du ausführen darfst werden angezeigt.",
            color=0x5865F2,
        )

        # MC-Server vorhanden?
        has_mc = bool(getattr(self.bot, 'mc_servers', {}))

        for category, commands_list in self._HELP_COMMANDS.items():
            # MC-Kategorien nur anzeigen wenn MC-Server konfiguriert
            if category.startswith("Minecraft") and not has_mc:
                continue

            # Nur Commands filtern die der User ausführen darf
            visible = []
            for cmd_name, cmd_desc, cmd_level in commands_list:
                if cmd_level <= user_level:
                    # Berechtigungslabel nur für Admins/Owner sichtbar
                    label = ""
                    if user_level >= self._LEVEL_ADMIN and cmd_level > self._LEVEL_ALL:
                        label = f" {self._LEVEL_LABELS.get(cmd_level, '')}"
                    visible.append(f"`{cmd_name}` — {cmd_desc}{label}")

            # Kategorie nur anzeigen wenn mindestens ein Command sichtbar
            if visible:
                embed.add_field(
                    name=category,
                    value="\n".join(visible),
                    inline=False,
                )

        embed.set_footer(text="Owner > Admin > Spieler > Alle")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /clear - Admin
    # ------------------------------------------------------------------

    @app_commands.command(name="clear", description="Nachrichten im Channel löschen")
    @app_commands.describe(
        anzahl="Anzahl der zu löschenden Nachrichten (Standard: 500, bei Datum: unbegrenzt)",
        stunden="Nur Nachrichten der letzten X Stunden löschen",
        von="Nachrichten AB diesem Datum löschen (Format: TT.MM.JJJJ oder TT.MM.JJJJ-HH:MM)",
        bis="Nachrichten BIS zu diesem Datum löschen (Format: TT.MM.JJJJ oder TT.MM.JJJJ-HH:MM)",
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

        # Pruefen ob schon ein Clear in diesem Channel läuft
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
                content="⚠️ In diesem Channel läuft bereits ein Loeschvorgang. "
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
                    content="Ungueltiges Datum für `von`. Format: `TT.MM.JJJJ` oder `TT.MM.JJJJ-HH:MM`"
                )
                return
            after = parsed

        if bis:
            parsed = self._parse_date(bis)
            if not parsed:
                await interaction.edit_original_response(
                    content="Ungueltiges Datum für `bis`. Format: `TT.MM.JJJJ` oder `TT.MM.JJJJ-HH:MM`"
                )
                return
            before = parsed

        # stunden-Parameter (überschreibt von/bis nicht, ergaenzt)
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


async def setup(bot):
    await bot.add_cog(GeneralCog(bot))
