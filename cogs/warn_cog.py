"""
Warn Cog — Phase 11c: Warn-System für den Admin Bot

Command-Struktur (app_commands.Group):
  /warn add <user> <grund> [punkte]   — User verwarnen (Admin)
  /warn remove <user> <warn_id>       — Bestimmten Warn entfernen (Admin)
  /warn list <user>                   — Aktive Warns anzeigen (Admin)
  /warn history <user>                — Alle Warns inkl. abgelaufener (Admin)

Auto-Aktionen basierend auf Gesamtpunkten:
  3+ Punkte  -> Auto-Mute (Discord Timeout 1h)
  6+ Punkte  -> Auto-Kick
  10+ Punkte -> Auto-Ban

Background-Task: Prueft stuendlich auf abgelaufene Warns (Standard: 30 Tage)
"""

from datetime import datetime, timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from modules.warn_manager import WarnManager
from utils.logger import get_logger
from utils.config import ADMIN_DATA_DIR
from utils.permissions import admin_only
from utils.embeds import (
    COLOR_BRAND,
    COLOR_ERROR,
    COLOR_SUCCESS,
    COLOR_WARNING,
    error_embed,
    hud_embed,
    success_embed,
    warning_embed,
)
from utils.ui_kit import subtext
from utils.loop_guard import guard

logger = get_logger("cogs.warn")


