"""
Counting-Cog: gemeinsames Hochzaehlen in einem Kanal (Ersatz fuer den Counting-Bot, 20.10.).

Regeln:
  - Jede Nachricht, die nur aus einer ganzen Zahl besteht, zaehlt. Anderer Text wird ignoriert.
  - Die Zahl muss genau die naechste sein, und niemand zaehlt zweimal hintereinander.
  - Fehler setzt auf 0 zurueck; der Rekord bleibt. Richtige Zahl = ✅, jede volle 100 = 🎉.
  - Wird die zuletzt gezaehlte Nachricht geloescht, nennt der Bot die aktuelle Zahl erneut.

  - `/counting kanal <kanal>` — (Admin) Zaehl-Kanal festlegen, Stand auf 0
  - `/counting stand`         — aktueller Stand und Rekord

Config/Stand je Guild in `guild_config` (Prefix `counting.`):
  channel_id, current, last_user_id, last_message_id, record.
"""
from __future__ import annotations

import asyncio
import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from modules.guild_context import GuildConfig
from utils.logger import get_logger
from utils.permissions import admin_only

logger = get_logger("cogs.counting")

_ZAHL = re.compile(r"^\d{1,9}$")
_OHNE_PING = discord.AllowedMentions.none()


def bewerte(text: str, aktuell: int, letzter: Optional[int], autor: int) -> tuple[str, Optional[int]]:
    """Reine Regel: ('ignorieren'|'richtig'|'falsche_zahl'|'doppelt', Zahl)."""
    text = text.strip()
    if not _ZAHL.match(text):
        return "ignorieren", None
    zahl = int(text)
    if letzter is not None and letzter == autor:
        return "doppelt", zahl
    if zahl != aktuell + 1:
        return "falsche_zahl", zahl
    return "richtig", zahl


class CountingCog(commands.Cog):
    """Haelt den Zaehlstand je Guild und prueft jede Nachricht im Zaehl-Kanal."""

    counting_grp = app_commands.Group(name="counting", description="Gemeinsam hochzaehlen")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._locks: dict[int, asyncio.Lock] = {}

    def _lock(self, guild_id: int) -> asyncio.Lock:
        return self._locks.setdefault(guild_id, asyncio.Lock())

    async def _kanal_id(self, guild_id: int) -> Optional[int]:
        wert = await GuildConfig.get(guild_id, "counting.channel_id")
        try:
            return int(wert) if wert else None
        except (TypeError, ValueError):
            return None

    async def _stand(self, gid: int) -> tuple[int, Optional[int], int]:
        """(aktuell, letzter Zaehler, Rekord)."""
        aktuell = int(await GuildConfig.get(gid, "counting.current", 0) or 0)
        letzter = await GuildConfig.get(gid, "counting.last_user_id")
        rekord = int(await GuildConfig.get(gid, "counting.record", 0) or 0)
        return aktuell, int(letzter) if letzter else None, rekord

    async def _setze(self, gid: int, aktuell: int, letzter: Optional[int], msg_id: Optional[int], rekord: int) -> None:
        await GuildConfig.set(gid, "counting.current", aktuell, updated_by="counting")
        await GuildConfig.set(gid, "counting.last_user_id", str(letzter) if letzter else None, updated_by="counting")
        await GuildConfig.set(gid, "counting.last_message_id", str(msg_id) if msg_id else None, updated_by="counting")
        await GuildConfig.set(gid, "counting.record", rekord, updated_by="counting")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Zahl im Zaehl-Kanal pruefen und Stand fortschreiben."""
        if message.author.bot or message.guild is None:
            return
        try:
            if message.channel.id != await self._kanal_id(message.guild.id):
                return
            gid = message.guild.id
            async with self._lock(gid):
                aktuell, letzter, rekord = await self._stand(gid)
                ergebnis, zahl = bewerte(message.content, aktuell, letzter, message.author.id)
                if ergebnis == "ignorieren":
                    return
                if ergebnis == "richtig":
                    rekord = max(rekord, zahl)
                    await self._setze(gid, zahl, message.author.id, message.id, rekord)
                    await message.add_reaction("🎉" if zahl % 100 == 0 else "✅")
                    return
                await self._setze(gid, 0, None, None, rekord)
            grund = ("zweimal hintereinander gezählt" if ergebnis == "doppelt"
                     else f"{zahl} geschrieben, dran war {aktuell + 1}")
            await message.add_reaction("❌")
            await message.channel.send(
                f"{message.author.mention} hat {grund}. Die Serie endet bei **{aktuell}**. "
                f"Weiter geht's mit **1**, der Rekord steht bei **{rekord}**.",
                allowed_mentions=_OHNE_PING,
            )
        except discord.HTTPException as e:
            logger.error(f"[{message.guild.id}] Counting: Discord-Fehler: {e}")
        except Exception as e:
            logger.error(f"[{message.guild.id}] Counting on_message: {e}", exc_info=True)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        """Geloeschte letzte Zahl: aktuellen Stand nennen, damit niemand aus dem Tritt kommt."""
        if message.guild is None:
            return
        try:
            if message.channel.id != await self._kanal_id(message.guild.id):
                return
            letzte = await GuildConfig.get(message.guild.id, "counting.last_message_id")
            if not letzte or str(message.id) != str(letzte):
                return
            aktuell, _, _ = await self._stand(message.guild.id)
            await message.channel.send(
                f"Die letzte Zahl (**{aktuell}**) wurde gelöscht. Weiter geht's mit **{aktuell + 1}**.",
                allowed_mentions=_OHNE_PING,
            )
        except Exception as e:
            logger.error(f"[{message.guild.id}] Counting on_message_delete: {e}", exc_info=True)

    @counting_grp.command(name="kanal", description="Zaehl-Kanal festlegen, Stand auf 0 (Admin)")
    @app_commands.describe(kanal="Textkanal fuer das Zaehlspiel")
    @admin_only()
    async def kanal_cmd(self, interaction: discord.Interaction, kanal: discord.TextChannel) -> None:
        """Kanal speichern und den Stand zuruecksetzen (Rekord bleibt)."""
        if interaction.guild is None:
            await interaction.response.send_message("Nur auf einem Server nutzbar.", ephemeral=True)
            return
        gid = interaction.guild.id
        await GuildConfig.set(gid, "counting.channel_id", str(kanal.id), updated_by="command")
        _, _, rekord = await self._stand(gid)
        await self._setze(gid, 0, None, None, rekord)
        await interaction.response.send_message(
            f"Zähl-Kanal: {kanal.mention}. Los geht's mit **1**.", ephemeral=True,
            allowed_mentions=_OHNE_PING,
        )

    @counting_grp.command(name="stand", description="Aktueller Zaehlstand und Rekord")
    async def stand_cmd(self, interaction: discord.Interaction) -> None:
        """Stand und Rekord anzeigen."""
        if interaction.guild is None:
            await interaction.response.send_message("Nur auf einem Server nutzbar.", ephemeral=True)
            return
        aktuell, _, rekord = await self._stand(interaction.guild.id)
        await interaction.response.send_message(
            f"Stand: **{aktuell}** · nächste Zahl: **{aktuell + 1}** · Rekord: **{rekord}**", ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CountingCog(bot))
