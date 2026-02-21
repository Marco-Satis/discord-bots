"""
Warn Cog — Phase 11c: Warn-System fuer den Admin Bot

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
        grund="Grund fuer die Verwarnung",
        punkte="Punktzahl (Standard: 1, max. 10)",
    )
    @admin_only()
    async def warn_add(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        grund: str,
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
                "Bots koennen nicht verwarnt werden.", ephemeral=True
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

        # Response-Embed
        embed = discord.Embed(
            title="Verwarnung erteilt",
            color=0xe67e22,
            timestamp=datetime.now(),
        )
        embed.add_field(name="User", value=user.mention, inline=True)
        embed.add_field(name="Punkte", value=f"+{punkte}", inline=True)
        embed.add_field(
            name="Gesamt",
            value=f"{total_points} Punkt(e) ({len(active_warns)} aktive Warns)",
            inline=True,
        )
        embed.add_field(name="Grund", value=grund, inline=False)
        embed.add_field(
            name="Warn-ID",
            value=f"`{warn_entry['id'][:8]}...`",
            inline=True,
        )
        embed.set_footer(text=f"von {interaction.user.display_name}")

        # User per DM benachrichtigen (fehlertolerant)
        dm_sent = await self._notify_user_dm(
            user,
            grund=grund,
            punkte=punkte,
            total_points=total_points,
        )
        if dm_sent:
            embed.add_field(name="DM", value="Gesendet", inline=True)
        else:
            embed.add_field(name="DM", value="Nicht moeglich", inline=True)

        await interaction.followup.send(embed=embed)

        # Auto-Aktion pruefen und ausfuehren
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
                f"Warn-ID `{warn_id}` nicht gefunden fuer {user.display_name}.",
                ephemeral=True,
            )
            return

        success = self.warn_mgr.remove_warn(user.id, resolved_id)

        if success:
            total_points = self.warn_mgr.get_total_points(user.id)
            embed = discord.Embed(
                title="Warn entfernt",
                description=(
                    f"Warn `{resolved_id[:8]}...` von {user.mention} entfernt.\n"
                    f"Verbleibende Punkte: **{total_points}**"
                ),
                color=0x2ecc71,
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
                f"Existiert die ID `{warn_id}` fuer {user.display_name}?",
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
        Automatische Aktion basierend auf Gesamtpunkten ausfuehren.

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
                f"Auto-Aktion '{action}' fehlgeschlagen fuer {user}: {e}",
                exc_info=True,
            )
            try:
                await interaction.followup.send(
                    f"Auto-Aktion ({action}) konnte nicht ausgefuehrt werden: {e}",
                    ephemeral=True,
                )
            except Exception:
                pass

    async def _auto_mute(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        total_points: int,
    ) -> None:
        """Discord Timeout (Mute) fuer 1 Stunde setzen"""
        duration = timedelta(hours=1)
        reason = f"Auto-Mute: {total_points} Warn-Punkte erreicht"

        try:
            await user.timeout(duration, reason=reason)

            embed = discord.Embed(
                title="Auto-Mute ausgefuehrt",
                description=(
                    f"{user.mention} wurde fuer **1 Stunde** stummgeschaltet.\n"
                    f"Grund: {total_points} Warn-Punkte "
                    f"(Schwelle: {self.warn_mgr.thresholds.get('mute', 3)})"
                ),
                color=0xf39c12,
            )
            await interaction.followup.send(embed=embed)
            logger.info(f"Auto-Mute: {user} ({total_points} Punkte)")

        except discord.Forbidden:
            await interaction.followup.send(
                f"Auto-Mute fehlgeschlagen: Keine Berechtigung fuer {user.display_name}.",
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
            dm_embed = discord.Embed(
                title="Du wurdest vom Server gekickt",
                description=(
                    f"Du hast **{total_points} Warn-Punkte** erreicht und wurdest "
                    f"automatisch vom Server gekickt.\n\n"
                    f"Du kannst dem Server erneut beitreten, "
                    f"aber weitere Verwarnungen fuehren zum Ban."
                ),
                color=0xe74c3c,
            )
            await user.send(embed=dm_embed)
        except (discord.Forbidden, discord.HTTPException):
            pass

        try:
            await interaction.guild.kick(user, reason=reason)

            embed = discord.Embed(
                title="Auto-Kick ausgefuehrt",
                description=(
                    f"**{user.display_name}** wurde vom Server gekickt.\n"
                    f"Grund: {total_points} Warn-Punkte "
                    f"(Schwelle: {self.warn_mgr.thresholds.get('kick', 6)})"
                ),
                color=0xe74c3c,
            )
            await interaction.followup.send(embed=embed)
            logger.info(f"Auto-Kick: {user} ({total_points} Punkte)")

        except discord.Forbidden:
            await interaction.followup.send(
                f"Auto-Kick fehlgeschlagen: Keine Berechtigung fuer {user.display_name}.",
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
            dm_embed = discord.Embed(
                title="Du wurdest vom Server gebannt",
                description=(
                    f"Du hast **{total_points} Warn-Punkte** erreicht und wurdest "
                    f"automatisch vom Server gebannt.\n\n"
                    f"Wende dich an einen Admin, falls du denkst, "
                    f"dass dies ein Fehler ist."
                ),
                color=0xe74c3c,
            )
            await user.send(embed=dm_embed)
        except (discord.Forbidden, discord.HTTPException):
            pass

        try:
            await interaction.guild.ban(
                user,
                reason=reason,
                delete_message_days=0,  # Nachrichten nicht loeschen
            )

            embed = discord.Embed(
                title="Auto-Ban ausgefuehrt",
                description=(
                    f"**{user.display_name}** wurde vom Server gebannt.\n"
                    f"Grund: {total_points} Warn-Punkte "
                    f"(Schwelle: {self.warn_mgr.thresholds.get('ban', 10)})"
                ),
                color=0xe74c3c,
            )
            await interaction.followup.send(embed=embed)
            logger.info(f"Auto-Ban: {user} ({total_points} Punkte)")

        except discord.Forbidden:
            await interaction.followup.send(
                f"Auto-Ban fehlgeschlagen: Keine Berechtigung fuer {user.display_name}.",
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
        grund: str,
        punkte: int,
        total_points: int,
    ) -> bool:
        """
        User per DM ueber Verwarnung benachrichtigen.

        Args:
            user: Der verwarnte User
            grund: Grund der Verwarnung
            punkte: Punkte dieses Warns
            total_points: Gesamtpunktzahl

        Returns:
            True wenn DM erfolgreich gesendet wurde
        """
        try:
            embed = discord.Embed(
                title="Du wurdest verwarnt",
                color=0xe67e22,
                timestamp=datetime.now(),
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
        Status-Text fuer Embed basierend auf Schwellenwerten.

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

    @staticmethod
    def _points_color(total_points: int) -> int:
        """Embed-Farbe basierend auf Punktzahl"""
        if total_points >= 10:
            return 0xFF0000  # Rot (Ban-Bereich)
        if total_points >= 6:
            return 0xE74C3C  # Dunkelrot (Kick-Bereich)
        if total_points >= 3:
            return 0xE67E22  # Orange (Mute-Bereich)
        if total_points >= 1:
            return 0xF1C40F  # Gelb (Warns vorhanden)
        return 0x2ECC71  # Gruen (keine Warns)

    # ==================================================================
    # Fehlerbehandlung
    # ==================================================================

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Zentrale Fehlerbehandlung fuer alle Commands in dieser Cog"""
        if isinstance(error, app_commands.CheckFailure):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Keine Berechtigung fuer diesen Befehl.", ephemeral=True
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
        except Exception:
            pass


async def setup(bot: commands.Bot) -> None:
    """Cog laden"""
    await bot.add_cog(WarnCog(bot))
