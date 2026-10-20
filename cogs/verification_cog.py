"""
Verification + Raid-Schutz Cog (Phase G Security).

  - Verification-Gate: persistenter „Verifizieren"-Button vergibt eine
    Verified-Rolle an neue Member (gegen Raid-Bots / Scanner).
  - Raid-Detection: Join-Spike (zu viele Joins im Zeitfenster) -> Alert in
    einen konfigurierten Channel.
  - Raid-Reaktion (20.10.2026, Marco): bei einem Join-Spike sperrt der Bot
    selbst fuer `raid_lock_minutes` (Standard 10): Einladungen und DMs pausiert
    (Discord-Sicherheitsaktion), Verifizieren pausiert, die Discord-
    Verifizierungsstufe geht auf „Hoch“; danach automatisch zurueck.
    Bewusst KEIN Auto-Kick neuer Konten: gekaufte Boost-Konten sind neu und
    treten gesammelt bei, ein Kick wuerde die Boosts kosten.

Config je Guild in `guild_config` (Prefix `security.`):
  verification_enabled, verification_role_id,
  raid_threshold, raid_window_seconds, raid_alert_channel_id,
  raid_lock_minutes (0 = nur Alarm), raid_lock_until, raid_lock_prev_level

Commands (Admin): /sicherheit verifizierung|verifizierung_aus|raidschutz|
raidschutz_aus|entsperren|status
"""
from __future__ import annotations

import time
from collections import deque
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from modules.guild_context import GuildConfig
from modules.raid_detector import RaidDetector
from utils.logger import get_logger
from utils.permissions import admin_only
from utils.embeds import (
    error_embed,
    info_embed,
    success_embed,
)

logger = get_logger("cogs.verification")

# Standard-Sperrdauer nach einem Join-Spike (Minuten); 0 = nur Alarm
RAID_LOCK_MINUTES_DEFAULT = 10
# Konten juenger als das gelten in der Raid-Meldung als „neu“
NEUES_KONTO_TAGE = 7


def sperre_aktiv(bis: Optional[float], jetzt: float) -> bool:
    """Ist eine Raid-Sperre bis `bis` (Unix-Zeit) zum Zeitpunkt `jetzt` aktiv?"""
    try:
        return bis is not None and float(bis) > jetzt
    except (TypeError, ValueError):
        return False


