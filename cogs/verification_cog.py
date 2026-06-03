"""
Verification + Raid-Schutz Cog (Phase G Security).

  - Verification-Gate: persistenter „Verifizieren"-Button vergibt eine
    Verified-Rolle an neue Member (gegen Raid-Bots / Scanner).
  - Raid-Detection: Join-Spike (zu viele Joins im Zeitfenster) -> Alert in
    einen konfigurierten Channel.

Config je Guild in `guild_config` (Prefix `security.`):
  verification_enabled, verification_role_id,
  raid_threshold, raid_window_seconds, raid_alert_channel_id

Commands (Admin): /sicherheit verifizierung|verifizierung_aus|raidschutz|
raidschutz_aus|status
"""
from __future__ import annotations

import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from modules.guild_context import GuildConfig
from modules.raid_detector import RaidDetector
from utils.logger import get_logger
from utils.permissions import admin_only

logger = get_logger("cogs.verification")


class VerificationView(discord.ui.View):
    """Persistenter Verifizieren-Button (timeout=None, fester custom_id)."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Verifizieren",
        style=discord.ButtonStyle.success,
        custom_id="verify:confirm",
        emoji="✅",
    )
    async def verify_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        """Vergibt die konfigurierte Verified-Rolle an den Klickenden."""
        if interaction.guild is None or not isinstance(
            interaction.user, discord.Member
        ):
            await interaction.response.send_message(
                "Nur auf einem Server nutzbar.", ephemeral=True
            )
            return

        # Gate respektieren: deaktivierte Verifizierung vergibt keine Rolle
        # (der persistente Button ueberlebt /verifizierung_aus).
        enabled = await GuildConfig.get(
            interaction.guild.id, "security.verification_enabled", False
        )
        if not enabled:
            await interaction.response.send_message(
                "Verifizierung ist derzeit deaktiviert.", ephemeral=True
            )
            return

        role_id = await GuildConfig.get(
            interaction.guild.id, "security.verification_role_id"
        )
        if not role_id:
            await interaction.response.send_message(
                "Verifizierung ist nicht konfiguriert.", ephemeral=True
            )
            return

        try:
            role = interaction.guild.get_role(int(role_id))
        except (TypeError, ValueError):
            role = None
        if role is None:
            await interaction.response.send_message(
                "Verifizierungs-Rolle nicht gefunden — bitte Admin informieren.",
                ephemeral=True,
            )
            return

        if role in interaction.user.roles:
            await interaction.response.send_message(
                "Du bist bereits verifiziert.", ephemeral=True
            )
            return

        try:
            await interaction.user.add_roles(role, reason="Verifizierung")
            await interaction.response.send_message(
                "Verifiziert! Willkommen auf dem Server.", ephemeral=True
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.warning(f"Verify-Rolle nicht vergebbar: {e}")
            await interaction.response.send_message(
                "Konnte die Rolle nicht vergeben (Bot-Rechte / Rollen-Hierarchie?).",
                ephemeral=True,
            )


class VerificationCog(commands.Cog):
    """Verification-Gate + Raid-Detection."""

    sicherheit = app_commands.Group(
        name="sicherheit",
        description="Server-Sicherheit (Verifizierung + Raid-Schutz)",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.raid = RaidDetector()

    async def cog_load(self) -> None:
        """Persistente Verify-View registrieren (ueberlebt Bot-Restart)."""
        self.bot.add_view(VerificationView())
        logger.info("Verification-Cog geladen, persistente View registriert")

    # ==================================================================
    # Raid-Detection
    # ==================================================================

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Join-Spike-Erkennung -> Alert."""
        if member.bot:
            return
        guild = member.guild
        # Korrupte Config-Werte duerfen on_member_join nicht bei jedem Join werfen.
        try:
            threshold = int(
                await GuildConfig.get(guild.id, "security.raid_threshold", 0) or 0
            )
            window = float(
                await GuildConfig.get(guild.id, "security.raid_window_seconds", 10) or 10
            )
        except (TypeError, ValueError):
            return
        if threshold <= 0:
            return

        now = time.time()
        is_raid = self.raid.record_join(
            guild.id, now, threshold=threshold, window_seconds=window
        )
        if is_raid:
            await self._maybe_raid_alert(guild, threshold, window, now)

    async def _maybe_raid_alert(
        self, guild: discord.Guild, threshold: int, window: float, now: float
    ) -> None:
        """Alert senden — Channel ZUERST aufloesen, dann Alarm-Cooldown buchen.

        So wird das Alarm-Fenster nicht still verbraucht wenn gar kein
        Alert-Channel existiert (Review-Finding #2).
        """
        channel_id = await GuildConfig.get(guild.id, "security.raid_alert_channel_id")
        channel = None
        if channel_id:
            try:
                channel = guild.get_channel(int(channel_id))
            except (TypeError, ValueError):
                channel = None
        if channel is None:
            channel = guild.system_channel
        if channel is None or not isinstance(channel, discord.TextChannel):
            logger.warning(f"Raid-Verdacht Guild {guild.id}: kein Alert-Channel")
            return

        # Cooldown erst jetzt buchen (Channel steht) -> kein verbranntes Fenster.
        if not self.raid.should_alert(guild.id, now):
            return

        count = self.raid.join_count(guild.id)
        embed = discord.Embed(
            title="⚠️ Raid-Verdacht",
            description=(
                f"**{count}** Joins in ~{int(window)}s "
                f"(Schwelle: {threshold}).\n"
                "Pruefe die neuen Mitglieder und erwaege einen Lockdown."
            ),
            color=0xE74C3C,
        )
        try:
            await channel.send(
                embed=embed, allowed_mentions=discord.AllowedMentions.none()
            )
            logger.info(f"Raid-Alert gesendet Guild {guild.id}: {count} Joins")
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.warning(f"Raid-Alert nicht sendbar: {e}")

    # ==================================================================
    # Commands
    # ==================================================================

    @sicherheit.command(
        name="verifizierung",
        description="Verifizierungs-Gate einrichten (Button-Rolle)",
    )
    @app_commands.describe(
        rolle="Rolle die verifizierte Mitglieder erhalten",
        channel="Channel in dem der Verify-Button gepostet wird (leer = hier)",
    )
    @admin_only()
    async def setup_verification(
        self,
        interaction: discord.Interaction,
        rolle: discord.Role,
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        """Verify-Rolle setzen + Verify-Nachricht mit Button posten."""
        await interaction.response.defer(ephemeral=True)
        if interaction.guild is None:
            await interaction.followup.send("Nur auf einem Server.", ephemeral=True)
            return

        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.followup.send(
                "Bitte einen Text-Channel angeben.", ephemeral=True
            )
            return

        await GuildConfig.set(
            interaction.guild.id, "security.verification_role_id", str(rolle.id),
            updated_by="command",
        )
        await GuildConfig.set(
            interaction.guild.id, "security.verification_enabled", True,
            updated_by="command",
        )

        embed = discord.Embed(
            title="✅ Verifizierung",
            description=(
                "Klicke auf **Verifizieren**, um Zugriff auf den Server zu "
                "erhalten."
            ),
            color=0x2ECC71,
        )
        try:
            await target.send(embed=embed, view=VerificationView())
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.followup.send(
                f"Verify-Nachricht konnte nicht gepostet werden: {e}", ephemeral=True
            )
            return

        await interaction.followup.send(
            f"Verifizierung aktiv. Rolle: **{rolle.name}**, Button in "
            f"{target.mention}.",
            ephemeral=True,
        )

    @sicherheit.command(
        name="verifizierung_aus",
        description="Verifizierungs-Gate deaktivieren",
    )
    @admin_only()
    async def disable_verification(
        self, interaction: discord.Interaction
    ) -> None:
        """Verifizierung deaktivieren (bestehende Rollen bleiben)."""
        await interaction.response.defer(ephemeral=True)
        if interaction.guild is None:
            await interaction.followup.send("Nur auf einem Server.", ephemeral=True)
            return
        await GuildConfig.set(
            interaction.guild.id, "security.verification_enabled", False,
            updated_by="command",
        )
        await interaction.followup.send("Verifizierung deaktiviert.", ephemeral=True)

    @sicherheit.command(
        name="raidschutz",
        description="Raid-Schutz konfigurieren (Join-Spike-Alert)",
    )
    @app_commands.describe(
        schwelle="Joins die im Fenster einen Alert ausloesen (>=2)",
        fenster_sekunden="Zeitfenster in Sekunden",
        alert_channel="Channel fuer Raid-Alerts (leer = System-Channel)",
    )
    @admin_only()
    async def setup_raid(
        self,
        interaction: discord.Interaction,
        schwelle: int,
        fenster_sekunden: int = 10,
        alert_channel: Optional[discord.TextChannel] = None,
    ) -> None:
        """Raid-Detection-Parameter setzen."""
        await interaction.response.defer(ephemeral=True)
        if interaction.guild is None:
            await interaction.followup.send("Nur auf einem Server.", ephemeral=True)
            return
        if schwelle < 2:
            await interaction.followup.send(
                "Schwelle muss mindestens 2 sein.", ephemeral=True
            )
            return
        if not (1 <= fenster_sekunden <= 600):
            await interaction.followup.send(
                "Fenster muss zwischen 1 und 600 Sekunden liegen.", ephemeral=True
            )
            return

        gid = interaction.guild.id
        await GuildConfig.set(gid, "security.raid_threshold", schwelle, updated_by="command")
        await GuildConfig.set(
            gid, "security.raid_window_seconds", fenster_sekunden, updated_by="command"
        )
        if alert_channel is not None:
            await GuildConfig.set(
                gid, "security.raid_alert_channel_id", str(alert_channel.id),
                updated_by="command",
            )
        await interaction.followup.send(
            f"Raid-Schutz aktiv: {schwelle} Joins / {fenster_sekunden}s.",
            ephemeral=True,
        )

    @sicherheit.command(
        name="raidschutz_aus",
        description="Raid-Schutz deaktivieren",
    )
    @admin_only()
    async def disable_raid(self, interaction: discord.Interaction) -> None:
        """Raid-Schutz aus (threshold=0)."""
        await interaction.response.defer(ephemeral=True)
        if interaction.guild is None:
            await interaction.followup.send("Nur auf einem Server.", ephemeral=True)
            return
        await GuildConfig.set(
            interaction.guild.id, "security.raid_threshold", 0, updated_by="command"
        )
        self.raid.reset(interaction.guild.id)
        await interaction.followup.send("Raid-Schutz deaktiviert.", ephemeral=True)

    @sicherheit.command(
        name="status",
        description="Sicherheits-Einstellungen anzeigen",
    )
    @admin_only()
    async def security_status(self, interaction: discord.Interaction) -> None:
        """Aktuelle Verifizierungs- + Raid-Schutz-Config anzeigen."""
        await interaction.response.defer(ephemeral=True)
        if interaction.guild is None:
            await interaction.followup.send("Nur auf einem Server.", ephemeral=True)
            return
        gid = interaction.guild.id
        verify_on = await GuildConfig.get(gid, "security.verification_enabled", False)
        role_id = await GuildConfig.get(gid, "security.verification_role_id")
        threshold = await GuildConfig.get(gid, "security.raid_threshold", 0)
        window = await GuildConfig.get(gid, "security.raid_window_seconds", 10)

        role = None
        if role_id:
            try:
                role = interaction.guild.get_role(int(role_id))
            except (TypeError, ValueError):
                role = None
        embed = discord.Embed(title="Sicherheits-Status", color=0x5865F2)
        embed.add_field(
            name="Verifizierung",
            value=("Aktiv — Rolle: " + (role.name if role else "?"))
            if verify_on else "Aus",
            inline=False,
        )
        embed.add_field(
            name="Raid-Schutz",
            value=(f"Aktiv — {threshold} Joins / {window}s")
            if threshold and int(threshold) > 0 else "Aus",
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Zentrale Fehlerbehandlung."""
        if isinstance(error, app_commands.CheckFailure):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Keine Berechtigung für diesen Befehl.", ephemeral=True
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
            logger.debug(f"Exception swallowed: {e}")


async def setup(bot: commands.Bot) -> None:
    """Registriert den Verification-Cog beim Bot."""
    await bot.add_cog(VerificationCog(bot))