class WarnCog(commands.Cog):
    """Warn-System mit Punkten, Verfall und automatischen Aktionen"""

    warn_grp = app_commands.Group(
        name="warn",
        description="Warn-System — Verwarnungen verwalten",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.warn_mgr = WarnManager(
            data_file=ADMIN_DATA_DIR / "warns.json",
        )

    async def cog_load(self) -> None:
        """SQLite-Daten laden und Background-Task starten wenn Cog geladen wird"""
        await self.warn_mgr.load_from_db()
        guard(self.check_expired_warns, name="check_expired_warns")
        self.check_expired_warns.start()
        logger.info("Warn-Cog geladen, SQLite-Daten geladen, Background-Task gestartet")

    async def cog_unload(self) -> None:
        """Background-Task stoppen wenn Cog entladen wird"""
        self.check_expired_warns.cancel()
        logger.info("Warn-Cog entladen")

    # ==================================================================
    # Background-Task: Abgelaufene Warns markieren (stuendlich)
    # ==================================================================

    @tasks.loop(hours=1)
    async def check_expired_warns(self) -> None:
        """Prueft stuendlich ob Warns abgelaufen sind (Standard: 30 Tage)"""
        try:
            newly_expired = self.warn_mgr.check_expired()
            if newly_expired:
                logger.info(
                    f"Warn-Verfall-Task: {len(newly_expired)} Warns abgelaufen"
                )
        except Exception as e:
            logger.error(f"Fehler im Warn-Verfall-Task: {e}", exc_info=True)

    @check_expired_warns.before_loop
    async def before_check_expired(self) -> None:
        """Warten bis Bot bereit ist"""
        await self.bot.wait_until_ready()

    # ==================================================================
    # /warn add <user> <grund> [punkte]
    # ==================================================================

    @warn_grp.command(
        name="add",
        description="User verwarnen (Standard: 1 Punkt)",
    )
    @app_commands.describe(
        user="Der zu verwarnende User",
        grund="Grund für die Verwarnung",
        punkte="Punktzahl (Standard: 1, max. 10)",
    )
    @admin_only()
    async def warn_add(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        grund: app_commands.Range[str, 1, 500],
        punkte: Optional[app_commands.Range[int, 1, 10]] = 1,
    ) -> None:
        await interaction.response.defer()

        # Selbst-Warn verhindern
        if user.id == interaction.user.id:
            await interaction.followup.send(
                "Du kannst dich nicht selbst verwarnen.", ephemeral=True
            )
            return

        # Bot-Warn verhindern
        if user.bot:
            await interaction.followup.send(
                "Bots können nicht verwarnt werden.", ephemeral=True
            )
            return

        # Warn hinzufuegen
        warn_entry = self.warn_mgr.add_warn(
            user_id=user.id,
            reason=grund,
            points=punkte,
            warned_by=interaction.user.display_name,
        )

        total_points = self.warn_mgr.get_total_points(user.id)
        active_warns = self.warn_mgr.get_warns(user.id)

        # User per DM benachrichtigen (fehlertolerant)
        dm_sent = await self._notify_user_dm(
            user,
            grund=grund,
            punkte=punkte,
            total_points=total_points,
        )

        # HUD-Stil: der Punktestand als Balken gegen die Ban-Schwelle macht die
        # Eskalation sichtbar — vorher standen hier sieben Felder fuer eine
        # Quittung.
        ban_thresh = self.warn_mgr.thresholds.get("ban", 10)
        warn_id = warn_entry["id"][:8]
        embed = hud_embed(
            "VERWARNUNG",
            state="crit" if total_points >= ban_thresh else "warn",
            meta=[
                (f"+{punkte}", "Punkte"),
                (str(total_points), "gesamt"),
                (str(len(active_warns)), "aktiv"),
            ],
            bar=(total_points, ban_thresh),
            description=f"{user.mention} · {discord.utils.escape_markdown(grund)}",
            lines=[
                subtext(f"Ban-Schwelle bei {ban_thresh} Punkten"),
                subtext(
                    f"durch {discord.utils.escape_markdown(interaction.user.display_name)} · "
                    f"Warn-ID `{warn_id}` · "
                    + ("DM zugestellt" if dm_sent else "DM nicht möglich")
                ),
            ],
        )

        await interaction.followup.send(embed=embed)

        # Auto-Aktion prüfen und ausführen
        await self._execute_auto_action(interaction, user, total_points)

    # ==================================================================
    # /warn remove <user> <warn_id>
    # ==================================================================

    @warn_grp.command(
        name="remove",
        description="Bestimmten Warn entfernen",
    )
    @app_commands.describe(
        user="User dessen Warn entfernt werden soll",
        warn_id="ID des Warns (aus /warn list oder /warn history)",
    )
    @admin_only()
    async def warn_remove(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        warn_id: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        # Warn-ID kann abgekuerzt sein (erste 8 Zeichen) — vollstaendige ID suchen
        resolved_id = self._resolve_warn_id(user.id, warn_id)
        if not resolved_id:
            await interaction.followup.send(
                f"Warn-ID `{warn_id}` nicht gefunden für {user.display_name}.",
                ephemeral=True,
            )
            return

        success = self.warn_mgr.remove_warn(user.id, resolved_id)

        if success:
            total_points = self.warn_mgr.get_total_points(user.id)
            embed = success_embed(
                title="Warn entfernt",
                description=(
                    f"Warn `{resolved_id[:8]}...` von {user.mention} entfernt.\n"
                    f"Verbleibende Punkte: **{total_points}**"
                ),
            )
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.followup.send(embed=embed, ephemeral=True)
            logger.info(
                f"Warn entfernt: {user} Warn-ID {resolved_id[:8]} "
                f"von {interaction.user}"
            )
        else:
            await interaction.followup.send(
                f"Warn konnte nicht entfernt werden. "
                f"Existiert die ID `{warn_id}` für {user.display_name}?",
                ephemeral=True,
            )

    # ==================================================================
    # /warn list <user>
    # ==================================================================

    @warn_grp.command(
        name="list",
        description="Aktive Warns eines Users anzeigen",
    )
    @app_commands.describe(user="User dessen aktive Warns angezeigt werden")
    @admin_only()
    async def warn_list(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        active_warns = self.warn_mgr.get_warns(user.id)
        total_points = self.warn_mgr.get_total_points(user.id)

        if not active_warns:
            await interaction.followup.send(
                f"{user.display_name} hat keine aktiven Verwarnungen.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"Aktive Warns — {user.display_name}",
            description=(
                f"**{len(active_warns)}** aktive Warn(s), "
                f"**{total_points}** Punkt(e) gesamt"
            ),
            color=self._points_color(total_points),
        )

        # Schwellenwert-Info
        threshold_info = self._threshold_status(total_points)
        if threshold_info:
            embed.description += f"\n{threshold_info}"

        for i, warn in enumerate(active_warns[:15], 1):
            # Datum formatieren
            warned_at = warn.get("warned_at", "?")
            try:
                dt = datetime.fromisoformat(warned_at)
                warned_at = dt.strftime("%d.%m.%Y %H:%M")
            except (ValueError, TypeError):
                pass

            embed.add_field(
                name=f"{i}. {warn.get('points', 1)} Punkt(e) — {warned_at}",
                value=(
                    f"**Grund:** {warn.get('reason', 'Kein Grund')}\n"
                    f"**Von:** {warn.get('warned_by', '?')}\n"
                    f"**ID:** `{warn.get('id', '?')[:8]}...`"
                ),
                inline=False,
            )

        if len(active_warns) > 15:
            embed.set_footer(
                text=f"... und {len(active_warns) - 15} weitere Warns"
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ==================================================================
    # /warn history <user>
    # ==================================================================

    @warn_grp.command(
        name="history",
        description="Alle Warns eines Users anzeigen (inkl. abgelaufener)",
    )
    @app_commands.describe(user="User dessen komplette Warn-Historie angezeigt wird")
    @admin_only()
    async def warn_history(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        all_warns = self.warn_mgr.get_all_warns(user.id)
        total_points = self.warn_mgr.get_total_points(user.id)

        if not all_warns:
            await interaction.followup.send(
                f"{user.display_name} hat keine Verwarnungen in der Historie.",
                ephemeral=True,
            )
            return

        active_count = sum(1 for w in all_warns if not w.get("expired", False))
        expired_count = len(all_warns) - active_count

        embed = discord.Embed(
            title=f"Warn-Historie — {user.display_name}",
            description=(
                f"**{len(all_warns)}** Warn(s) insgesamt "
                f"({active_count} aktiv, {expired_count} abgelaufen)\n"
                f"Aktuelle Punkte: **{total_points}**"
            ),
            color=self._points_color(total_points),
        )

        for i, warn in enumerate(all_warns[:20], 1):
            # Datum formatieren
            warned_at = warn.get("warned_at", "?")
            try:
                dt = datetime.fromisoformat(warned_at)
                warned_at = dt.strftime("%d.%m.%Y %H:%M")
            except (ValueError, TypeError):
                pass

            # Status-Indikator
            is_expired = warn.get("expired", False)
            status = "[ABGELAUFEN] " if is_expired else ""
            points_display = f"~~{warn.get('points', 1)}~~" if is_expired else str(warn.get("points", 1))

            embed.add_field(
                name=f"{i}. {status}{points_display} Punkt(e) — {warned_at}",
                value=(
                    f"**Grund:** {warn.get('reason', 'Kein Grund')}\n"
                    f"**Von:** {warn.get('warned_by', '?')}\n"
                    f"**ID:** `{warn.get('id', '?')[:8]}...`"
                ),
                inline=False,
            )

        if len(all_warns) > 20:
            embed.set_footer(
                text=f"... und {len(all_warns) - 20} weitere Warns"
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ==================================================================
    # Auto-Aktionen (Mute, Kick, Ban)
    # ==================================================================

    async def _execute_auto_action(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        total_points: int,
    ) -> None:
        """
        Automatische Aktion basierend auf Gesamtpunkten ausführen.

        Schwellenwerte (konfigurierbar im WarnManager):
          3+  -> Mute (Discord Timeout 1h)
          6+  -> Kick
          10+ -> Ban
        """
        action = self.warn_mgr.get_threshold_action(user.id)
        if action is None:
            return

        try:
            if action == "ban":
                await self._auto_ban(interaction, user, total_points)
            elif action == "kick":
                await self._auto_kick(interaction, user, total_points)
            elif action == "mute":
                await self._auto_mute(interaction, user, total_points)
        except Exception as e:
            logger.error(
                f"Auto-Aktion '{action}' fehlgeschlagen für {user}: {e}",
                exc_info=True,
            )
            try:
                await interaction.followup.send(
                    f"Auto-Aktion ({action}) konnte nicht ausgefuehrt werden: {e}",
                    ephemeral=True,
                )
            except Exception as e:
                logger.debug(f"Exception swallowed (B110-refactor 3.1): {e}")

    async def _auto_mute(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        total_points: int,
    ) -> None:
        """Discord Timeout (Mute) für 1 Stunde setzen"""
        duration = timedelta(hours=1)
        reason = f"Auto-Mute: {total_points} Warn-Punkte erreicht"

        try:
            await user.timeout(duration, reason=reason)

            embed = warning_embed(
                title="Auto-Mute ausgefuehrt",
                description=(
                    f"{user.mention} wurde für **1 Stunde** stummgeschaltet.\n"
                    f"Grund: {total_points} Warn-Punkte "
                    f"(Schwelle: {self.warn_mgr.thresholds.get('mute', 3)})"
                ),
            )
            await interaction.followup.send(embed=embed)
            logger.info(f"Auto-Mute: {user} ({total_points} Punkte)")

        except discord.Forbidden:
            await interaction.followup.send(
                f"Auto-Mute fehlgeschlagen: Keine Berechtigung für {user.display_name}.",
                ephemeral=True,
            )
        except discord.HTTPException as e:
            await interaction.followup.send(
                f"Auto-Mute fehlgeschlagen: {e}",
                ephemeral=True,
            )

    async def _auto_kick(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        total_points: int,
    ) -> None:
        """User vom Server kicken"""
        reason = f"Auto-Kick: {total_points} Warn-Punkte erreicht"

        # DM vor dem Kick senden
        try:
            dm_embed = error_embed(
                title="Du wurdest vom Server gekickt",
                description=(
                    f"Du hast **{total_points} Warn-Punkte** erreicht und wurdest "
                    f"automatisch vom Server gekickt.\n\n"
                    f"Du kannst dem Server erneut beitreten, "
                    f"aber weitere Verwarnungen fuehren zum Ban."
                ),
            )
            await user.send(embed=dm_embed)
        except (discord.Forbidden, discord.HTTPException):
            pass

        try:
            await interaction.guild.kick(user, reason=reason)

            embed = error_embed(
                title="Auto-Kick ausgefuehrt",
                description=(
                    f"**{user.display_name}** wurde vom Server gekickt.\n"
                    f"Grund: {total_points} Warn-Punkte "
                    f"(Schwelle: {self.warn_mgr.thresholds.get('kick', 6)})"
                ),
            )
            await interaction.followup.send(embed=embed)
            logger.info(f"Auto-Kick: {user} ({total_points} Punkte)")

        except discord.Forbidden:
            await interaction.followup.send(
                f"Auto-Kick fehlgeschlagen: Keine Berechtigung für {user.display_name}.",
                ephemeral=True,
            )
        except discord.HTTPException as e:
            await interaction.followup.send(
                f"Auto-Kick fehlgeschlagen: {e}",
                ephemeral=True,
            )

    async def _auto_ban(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        total_points: int,
    ) -> None:
        """User vom Server bannen"""
        reason = f"Auto-Ban: {total_points} Warn-Punkte erreicht"

        # DM vor dem Ban senden
        try:
            dm_embed = error_embed(
                title="Du wurdest vom Server gebannt",
                description=(
                    f"Du hast **{total_points} Warn-Punkte** erreicht und wurdest "
                    f"automatisch vom Server gebannt.\n\n"
                    f"Wende dich an einen Admin, falls du denkst, "
                    f"dass dies ein Fehler ist."
                ),
            )
            await user.send(embed=dm_embed)
        except (discord.Forbidden, discord.HTTPException):
            pass

        try:
            await interaction.guild.ban(
                user,
                reason=reason,
                delete_message_days=0,  # Nachrichten nicht löschen
            )

            embed = error_embed(
                title="Auto-Ban ausgefuehrt",
                description=(
                    f"**{user.display_name}** wurde vom Server gebannt.\n"
                    f"Grund: {total_points} Warn-Punkte "
                    f"(Schwelle: {self.warn_mgr.thresholds.get('ban', 10)})"
                ),
            )
            await interaction.followup.send(embed=embed)
            logger.info(f"Auto-Ban: {user} ({total_points} Punkte)")

        except discord.Forbidden:
            await interaction.followup.send(
                f"Auto-Ban fehlgeschlagen: Keine Berechtigung für {user.display_name}.",
                ephemeral=True,
            )
        except discord.HTTPException as e:
            await interaction.followup.send(
                f"Auto-Ban fehlgeschlagen: {e}",
                ephemeral=True,
            )

    # ==================================================================
    # Hilfsmethoden
    # ==================================================================

    def _resolve_warn_id(self, user_id: int, partial_id: str) -> Optional[str]:
        """
        Warn-ID aufloesen — unterstuetzt abgekuerzte IDs (erste 8 Zeichen).

        Args:
            user_id: Discord-User-ID
            partial_id: Vollstaendige oder abgekuerzte Warn-ID

        Returns:
            Vollstaendige Warn-ID oder None wenn nicht gefunden
        """
        all_warns = self.warn_mgr.get_all_warns(user_id)
        partial_clean = partial_id.strip().rstrip(".")

        for warn in all_warns:
            warn_id = warn.get("id", "")
            if warn_id == partial_clean or warn_id.startswith(partial_clean):
                return warn_id

        return None

    async def _notify_user_dm(
        self,
        user: discord.Member,
        grund: app_commands.Range[str, 1, 500],
        punkte: int,
        total_points: int,
    ) -> bool:
        """
        User per DM über Verwarnung benachrichtigen.

        Args:
            user: Der verwarnte User
            grund: Grund der Verwarnung
            punkte: Punkte dieses Warns
            total_points: Gesamtpunktzahl

        Returns:
            True wenn DM erfolgreich gesendet wurde
        """
        try:
            embed = warning_embed(
                title="Du wurdest verwarnt",
            )
            embed.add_field(name="Grund", value=grund, inline=False)
            embed.add_field(name="Punkte", value=f"+{punkte}", inline=True)
            embed.add_field(
                name="Gesamt", value=f"{total_points} Punkt(e)", inline=True
            )

            # Schwellenwert-Warnung
            threshold_info = self._threshold_warning(total_points)
            if threshold_info:
                embed.add_field(
                    name="Achtung",
                    value=threshold_info,
                    inline=False,
                )

            await user.send(embed=embed)
            return True

        except (discord.Forbidden, discord.HTTPException):
            logger.debug(f"DM an {user} konnte nicht gesendet werden")
            return False

    def _threshold_warning(self, total_points: int) -> Optional[str]:
        """
        Warnung generieren basierend auf aktueller Punktzahl.

        Returns:
            Warntext oder None wenn kein Schwellenwert nahe
        """
        mute_thresh = self.warn_mgr.thresholds.get("mute", 3)
        kick_thresh = self.warn_mgr.thresholds.get("kick", 6)
        ban_thresh = self.warn_mgr.thresholds.get("ban", 10)

        if total_points >= ban_thresh:
            return "Du wurdest automatisch gebannt!"
        if total_points >= kick_thresh:
            return "Du wurdest automatisch gekickt!"
        if total_points >= mute_thresh:
            return (
                f"Du wurdest automatisch stummgeschaltet (1h)! "
                f"Bei {kick_thresh} Punkten wirst du gekickt."
            )

        # Naechster Schwellenwert
        if total_points >= mute_thresh - 1:
            return f"Noch {mute_thresh - total_points} Punkt(e) bis zur Stummschaltung!"

        return None

    def _threshold_status(self, total_points: int) -> Optional[str]:
        """
        Status-Text für Embed basierend auf Schwellenwerten.

        Returns:
            Status-String oder None
        """
        mute_thresh = self.warn_mgr.thresholds.get("mute", 3)
        kick_thresh = self.warn_mgr.thresholds.get("kick", 6)
        ban_thresh = self.warn_mgr.thresholds.get("ban", 10)

        parts: list[str] = []
        if total_points >= ban_thresh:
            parts.append(f"**BAN-SCHWELLE ERREICHT** ({ban_thresh}+)")
        elif total_points >= kick_thresh:
            parts.append(f"**KICK-SCHWELLE ERREICHT** ({kick_thresh}+)")
        elif total_points >= mute_thresh:
            parts.append(f"**MUTE-SCHWELLE ERREICHT** ({mute_thresh}+)")

        parts.append(
            f"Schwellen: Mute={mute_thresh} | Kick={kick_thresh} | Ban={ban_thresh}"
        )

        return "\n".join(parts)

    def _points_color(self, total_points: int) -> int:
        """Embed-Farbe nach Punktestand — aus der zentralen Palette.

        Die Schwellen kommen aus der Konfiguration, nicht mehr aus fest
        verdrahteten Zahlen: wer Mute/Kick/Ban verschiebt, verschiebt damit auch
        die Farbe.
        """
        if total_points >= self.warn_mgr.thresholds.get("kick", 6):
            return COLOR_ERROR
        if total_points >= self.warn_mgr.thresholds.get("mute", 3):
            return COLOR_WARNING
        if total_points >= 1:
            return COLOR_BRAND
        return COLOR_SUCCESS

    # ==================================================================
    # Fehlerbehandlung
    # ==================================================================

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Zentrale Fehlerbehandlung für alle Commands in dieser Cog"""
        if isinstance(error, app_commands.CheckFailure):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Keine Berechtigung für diesen Befehl.", ephemeral=True
                )
            return

        cmd_name = interaction.command.name if interaction.command else "unknown"
        logger.error(f"Command error in {cmd_name}: {error}", exc_info=True)
        try:
            msg = f"Ein Fehler ist aufgetreten: {error}"
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception as e:
            logger.debug(f"Exception swallowed (B110-refactor 3.1): {e}")


async def setup(bot: commands.Bot) -> None:
    """Cog laden"""
    await bot.add_cog(WarnCog(bot))
