"""
Audit Cog — Phase 11g
Cog für den Admin Bot: Leitet Discord-Events an den AuditLogger weiter
und bietet Konfigurations-Commands für die Audit-Kanaele.

Event-Listener:
  - on_member_join / on_member_remove
  - on_member_update (Nick-Aenderungen, Rollen-Aenderungen)
  - on_guild_channel_create / on_guild_channel_delete
  - on_guild_role_create / on_guild_role_delete

Commands:
  /audit setup <kategorie> <channel>  — Admin: Kanal für Log-Kategorie setzen
  /audit status                       — Admin: Aktuelle Audit-Konfiguration anzeigen
"""

from __future__ import annotations

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
    app_commands.Choice(name="Server (Channels/Rollen)", value="server"),
]

# Lesbare Namen für die Status-Anzeige
_CATEGORY_LABELS = {
    "mod_log_channel": "Moderation",
    "join_log_channel": "Joins / Leaves",
    "member_log_channel": "Member (Nick/Avatar)",
    "role_log_channel": "Rollen-Aenderungen",
    "server_log_channel": "Server (Channels/Rollen)",
}


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
        """Mitglied hat den Server verlassen"""
        try:
            await self.audit.log_leave(self.bot, member)
        except Exception as e:
            logger.error(f"Fehler beim Loggen von member_remove: {e}", exc_info=True)

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
