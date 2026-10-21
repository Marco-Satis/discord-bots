"""
Sticky-Rollen (Maki-Vorschlag 5, 21.10.2026).

Wer den Server verlaesst und wiederkommt, bekommt seine Rollen zurueck.

Ausgenommen (nie gespeichert, nie zurueckgegeben):
  - @everyone und Bot-/Integrationsrollen (managed, z. B. Booster-Rolle)
  - Team-Rollen: jede Rolle mit Moderations- oder Verwaltungsrechten
  - Rollen auf oder ueber Marshals hoechster Rolle (kann er nicht vergeben)

Waehrend einer Raid-Sperre gibt Marshal nichts zurueck: sonst kaeme ein
zurueckkehrendes Konto mit der Verifiziert-Rolle an der pausierten
Verifizierung vorbei.

Speicher: guild_config-Key `sticky_roles.gespeichert` = {user_id: {"rollen": [...], "zeit": unix}}.
Eintraege verfallen nach HALTBAR_TAGE. Schalter: Modul `sticky_roles` (guild_modules).
"""
from __future__ import annotations

import asyncio
import time
from typing import Iterable

import discord
from discord.ext import commands

from cogs.verification_cog import sperre_aktiv
from modules import notfall_meldung
from modules.guild_context import GuildConfig
from utils.logger import get_logger

logger = get_logger("cogs.sticky_roles")

SPEICHER_KEY = "sticky_roles.gespeichert"
HALTBAR_TAGE = 180

# Rechte, die eine Rolle zur Team-Rolle machen
TEAM_RECHTE = (
    "administrator", "manage_guild", "manage_roles", "manage_channels",
    "manage_messages", "kick_members", "ban_members", "moderate_members",
    "manage_webhooks", "manage_nicknames", "mute_members", "deafen_members",
    "move_members",
)


def ist_team_rolle(perms: discord.Permissions) -> bool:
    """Hat die Rolle irgendein Moderations- oder Verwaltungsrecht?"""
    return any(getattr(perms, name, False) for name in TEAM_RECHTE)


def merkbare_rollen(rollen: Iterable[discord.Role], bot_top_position: int) -> list[int]:
    """IDs der Rollen, die Marshal sich merken und spaeter zurueckgeben darf."""
    return [
        r.id
        for r in rollen
        if not r.is_default()
        and not r.managed
        and not ist_team_rolle(r.permissions)
        and r.position < bot_top_position
    ]


def aufraeumen(speicher: dict, jetzt: float) -> dict:
    """Abgelaufene Eintraege entfernen."""
    grenze = jetzt - HALTBAR_TAGE * 86400
    return {uid: e for uid, e in speicher.items() if float(e.get("zeit", 0)) >= grenze}


class StickyRolesCog(commands.Cog):
    """Gibt zurueckkehrenden Mitgliedern ihre Rollen wieder."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._lock = asyncio.Lock()

    async def _aktiv(self, guild_id: int) -> bool:
        return await GuildConfig.is_module_enabled(guild_id, "sticky_roles")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """Rollen beim Verlassen merken."""
        if member.bot:
            return
        guild = member.guild
        try:
            if not await self._aktiv(guild.id):
                return
            ids = merkbare_rollen(member.roles, guild.me.top_role.position)
            async with self._lock:
                speicher = aufraeumen(
                    await GuildConfig.get(guild.id, SPEICHER_KEY, {}) or {}, time.time()
                )
                if ids:
                    speicher[str(member.id)] = {"rollen": ids, "zeit": time.time()}
                else:
                    speicher.pop(str(member.id), None)
                await GuildConfig.set(guild.id, SPEICHER_KEY, speicher, updated_by="sticky_roles")
        except Exception as e:
            logger.error(f"Sticky-Rollen merken fehlgeschlagen: {e}")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Gemerkte Rollen zurueckgeben (ausser waehrend einer Raid-Sperre)."""
        if member.bot:
            return
        guild = member.guild
        try:
            if not await self._aktiv(guild.id):
                return
            if sperre_aktiv(await GuildConfig.get(guild.id, "security.raid_lock_until"), time.time()):
                logger.info("Sticky-Rollen: Raid-Sperre aktiv, keine Rueckgabe")
                return
            async with self._lock:
                speicher = await GuildConfig.get(guild.id, SPEICHER_KEY, {}) or {}
                eintrag = speicher.pop(str(member.id), None)
                if eintrag is None:
                    return
                await GuildConfig.set(guild.id, SPEICHER_KEY, speicher, updated_by="sticky_roles")

            top = guild.me.top_role.position
            rollen = [guild.get_role(int(rid)) for rid in eintrag.get("rollen", [])]
            # Erneut pruefen: Rollen koennen inzwischen geloescht, verschoben oder Team-Rollen sein
            erlaubt = {r.id for r in rollen if r is not None} & set(
                merkbare_rollen([r for r in rollen if r is not None], top)
            )
            geben = [r for r in rollen if r is not None and r.id in erlaubt]
            if not geben:
                return
            await member.add_roles(*geben, reason="Sticky-Rollen: Mitglied ist zurückgekehrt")
            logger.info(f"Sticky-Rollen: {len(geben)} Rolle(n) zurückgegeben")
        except discord.Forbidden:
            logger.warning("Sticky-Rollen: Marshal fehlt das Recht, Rollen zu vergeben")
            await notfall_meldung.melden(
                self.bot, "Marshal fehlt ein Recht",
                "Sticky-Rollen: Rollen konnten nicht zurückgegeben werden (Rollen verwalten fehlt "
                "oder Marshals Rolle liegt zu tief).",
                schluessel="forbidden:sticky_roles",
            )
        except Exception as e:
            logger.error(f"Sticky-Rollen zurückgeben fehlgeschlagen: {e}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StickyRolesCog(bot))
