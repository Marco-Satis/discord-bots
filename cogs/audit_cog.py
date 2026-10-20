"""
Audit Cog — Phase 11g
Cog für den Admin Bot: Leitet Discord-Events an den AuditLogger weiter
und bietet Konfigurations-Commands für die Audit-Kanaele.

Event-Listener:
  - on_member_join / on_member_remove (Kick ueber das Discord-Audit-Log erkannt)
  - on_member_update (Nick-Aenderungen, Rollen-Aenderungen, Timeout)
  - on_guild_channel_create / on_guild_channel_delete
  - on_guild_role_create / on_guild_role_delete
  - seit 20.10.2026: on_message_delete, on_raw_message_delete (nicht im Cache),
    on_raw_bulk_message_delete, on_message_edit, on_voice_state_update,
    on_member_ban / on_member_unban, on_invite_create / on_invite_delete

Commands:
  /audit setup <kategorie> <channel>  — Admin: Kanal für Log-Kategorie setzen
  /audit status                       — Admin: Aktuelle Audit-Konfiguration anzeigen
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from modules.audit_logger import AuditLogger, CATEGORY_TO_KEY
from utils.logger import get_logger
from utils.permissions import admin_only
from utils.embeds import (
    error_embed,
    info_embed,
    success_embed,
)

logger = get_logger("cogs.audit")

# Auswahl-Optionen für Log-Kategorien
_CATEGORY_CHOICES = [
    app_commands.Choice(name="Moderation", value="mod"),
    app_commands.Choice(name="Joins / Leaves", value="join"),
    app_commands.Choice(name="Member (Nick/Avatar)", value="member"),
    app_commands.Choice(name="Rollen-Aenderungen", value="role"),
    app_commands.Choice(name="Server (Channels/Rollen/Invites)", value="server"),
    app_commands.Choice(name="Nachrichten (gelöscht/bearbeitet)", value="message"),
    app_commands.Choice(name="Voice (beitreten/wechseln/verlassen)", value="voice"),
]

# Lesbare Namen für die Status-Anzeige
_CATEGORY_LABELS = {
    "mod_log_channel": "Moderation",
    "join_log_channel": "Joins / Leaves",
    "member_log_channel": "Member (Nick/Avatar)",
    "role_log_channel": "Rollen-Aenderungen",
    "server_log_channel": "Server (Channels/Rollen/Invites)",
    "message_log_channel": "Nachrichten",
    "voice_log_channel": "Voice",
}

# Discord schreibt Audit-Log-Eintraege leicht verzoegert: kurz warten, dann suchen
_AUDIT_WAIT_S = 2.0
# Eintraege aelter als das gehoeren nicht zum aktuellen Ereignis
_AUDIT_MAX_AGE_S = 20.0


class AuditCog(commands.Cog):
    """
    Audit-Logging-Cog für den Admin Bot.

    Faengt relevante Discord-Events ab und leitet sie an den
    AuditLogger weiter, der die Embeds in die konfigurierten
    Kanaele sendet.
    """

    audit_grp = app_commands.Group(
        name="audit",
        description="Audit-Logging konfigurieren",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.audit = AuditLogger()
        # AuditLogger auf dem Bot verfügbar machen (für andere Cogs, z.B. Mod-Cog)
        bot.audit_logger = self.audit
        logger.info("AuditCog initialisiert")

    # ==================================================================
    # Event-Listener
    # ==================================================================

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Neues Mitglied beigetreten"""
        try:
            await self.audit.log_join(self.bot, member)
        except Exception as e:
            logger.error(f"Fehler beim Loggen von member_join: {e}", exc_info=True)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """Mitglied hat den Server verlassen (oder wurde gekickt)"""
        try:
            await self.audit.log_leave(self.bot, member)
            kick = await self._audit_eintrag(member.guild, discord.AuditLogAction.kick, member.id)
            if kick:
                await self.audit.log_mod_action(self.bot, "Kick", member, kick.user, kick.reason)
        except Exception as e:
            logger.error(f"Fehler beim Loggen von member_remove: {e}", exc_info=True)

    async def _audit_eintrag(
        self, guild: discord.Guild, action: discord.AuditLogAction, target_id: int
    ) -> Optional[discord.AuditLogEntry]:
        """Passenden, frischen Discord-Audit-Log-Eintrag suchen (Moderator + Grund).

        Ohne VIEW_AUDIT_LOG oder bei API-Fehlern: None, das Ereignis wird
        trotzdem geloggt (Moderator dann „unbekannt“).
        """
        await asyncio.sleep(_AUDIT_WAIT_S)
        jetzt = datetime.now(timezone.utc)
        try:
            async for eintrag in guild.audit_logs(limit=6, action=action):
                ziel = getattr(eintrag.target, "id", None)
                if ziel == target_id and (jetzt - eintrag.created_at).total_seconds() < _AUDIT_MAX_AGE_S:
                    return eintrag
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.debug(f"Audit-Log nicht lesbar ({action}): {e}")
        return None

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        """Bann (auch ueber Discord selbst oder andere Bots)"""
        try:
            eintrag = await self._audit_eintrag(guild, discord.AuditLogAction.ban, user.id)
            await self.audit.log_mod_action(
                self.bot, "Bann", user,
                eintrag.user if eintrag else None, eintrag.reason if eintrag else None,
            )
        except Exception as e:
            logger.error(f"Fehler beim Loggen von member_ban: {e}", exc_info=True)

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        """Bann aufgehoben"""
        try:
            eintrag = await self._audit_eintrag(guild, discord.AuditLogAction.unban, user.id)
            await self.audit.log_mod_action(
                self.bot, "Bann aufgehoben", user,
                eintrag.user if eintrag else None, eintrag.reason if eintrag else None,
            )
        except Exception as e:
            logger.error(f"Fehler beim Loggen von member_unban: {e}", exc_info=True)

    async def _timeout_loggen(self, before: discord.Member, after: discord.Member) -> None:
        """Timeout gesetzt oder aufgehoben (communication_disabled_until)."""
        if before.timed_out_until == after.timed_out_until:
            return
        jetzt = datetime.now(timezone.utc)
        aktiv = after.timed_out_until is not None and after.timed_out_until > jetzt
        eintrag = await self._audit_eintrag(
            after.guild, discord.AuditLogAction.member_update, after.id
        )
        await self.audit.log_mod_action(
            self.bot, "Timeout" if aktiv else "Timeout aufgehoben", after,
            eintrag.user if eintrag else None, eintrag.reason if eintrag else None,
            until=after.timed_out_until if aktiv else None,
        )

    # --- Nachrichten -------------------------------------------------------

    def _nachricht_loggen(self, message: discord.Message) -> bool:
        """Nur Menschen, nur Server-Kanaele, nie die Log-Kanaele selbst."""
        return (
            message.guild is not None
            and not message.author.bot
            and not self.audit.is_log_channel(message.channel.id)
        )

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        """Geloeschte Nachricht aus dem Cache (mit Inhalt)"""
        try:
            if self._nachricht_loggen(message):
                await self.audit.log_message_delete(self.bot, message)
        except Exception as e:
            logger.error(f"Fehler beim Loggen von message_delete: {e}", exc_info=True)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        """Geloeschte Nachricht, die nicht im Cache lag (sonst on_message_delete)"""
        try:
            if (
                payload.cached_message is None
                and payload.guild_id is not None
                and not self.audit.is_log_channel(payload.channel_id)
            ):
                await self.audit.log_message_delete_uncached(
                    self.bot, payload.channel_id, payload.message_id
                )
        except Exception as e:
            logger.error(f"Fehler beim Loggen von raw_message_delete: {e}", exc_info=True)

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(
        self, payload: discord.RawBulkMessageDeleteEvent
    ) -> None:
        """Massenloeschung (z. B. /clear)"""
        try:
            if payload.guild_id is not None and not self.audit.is_log_channel(payload.channel_id):
                await self.audit.log_bulk_delete(
                    self.bot, payload.channel_id, len(payload.message_ids)
                )
        except Exception as e:
            logger.error(f"Fehler beim Loggen von bulk_delete: {e}", exc_info=True)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        """Bearbeitete Nachricht (nur echte Textaenderungen, keine Link-Vorschauen)"""
        try:
            if self._nachricht_loggen(after) and before.content != after.content:
                await self.audit.log_message_edit(self.bot, before, after)
        except Exception as e:
            logger.error(f"Fehler beim Loggen von message_edit: {e}", exc_info=True)

    # --- Voice und Invites -------------------------------------------------

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        """Voice beitreten / wechseln / verlassen (Bots ausgenommen)"""
        try:
            if not member.bot:
                await self.audit.log_voice(self.bot, member, before, after)
        except Exception as e:
            logger.error(f"Fehler beim Loggen von voice_state_update: {e}", exc_info=True)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        """Einladung erstellt"""
        try:
            if invite.guild is not None:
                await self.audit.log_invite(self.bot, invite, created=True)
        except Exception as e:
            logger.error(f"Fehler beim Loggen von invite_create: {e}", exc_info=True)

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite) -> None:
        """Einladung geloescht oder abgelaufen"""
        try:
            if invite.guild is not None:
                await self.audit.log_invite(self.bot, invite, created=False)
        except Exception as e:
            logger.error(f"Fehler beim Loggen von invite_delete: {e}", exc_info=True)

    @commands.Cog.listener()
    async def on_member_update(
        self, before: discord.Member, after: discord.Member
    ) -> None:
        """
        Member-Update: Nick-Aenderungen, Avatar-Aenderungen, Rollen-Aenderungen.

        Discord feuert on_member_update für alle Aenderungen am Member-Objekt.
        Wir prüfen hier welche Aenderung vorliegt und loggen entsprechend.
        """
        try:
            # Nick- und Avatar-Aenderungen
            if before.nick != after.nick or before.guild_avatar != after.guild_avatar:
                await self.audit.log_member_update(self.bot, before, after)

            # Rollen-Aenderungen (separates Log)
            if before.roles != after.roles:
                await self.audit.log_role_change(
                    self.bot, after, before.roles, after.roles
                )

            # Timeout gesetzt/aufgehoben (Moderations-Log)
            await self._timeout_loggen(before, after)
        except Exception as e:
            logger.error(f"Fehler beim Loggen von member_update: {e}", exc_info=True)

    @commands.Cog.listener()
    async def on_guild_channel_create(
        self, channel: discord.abc.GuildChannel
    ) -> None:
        """Neuer Kanal erstellt"""
        try:
            await self.audit.log_channel_change(self.bot, "erstellt", channel)
        except Exception as e:
            logger.error(f"Fehler beim Loggen von channel_create: {e}", exc_info=True)

    @commands.Cog.listener()
    async def on_guild_channel_delete(
        self, channel: discord.abc.GuildChannel
    ) -> None:
        """Kanal gelöscht"""
        try:
            await self.audit.log_channel_change(self.bot, "gelöscht", channel)
        except Exception as e:
            logger.error(f"Fehler beim Loggen von channel_delete: {e}", exc_info=True)

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        """Neue Rolle erstellt"""
        try:
            await self.audit.log_role_create(self.bot, role)
        except Exception as e:
            logger.error(f"Fehler beim Loggen von role_create: {e}", exc_info=True)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        """Rolle gelöscht"""
        try:
            await self.audit.log_role_delete(self.bot, role)
        except Exception as e:
            logger.error(f"Fehler beim Loggen von role_delete: {e}", exc_info=True)

    # ==================================================================
    # Slash-Commands
    # ==================================================================

    @audit_grp.command(
        name="setup",
        description="Log-Kanal für eine Audit-Kategorie festlegen (Admin)",
    )
    @app_commands.describe(
        kategorie="Welche Log-Kategorie konfiguriert werden soll",
        channel="Der Ziel-Kanal für diese Kategorie",
    )
    @app_commands.choices(kategorie=_CATEGORY_CHOICES)
    @admin_only()
    async def audit_setup(
        self,
        interaction: discord.Interaction,
        kategorie: app_commands.Choice[str],
        channel: discord.TextChannel,
    ) -> None:
        """Kanal für eine Audit-Kategorie setzen"""
        await interaction.response.defer(ephemeral=True)

        success = self.audit.set_channel(kategorie.value, channel.id)

        if success:
            embed = success_embed(
                title="Audit-Kanal konfiguriert",
                description=(
                    f"**{kategorie.name}** wird jetzt in {channel.mention} geloggt."
                ),
            )
            logger.info(
                f"Audit-Setup: {kategorie.name} -> #{channel.name} "
                f"(von {interaction.user})"
            )
        else:
            embed = error_embed(
                title="Fehler",
                description=f"Ungueltige Kategorie: `{kategorie.value}`",
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @audit_grp.command(
        name="status",
        description="Aktuelle Audit-Konfiguration anzeigen (Admin)",
    )
    @admin_only()
    async def audit_status(self, interaction: discord.Interaction) -> None:
        """Aktuelle Audit-Konfiguration als Embed anzeigen"""
        await interaction.response.defer(ephemeral=True)

        config = self.audit.get_config()

        embed = info_embed(
            title="Audit-Log Konfiguration",
            description="Übersicht der konfigurierten Log-Kanaele:",
        )

        for config_key, label in _CATEGORY_LABELS.items():
            channel_id = config.get(config_key, 0)
            if channel_id:
                channel = self.bot.get_channel(channel_id)
                if channel:
                    value = f"{channel.mention} (`{channel.id}`)"
                else:
                    value = f"Kanal nicht gefunden (`{channel_id}`)"
            else:
                value = "Nicht konfiguriert"

            embed.add_field(name=label, value=value, inline=False)

        embed.set_footer(
            text="Nutze /audit setup <kategorie> <channel> zum Aendern"
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

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
    await bot.add_cog(AuditCog(bot))