def rest_minuten(bis: float, jetzt: float) -> int:
    """Verbleibende Sperrzeit, aufgerundet auf ganze Minuten (mindestens 1)."""
    return max(1, int((float(bis) - jetzt + 59) // 60))


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

        # Raid-Sperre: waehrend einer Join-Welle wird niemand verifiziert.
        bis = await GuildConfig.get(interaction.guild.id, "security.raid_lock_until")
        jetzt = time.time()
        if sperre_aktiv(bis, jetzt):
            await interaction.response.send_message(
                "Der Server ist wegen sehr vieler Beitritte kurz gesperrt. "
                f"Bitte versuch es in {rest_minuten(bis, jetzt)} Minute(n) noch einmal.",
                ephemeral=True,
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
        # Letzte Beitritte je Guild (Zeit, Member) fuer die Raid-Meldung
        self._letzte_joins: dict[int, deque] = {}

    async def cog_load(self) -> None:
        """Persistente Verify-View registrieren (ueberlebt Bot-Restart)."""
        self.bot.add_view(VerificationView())
        self.sperren_pruefen.start()
        logger.info("Verification-Cog geladen, persistente View registriert")

    async def cog_unload(self) -> None:
        self.sperren_pruefen.cancel()

    # ==================================================================
    # Raid-Sperre (automatische Reaktion)
    # ==================================================================

    @tasks.loop(seconds=60)
    async def sperren_pruefen(self) -> None:
        """Abgelaufene Raid-Sperren aufheben, auch nach einem Bot-Neustart."""
        jetzt = time.time()
        for guild in self.bot.guilds:
            try:
                bis = await GuildConfig.get(guild.id, "security.raid_lock_until")
                if bis is not None and not sperre_aktiv(bis, jetzt):
                    await self._sperre_aufheben(guild, "Sperrzeit abgelaufen")
            except Exception as e:  # noqa: BLE001 — Loop darf nie sterben
                logger.error(f"Raid-Sperre pruefen Guild {guild.id}: {e}", exc_info=True)

    @sperren_pruefen.before_loop
    async def _vor_sperren_pruefen(self) -> None:
        await self.bot.wait_until_ready()

    async def _sperre_setzen(self, guild: discord.Guild, jetzt: float) -> Optional[float]:
        """Sperre aktivieren (idempotent). Gibt das Sperrende zurueck, None = keine Sperre."""
        try:
            minuten = int(await GuildConfig.get(
                guild.id, "security.raid_lock_minutes", RAID_LOCK_MINUTES_DEFAULT
            ) or 0)
        except (TypeError, ValueError):
            minuten = RAID_LOCK_MINUTES_DEFAULT
        if minuten <= 0:
            return None
        bis_alt = await GuildConfig.get(guild.id, "security.raid_lock_until")
        if sperre_aktiv(bis_alt, jetzt):
            return float(bis_alt)

        bis = jetzt + minuten * 60
        await GuildConfig.set(guild.id, "security.raid_lock_until", bis, updated_by="raid")
        if guild.verification_level < discord.VerificationLevel.high:
            await GuildConfig.set(
                guild.id, "security.raid_lock_prev_level",
                guild.verification_level.value, updated_by="raid",
            )
            try:
                await guild.edit(
                    verification_level=discord.VerificationLevel.high,
                    reason=f"Raid-Schutz: Join-Welle, Sperre {minuten} min",
                )
            except (discord.Forbidden, discord.HTTPException) as e:
                logger.warning(f"Verifizierungsstufe nicht hochsetzbar Guild {guild.id}: {e}")
        # Discords eigene Sicherheitsaktion: Einladungen + DMs pausieren. Laeuft
        # bei Discord selbst ab, auch wenn der Bot zwischendurch ausfaellt.
        try:
            ende = datetime.fromtimestamp(bis, tz=timezone.utc)
            await guild.edit(
                invites_disabled_until=ende, dms_disabled_until=ende,
                reason=f"Raid-Schutz: Einladungen/DMs {minuten} min pausiert",
            )
        except (discord.Forbidden, discord.HTTPException, TypeError) as e:
            logger.warning(f"Einladungen/DMs nicht pausierbar Guild {guild.id}: {e}")
        logger.warning(f"Raid-Sperre aktiv Guild {guild.id} fuer {minuten} min")
        return bis

    async def _sperre_aufheben(self, guild: discord.Guild, grund: str) -> bool:
        """Sperre beenden, Verifizierungsstufe zuruecksetzen, Team informieren."""
        bis = await GuildConfig.get(guild.id, "security.raid_lock_until")
        if bis is None:
            return False
        if sperre_aktiv(bis, time.time()):
            # Vorzeitig aufgehoben: Discords Pause ebenfalls beenden (laeuft
            # sonst bis zum alten Sperrende weiter).
            try:
                await guild.edit(
                    invites_disabled_until=None, dms_disabled_until=None,
                    reason=f"Raid-Schutz: Sperre beendet ({grund})",
                )
            except (discord.Forbidden, discord.HTTPException, TypeError) as e:
                logger.warning(f"Einladungs-/DM-Pause nicht aufhebbar Guild {guild.id}: {e}")
        prev = await GuildConfig.get(guild.id, "security.raid_lock_prev_level")
        if prev is not None:
            try:
                await guild.edit(
                    verification_level=discord.VerificationLevel(int(prev)),
                    reason=f"Raid-Schutz: Sperre beendet ({grund})",
                )
            except (discord.Forbidden, discord.HTTPException, ValueError, TypeError) as e:
                logger.warning(f"Verifizierungsstufe nicht zuruecksetzbar Guild {guild.id}: {e}")
        await GuildConfig.delete(guild.id, "security.raid_lock_until")
        await GuildConfig.delete(guild.id, "security.raid_lock_prev_level")
        logger.info(f"Raid-Sperre aufgehoben Guild {guild.id}: {grund}")

        channel = await self._alert_channel(guild)
        if channel is not None:
            try:
                await channel.send(
                    embed=success_embed(
                        title="Raid-Sperre aufgehoben",
                        description=f"{grund}. Verifizieren geht wieder, die Verifizierungsstufe ist zurückgesetzt.",
                    ),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except (discord.Forbidden, discord.HTTPException) as e:
                logger.warning(f"Entsperr-Meldung nicht sendbar: {e}")
        return True

    async def _alert_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        """Alert-Kanal (Dashboard/Command), sonst Systemkanal."""
        channel_id = await GuildConfig.get(guild.id, "security.raid_alert_channel_id")
        channel = None
        if channel_id:
            try:
                channel = guild.get_channel(int(channel_id))
            except (TypeError, ValueError):
                channel = None
        if channel is None:
            channel = guild.system_channel
        return channel if isinstance(channel, discord.TextChannel) else None

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
        joins = self._letzte_joins.setdefault(guild.id, deque(maxlen=50))
        joins.append((now, member))
        is_raid = self.raid.record_join(
            guild.id, now, threshold=threshold, window_seconds=window
        )
        if is_raid:
            try:
                bis = await self._sperre_setzen(guild, now)
            except Exception as e:  # noqa: BLE001 — Alarm geht trotzdem raus
                logger.error(f"Raid-Sperre setzen Guild {guild.id}: {e}", exc_info=True)
                bis = None
            await self._maybe_raid_alert(guild, threshold, window, now, bis)

    def _joiner_text(self, guild_id: int, seit: float) -> str:
        """Beigetretene seit `seit`, mit Kontoalter; neue Konten markiert (nur Team-Kanal)."""
        zeilen = []
        jetzt = datetime.now(timezone.utc)
        for ts, member in self._letzte_joins.get(guild_id, ()):
            if ts < seit:
                continue
            tage = (jetzt - member.created_at).days
            marke = " ⚠️ neu" if tage < NEUES_KONTO_TAGE else ""
            zeilen.append(f"{member.mention} · Konto {tage} Tag(e){marke}")
        if len(zeilen) > 15:
            zeilen = zeilen[:15] + [f"… und {len(zeilen) - 15} weitere"]
        return "\n".join(zeilen) or "–"

    async def _maybe_raid_alert(
        self, guild: discord.Guild, threshold: int, window: float, now: float,
        gesperrt_bis: Optional[float] = None,
    ) -> None:
        """Alert senden — Channel ZUERST aufloesen, dann Alarm-Cooldown buchen.

        So wird das Alarm-Fenster nicht still verbraucht wenn gar kein
        Alert-Channel existiert (Review-Finding #2).
        """
        channel = await self._alert_channel(guild)
        if channel is None:
            logger.warning(f"Raid-Verdacht Guild {guild.id}: kein Alert-Channel")
            return

        # Cooldown erst jetzt buchen (Channel steht) -> kein verbranntes Fenster.
        if not self.raid.should_alert(guild.id, now):
            return

        count = self.raid.join_count(guild.id)
        if gesperrt_bis:
            reaktion = (
                f"**Automatisch gesperrt bis <t:{int(gesperrt_bis)}:t>:** Einladungen und "
                "DMs sind pausiert, Verifizieren ebenfalls, die Discord-Verifizierungsstufe "
                "steht auf „Hoch“. Danach hebt Marshal die Sperre selbst auf. Früher "
                "aufheben: `/sicherheit entsperren`."
            )
        else:
            reaktion = "Automatische Sperre ist aus. Prüfe die neuen Mitglieder."
        embed = error_embed(
            title="⚠️ Raid-Verdacht",
            description=(
                f"**{count}** Joins in ~{int(window)}s "
                f"(Schwelle: {threshold}).\n{reaktion}"
            ),
        )
        embed.add_field(
            name="Beigetreten", value=self._joiner_text(guild.id, now - window * 3)[:1024],
            inline=False,
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

        embed = success_embed(
            title="✅ Verifizierung",
            description=(
                "Klicke auf **Verifizieren**, um Zugriff auf den Server zu "
                "erhalten."
            ),
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
        sperre_minuten="Automatische Sperre nach einem Spike (0 = nur Alarm, leer = unveraendert)",
    )
    @admin_only()
    async def setup_raid(
        self,
        interaction: discord.Interaction,
        schwelle: int,
        fenster_sekunden: int = 10,
        alert_channel: Optional[discord.TextChannel] = None,
        sperre_minuten: Optional[app_commands.Range[int, 0, 120]] = None,
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
        if sperre_minuten is not None:
            await GuildConfig.set(
                gid, "security.raid_lock_minutes", int(sperre_minuten), updated_by="command"
            )
        minuten = await GuildConfig.get(
            gid, "security.raid_lock_minutes", RAID_LOCK_MINUTES_DEFAULT
        )
        await interaction.followup.send(
            f"Raid-Schutz aktiv: {schwelle} Joins / {fenster_sekunden}s, "
            + (f"danach {minuten} min Sperre." if minuten else "nur Alarm."),
            ephemeral=True,
        )

    @sicherheit.command(
        name="entsperren",
        description="Laufende Raid-Sperre sofort aufheben",
    )
    @admin_only()
    async def unlock_raid(self, interaction: discord.Interaction) -> None:
        """Raid-Sperre manuell beenden."""
        await interaction.response.defer(ephemeral=True)
        if interaction.guild is None:
            await interaction.followup.send("Nur auf einem Server.", ephemeral=True)
            return
        aufgehoben = await self._sperre_aufheben(
            interaction.guild, f"Von {interaction.user.display_name} aufgehoben"
        )
        await interaction.followup.send(
            "Sperre aufgehoben." if aufgehoben else "Es läuft keine Sperre.", ephemeral=True
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
        embed = info_embed(title="Sicherheits-Status")
        embed.add_field(
            name="Verifizierung",
            value=("Aktiv — Rolle: " + (role.name if role else "?"))
            if verify_on else "Aus",
            inline=False,
        )
        minuten = await GuildConfig.get(
            gid, "security.raid_lock_minutes", RAID_LOCK_MINUTES_DEFAULT
        )
        bis = await GuildConfig.get(gid, "security.raid_lock_until")
        embed.add_field(
            name="Raid-Schutz",
            value=(
                f"Aktiv — {threshold} Joins / {window}s, "
                + (f"Sperre {minuten} min" if minuten else "nur Alarm")
            )
            if threshold and int(threshold) > 0 else "Aus",
            inline=False,
        )
        if sperre_aktiv(bis, time.time()):
            embed.add_field(
                name="Sperre läuft", value=f"bis <t:{int(float(bis))}:t>", inline=False
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
