"""
Leveling Cog — XP- und Level-System für den Admin Bot

Features:
  - XP durch Nachrichten (mit Cooldown und Zufallsrange)
  - XP durch Voice-Aktivitaet (alle 5 Minuten geprueft)
  - Level-Up-Benachrichtigung mit Embed
  - Automatische Rollen-Belohnungen bei Level-Ups
  - Rank-Card mit Fortschrittsbalken
  - Leaderboard (Top 10)
  - Admin-Commands: XP setzen, Channel-Multiplikator, Rollen-Belohnung

Command-Struktur:
  /rank [user]                   — Rang-Karte anzeigen (Alle)
  /leaderboard                   — Top 10 anzeigen (Alle)
  /xp set <user> <amount>        — XP manuell setzen (Admin)
  /xp multiplier <channel> <factor> — Channel-Multiplikator (Admin)
  /xp reward <level> <rolle>     — Rollen-Belohnung setzen (Admin)
"""

from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from modules.leveling import LevelingManager
from utils.logger import get_logger
from utils.config import ADMIN_DATA_DIR
from utils.permissions import admin_only
from utils.formatting import progress_bar

logger = get_logger("cogs.leveling")


class LevelingCog(commands.Cog):
    """XP- und Level-System mit Nachrichten- und Voice-Tracking"""

    # Slash-Command-Gruppe für Admin-XP-Befehle
    xp_grp = app_commands.Group(
        name="xp",
        description="XP-System verwalten (Admin)",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.leveling = LevelingManager(
            data_file=ADMIN_DATA_DIR / "leveling.json",
            config_file=ADMIN_DATA_DIR / "leveling_config.json",
        )
        # Speichert User-IDs die gerade im Voice sind (für XP-Berechnung)
        # Format: {user_id: anzahl_5min_zyklen_im_voice}
        self._voice_tracking: dict[int, int] = {}

    async def cog_load(self) -> None:
        """DB-Daten laden und Voice-XP-Task starten."""
        # Leveling-Daten aus SQLite laden
        try:
            await self.leveling.load_from_db()
            logger.info("Leveling-Daten aus SQLite geladen")
        except Exception as e:
            logger.warning(f"SQLite-Load fehlgeschlagen: {e}")
        self.voice_xp_task.start()
        logger.info("Leveling-Cog geladen, Voice-XP-Task gestartet")

    async def cog_unload(self) -> None:
        """Voice-XP-Task stoppen wenn Cog entladen wird."""
        self.voice_xp_task.cancel()

    # ==================================================================
    # Event: Nachrichten-XP
    # ==================================================================

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """
        XP für Nachrichten vergeben.

        Ueberspringt:
        - Bot-Nachrichten
        - DMs (kein Guild)
        - Beachtet Cooldown (im LevelingManager)

        Bei Level-Up:
        - Embed im Channel senden
        - Rollen-Belohnung vergeben (falls konfiguriert)
        """
        # Bots und DMs ignorieren
        if message.author.bot:
            return
        if not message.guild:
            return

        channel_id = message.channel.id if message.channel else None
        xp_gained, leveled_up = self.leveling.add_message_xp(
            message.author.id, channel_id=channel_id
        )

        if not leveled_up:
            return

        # Level-Up Benachrichtigung
        user_data = self.leveling.get_user(message.author.id)
        new_level = user_data["level"]

        embed = discord.Embed(
            title="Level Up!",
            description=(
                f"{message.author.mention} hat **Level {new_level}** erreicht!"
            ),
            color=0xf1c40f,
        )

        # Fortschrittsbalken für nächstes Level
        xp_needed = self.leveling.xp_for_level(new_level)
        current_xp = user_data["xp"]
        # XP die bereits im aktuellen Level gesammelt wurden
        if new_level > 0:
            xp_prev_level = self.leveling.xp_for_level(new_level - 1)
            xp_in_level = current_xp - xp_prev_level
            xp_for_next = xp_needed - xp_prev_level
        else:
            xp_in_level = current_xp
            xp_for_next = xp_needed

        bar = progress_bar(xp_in_level, xp_for_next, length=10)
        embed.add_field(
            name="Naechstes Level",
            value=f"{bar} ({xp_in_level}/{xp_for_next} XP)",
            inline=False,
        )

        embed.set_thumbnail(url=message.author.display_avatar.url)

        try:
            await message.channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.warning(f"Level-Up Nachricht konnte nicht gesendet werden: {e}")

        # Rollen-Belohnung vergeben
        await self._apply_role_reward(message.author, new_level, message.guild)

    # ==================================================================
    # Background-Task: Voice-XP
    # ==================================================================

    @tasks.loop(minutes=5)
    async def voice_xp_task(self) -> None:
        """
        Alle 5 Minuten Voice-Channels prüfen und XP vergeben.

        Bedingungen für XP:
        - User darf nicht gemutet sein (self_mute oder mute)
        - Mindestens 2 User im Channel (kein Solo-Farming)
        - Nicht im AFK-Channel
        """
        for guild in self.bot.guilds:
            afk_channel = guild.afk_channel

            for vc in guild.voice_channels:
                # AFK-Channel überspringen
                if afk_channel and vc.id == afk_channel.id:
                    continue

                # Nur echte Teilnehmer zaehlen (nicht gemutet, nicht Bot)
                active_members = [
                    m for m in vc.members
                    if not m.bot
                    and not m.voice.self_mute
                    and not m.voice.mute
                ]

                # Mindestens 2 User im Channel
                if len(active_members) < 2:
                    continue

                for member in active_members:
                    xp_gained, leveled_up = self.leveling.add_voice_xp(
                        member.id, minutes=5
                    )

                    if leveled_up:
                        await self._send_voice_level_up(member, guild)

    @voice_xp_task.before_loop
    async def before_voice_xp(self) -> None:
        """Warten bis Bot bereit ist."""
        await self.bot.wait_until_ready()

    async def _send_voice_level_up(
        self,
        member: discord.Member,
        guild: discord.Guild,
    ) -> None:
        """
        Level-Up Nachricht für Voice-XP senden.

        Versucht den System-Channel des Guilds zu verwenden.
        Falls nicht vorhanden, wird die Nachricht übersprungen.
        """
        user_data = self.leveling.get_user(member.id)
        new_level = user_data["level"]

        embed = discord.Embed(
            title="Level Up!",
            description=(
                f"{member.mention} hat durch Voice-Aktivitaet "
                f"**Level {new_level}** erreicht!"
            ),
            color=0xf1c40f,
        )
        embed.set_thumbnail(url=member.display_avatar.url)

        # System-Channel oder ersten beschreibbaren Text-Channel finden
        channel = guild.system_channel
        if not channel:
            for tc in guild.text_channels:
                perms = tc.permissions_for(guild.me)
                if perms.send_messages and perms.embed_links:
                    channel = tc
                    break

        if channel:
            try:
                await channel.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException) as e:
                logger.warning(
                    f"Voice Level-Up Nachricht fehlgeschlagen: {e}"
                )

        # Rollen-Belohnung vergeben
        await self._apply_role_reward(member, new_level, guild)

    # ==================================================================
    # Hilfsmethoden
    # ==================================================================

    async def _apply_role_reward(
        self,
        member: discord.Member,
        level: int,
        guild: discord.Guild,
    ) -> None:
        """
        Rollen-Belohnung für ein Level vergeben (falls konfiguriert).

        Prueft auch alle vorherigen Level, damit nachtraeglich
        konfigurierte Belohnungen vergeben werden.

        Args:
            member: Discord-Member
            level: Das erreichte Level
            guild: Discord-Guild
        """
        # Alle Level bis zum aktuellen prüfen
        for lvl in range(1, level + 1):
            role_id = self.leveling.get_role_reward(lvl)
            if role_id is None:
                continue

            role = guild.get_role(role_id)
            if not role:
                logger.warning(
                    f"Belohnungs-Rolle {role_id} für Level {lvl} nicht gefunden"
                )
                continue

            if role in member.roles:
                continue  # Hat die Rolle bereits

            try:
                await member.add_roles(role, reason=f"Leveling: Level {lvl} erreicht")
                logger.info(
                    f"Rolle '{role.name}' an {member.display_name} vergeben "
                    f"(Level {lvl})"
                )
            except (discord.Forbidden, discord.HTTPException) as e:
                logger.warning(
                    f"Rolle '{role.name}' konnte nicht vergeben werden: {e}"
                )

    def _build_rank_embed(
        self,
        member: discord.Member,
        user_data: dict,
        rank: int,
    ) -> discord.Embed:
        """
        Rang-Embed für einen User erstellen.

        Args:
            member: Discord-Member
            user_data: Dict mit xp, level, total_messages, voice_minutes
            rank: Position im Leaderboard (1-basiert)

        Returns:
            Discord-Embed mit Rang-Informationen
        """
        level = user_data.get("level", 0)
        xp = user_data.get("xp", 0)
        total_messages = user_data.get("total_messages", 0)
        voice_minutes = user_data.get("voice_minutes", 0)

        # XP-Fortschritt im aktuellen Level berechnen
        xp_needed = self.leveling.xp_for_level(level)
        if level > 0:
            xp_prev = self.leveling.xp_for_level(level - 1)
            xp_in_level = xp - xp_prev
            xp_for_next = xp_needed - xp_prev
        else:
            xp_in_level = xp
            xp_for_next = xp_needed

        bar = progress_bar(xp_in_level, xp_for_next, length=12)

        embed = discord.Embed(
            title=f"Rang — {member.display_name}",
            color=member.color if member.color != discord.Color.default() else 0x5865F2,
        )

        embed.set_thumbnail(url=member.display_avatar.url)

        embed.add_field(name="Rang", value=f"#{rank}", inline=True)
        embed.add_field(name="Level", value=str(level), inline=True)
        embed.add_field(name="XP", value=f"{xp:,}", inline=True)

        embed.add_field(
            name="Fortschritt",
            value=f"{bar}\n{xp_in_level:,} / {xp_for_next:,} XP",
            inline=False,
        )

        embed.add_field(
            name="Nachrichten",
            value=f"{total_messages:,}",
            inline=True,
        )

        # Voice-Minuten in lesbares Format umwandeln
        if voice_minutes >= 60:
            voice_display = f"{voice_minutes // 60}h {voice_minutes % 60}m"
        else:
            voice_display = f"{voice_minutes}m"
        embed.add_field(name="Voice-Zeit", value=voice_display, inline=True)

        return embed

    # ==================================================================
    # /rank [user]
    # ==================================================================

    @app_commands.command(
        name="rank",
        description="Zeigt deinen Rang oder den eines anderen Users an",
    )
    @app_commands.describe(user="User dessen Rang angezeigt werden soll (leer = eigener)")
    async def rank_command(
        self,
        interaction: discord.Interaction,
        user: Optional[discord.Member] = None,
    ) -> None:
        """Rang-Karte mit Level, XP, Fortschrittsbalken und Position anzeigen."""
        await interaction.response.defer()

        target = user or interaction.user
        # Sicherstellen dass wir ein Member-Objekt haben
        if isinstance(target, discord.User) and interaction.guild:
            target = interaction.guild.get_member(target.id) or target

        user_data = self.leveling.get_user(target.id)
        rank = self.leveling.get_rank(target.id)

        embed = self._build_rank_embed(target, user_data, rank)
        await interaction.followup.send(embed=embed)

    # ==================================================================
    # /leaderboard
    # ==================================================================

    @app_commands.command(
        name="leaderboard",
        description="Zeigt die Top 10 im XP-Ranking an",
    )
    async def leaderboard_command(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Top 10 Leaderboard als Embed anzeigen."""
        await interaction.response.defer()

        top = self.leveling.get_leaderboard(limit=10)

        if not top:
            await interaction.followup.send(
                "Noch keine Leveling-Daten vorhanden.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="Leaderboard — Top 10",
            color=0xf1c40f,
        )

        description_lines: list[str] = []
        for i, entry in enumerate(top, start=1):
            user_id = entry["user_id"]
            level = entry["level"]
            xp = entry["xp"]

            # Medaillen für Top 3
            if i == 1:
                medal = "\U0001f947"
            elif i == 2:
                medal = "\U0001f948"
            elif i == 3:
                medal = "\U0001f949"
            else:
                medal = f"**{i}.**"

            # User-Mention oder Fallback
            member = None
            if interaction.guild:
                member = interaction.guild.get_member(user_id)

            if member:
                name = member.display_name
            else:
                name = f"User {user_id}"

            description_lines.append(
                f"{medal} **{name}** — Level {level} ({xp:,} XP)"
            )

        embed.description = "\n".join(description_lines)

        if interaction.guild:
            embed.set_footer(
                text=f"{interaction.guild.name} — Leveling",
                icon_url=interaction.guild.icon.url if interaction.guild.icon else None,
            )

        await interaction.followup.send(embed=embed)

    # ==================================================================
    # /xp set <user> <amount>
    # ==================================================================

    @xp_grp.command(
        name="set",
        description="XP eines Users manuell setzen",
    )
    @app_commands.describe(
        user="Der User dessen XP gesetzt werden sollen",
        amount="Neue XP-Anzahl",
    )
    @admin_only()
    async def xp_set(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        amount: int,
    ) -> None:
        """XP eines Users direkt auf einen bestimmten Wert setzen."""
        await interaction.response.defer(ephemeral=True)

        if amount < 0:
            await interaction.followup.send(
                "XP muessen >= 0 sein.", ephemeral=True
            )
            return

        self.leveling.set_xp(user.id, amount)
        user_data = self.leveling.get_user(user.id)

        await interaction.followup.send(
            f"XP für **{user.display_name}** auf **{amount:,}** gesetzt "
            f"(Level {user_data['level']}).",
            ephemeral=True,
        )

        logger.info(
            f"XP manuell gesetzt: {user.display_name} -> {amount} "
            f"von {interaction.user.display_name}"
        )

    # ==================================================================
    # /xp multiplier <channel> <factor>
    # ==================================================================

    @xp_grp.command(
        name="multiplier",
        description="XP-Multiplikator für einen Channel setzen",
    )
    @app_commands.describe(
        channel="Der Channel für den der Multiplikator gilt",
        factor="Multiplikator (z.B. 1.5 = 50% mehr XP, 0.5 = halbe XP, 1.0 = Standard)",
    )
    @admin_only()
    async def xp_multiplier(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        factor: float,
    ) -> None:
        """XP-Multiplikator für einen bestimmten Channel setzen."""
        await interaction.response.defer(ephemeral=True)

        if factor < 0:
            await interaction.followup.send(
                "Multiplikator muss >= 0 sein.", ephemeral=True
            )
            return

        if factor > 10.0:
            await interaction.followup.send(
                "Multiplikator darf maximal 10.0 sein.", ephemeral=True
            )
            return

        self.leveling.set_channel_multiplier(channel.id, factor)

        if factor == 1.0:
            await interaction.followup.send(
                f"Multiplikator für {channel.mention} zurückgesetzt (Standard: 1.0x).",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"Multiplikator für {channel.mention} auf **{factor}x** gesetzt.",
                ephemeral=True,
            )

    # ==================================================================
    # /xp reward <level> <rolle>
    # ==================================================================

    @xp_grp.command(
        name="reward",
        description="Rollen-Belohnung für ein Level setzen",
    )
    @app_commands.describe(
        level="Das Level bei dem die Rolle vergeben wird",
        rolle="Die Rolle die bei diesem Level vergeben wird",
    )
    @admin_only()
    async def xp_reward(
        self,
        interaction: discord.Interaction,
        level: int,
        rolle: discord.Role,
    ) -> None:
        """Automatische Rollen-Belohnung für ein bestimmtes Level konfigurieren."""
        await interaction.response.defer(ephemeral=True)

        if level < 1:
            await interaction.followup.send(
                "Level muss mindestens 1 sein.", ephemeral=True
            )
            return

        if level > 200:
            await interaction.followup.send(
                "Level darf maximal 200 sein.", ephemeral=True
            )
            return

        self.leveling.set_role_reward(level, rolle.id)

        await interaction.followup.send(
            f"Rollen-Belohnung gesetzt: Bei **Level {level}** wird die Rolle "
            f"**{rolle.name}** vergeben.",
            ephemeral=True,
        )

    # ==================================================================
    # Fehlerbehandlung
    # ==================================================================

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Zentrale Fehlerbehandlung für alle Commands in dieser Cog."""
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
    """Cog zum Bot hinzufuegen."""
    await bot.add_cog(LevelingCog(bot))
