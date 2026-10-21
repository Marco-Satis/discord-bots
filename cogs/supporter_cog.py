"""
Supporter-Cog: HQ-Supporter-Rolle nur fuer echte Booster.

Discord vergibt die Booster-Rolle automatisch an jeden Account, der boostet, auch an gekaufte
Boost-Accounts. Die HQ-Supporter-Rolle bekommt deshalb nur, wer boostet UND verifiziert ist
(Verifizierungs-Rolle aus `security.verification_role_id`). Endet der Boost oder fehlt die
Verifizierung, wird sie wieder entzogen. Von Hand vergeben haelt sie nur bei echten Boostern.

  - on_member_update        — Boost, Verifizierung oder Rolle aendert sich -> abgleichen
  - on_ready                — alle Mitglieder abgleichen (Ausfallzeit nachholen)
  - `/supporter rolle`      — (Admin) HQ-Supporter-Rolle festlegen, danach abgleichen
  - `/supporter abgleich`   — (Admin) sofort abgleichen

Config je Guild in `guild_config`: booster.supporter_role_id.
"""
from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from modules.guild_context import GuildConfig
from modules.platzhalter import render
from utils.logger import get_logger
from utils.permissions import admin_only

logger = get_logger("cogs.supporter")

_GRUND_DAZU = "HQ-Supporter: boostet und ist verifiziert"
_GRUND_WEG = "HQ-Supporter: kein Boost oder nicht verifiziert"


DANKE_STANDARD = "💎 Danke {user} für deinen Boost! Du bist jetzt HQ-Supporter."


async def _rolle(guild: discord.Guild, key: str) -> Optional[discord.Role]:
    """Rolle aus der Guild-Config aufloesen (oder None)."""
    role_id = await GuildConfig.get(guild.id, key)
    if not role_id:
        return None
    try:
        return guild.get_role(int(role_id))
    except (TypeError, ValueError):
        return None


def soll_supporter(member: discord.Member, verifiziert: discord.Role) -> bool:
    """Echter Booster = boostet gerade und hat die Verifizierungs-Rolle."""
    return member.premium_since is not None and verifiziert in member.roles


async def abgleichen(
    member: discord.Member, verifiziert: discord.Role, supporter: discord.Role
) -> Optional[str]:
    """Gibt oder nimmt die Supporter-Rolle. Rueckgabe 'dazu', 'weg' oder None."""
    soll = soll_supporter(member, verifiziert)
    hat = supporter in member.roles
    if soll == hat or member.bot:
        return None
    try:
        if soll:
            await member.add_roles(supporter, reason=_GRUND_DAZU)
            return "dazu"
        await member.remove_roles(supporter, reason=_GRUND_WEG)
        return "weg"
    except discord.HTTPException as e:
        logger.error(f"[{member.guild.id}] HQ-Supporter-Abgleich fehlgeschlagen fuer {member.id}: {e}")
        return None


class SupporterCog(commands.Cog):
    """Haelt die HQ-Supporter-Rolle mit echten Boostern synchron."""

    supporter_grp = app_commands.Group(
        name="supporter",
        description="HQ-Supporter-Rolle fuer echte Booster",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _rollen(
        self, guild: discord.Guild
    ) -> tuple[Optional[discord.Role], Optional[discord.Role]]:
        """Verifizierungs- und Supporter-Rolle der Guild."""
        return (
            await _rolle(guild, "security.verification_role_id"),
            await _rolle(guild, "booster.supporter_role_id"),
        )

    async def _alle_abgleichen(self, guild: discord.Guild) -> tuple[int, int]:
        """Gleicht alle Mitglieder ab. Rueckgabe (dazu, weg)."""
        verifiziert, supporter = await self._rollen(guild)
        if verifiziert is None or supporter is None:
            return 0, 0
        dazu = weg = 0
        for member in guild.members:
            ergebnis = await abgleichen(member, verifiziert, supporter)
            dazu += ergebnis == "dazu"
            weg += ergebnis == "weg"
        if dazu or weg:
            logger.info(f"[{guild.id}] HQ-Supporter abgeglichen: +{dazu} / -{weg}")
        return dazu, weg

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Nach (Re-)Connect einmal abgleichen, falls Boosts waehrend Ausfallzeit wechselten."""
        for guild in self.bot.guilds:
            try:
                await self._alle_abgleichen(guild)
            except Exception as e:
                logger.error(f"[{guild.id}] HQ-Supporter-Startabgleich fehlgeschlagen: {e}", exc_info=True)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        """Bei Boost-Start/-Ende, Verifizierung oder Supporter-Rollenwechsel abgleichen."""
        try:
            verifiziert, supporter = await self._rollen(after.guild)
            if verifiziert is None or supporter is None:
                return
            relevant = (
                before.premium_since != after.premium_since
                or (verifiziert in before.roles) != (verifiziert in after.roles)
                or (supporter in before.roles) != (supporter in after.roles)
            )
            if relevant:
                await abgleichen(after, verifiziert, supporter)
        except Exception as e:
            logger.error(f"[{after.guild.id}] HQ-Supporter on_member_update: {e}", exc_info=True)

    @commands.Cog.listener("on_member_update")
    async def boost_danke(self, before: discord.Member, after: discord.Member) -> None:
        """
        Danke-Nachricht beim ersten Boost (21.10.2026). Eingestellt im Dashboard
        (Seite HQ-Supporter): booster.danke_aktiv, booster.danke_kanal_id, booster.danke_text.
        Discord meldet weitere Boosts desselben Mitglieds nicht als Aenderung; gedankt
        wird also einmal je Boost-Beginn.
        """
        if before.premium_since is not None or after.premium_since is None or after.bot:
            return
        guild = after.guild
        try:
            if not await GuildConfig.get(guild.id, "booster.danke_aktiv", False):
                return
            kanal_id = await GuildConfig.get(guild.id, "booster.danke_kanal_id")
            kanal = guild.get_channel(int(kanal_id)) if kanal_id else None
            if kanal is None:
                logger.warning(f"[{guild.id}] Boost-Danke: kein gueltiger Kanal")
                return
            text = await GuildConfig.get(guild.id, "booster.danke_text", DANKE_STANDARD) or DANKE_STANDARD
            await kanal.send(
                render(str(text), member=after, guild=guild)[:2000],
                allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=[after]),
            )
            logger.info(f"[{guild.id}] Boost-Danke gesendet")
        except Exception as e:
            logger.error(f"[{guild.id}] Boost-Danke fehlgeschlagen: {e}", exc_info=True)

    @supporter_grp.command(name="rolle", description="HQ-Supporter-Rolle festlegen (Admin)")
    @app_commands.describe(rolle="Rolle, die echte Booster bekommen")
    @admin_only()
    async def rolle_cmd(self, interaction: discord.Interaction, rolle: discord.Role) -> None:
        """Supporter-Rolle in der Guild-Config speichern und sofort abgleichen."""
        if interaction.guild is None:
            await interaction.response.send_message("Nur auf einem Server nutzbar.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await GuildConfig.set(
            interaction.guild.id, "booster.supporter_role_id", str(rolle.id), updated_by="command"
        )
        dazu, weg = await self._alle_abgleichen(interaction.guild)
        await interaction.followup.send(
            f"HQ-Supporter-Rolle: {rolle.mention}. Abgleich: +{dazu} / -{weg}.",
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
        )

    @supporter_grp.command(name="abgleich", description="HQ-Supporter sofort abgleichen (Admin)")
    @admin_only()
    async def abgleich_cmd(self, interaction: discord.Interaction) -> None:
        """Alle Mitglieder jetzt abgleichen und das Ergebnis melden."""
        if interaction.guild is None:
            await interaction.response.send_message("Nur auf einem Server nutzbar.", ephemeral=True)
            return
        verifiziert, supporter = await self._rollen(interaction.guild)
        if verifiziert is None or supporter is None:
            await interaction.response.send_message(
                "Verifizierungs- oder Supporter-Rolle ist nicht konfiguriert.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        dazu, weg = await self._alle_abgleichen(interaction.guild)
        await interaction.followup.send(f"Abgleich: +{dazu} / -{weg}.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SupporterCog(bot))
