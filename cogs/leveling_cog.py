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

import asyncio
import io
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from modules.leveling import LevelingManager
from modules.member_cache import upsert_member
from modules.voice_sessions import VoiceSessionTracker
from modules.guild_context import get_primary_guild_id, GuildConfig
from utils.async_tasks import schedule_from_sync
from utils.logger import get_logger
from utils.config import ADMIN_DATA_DIR
from utils.permissions import admin_only
from utils.formatting import progress_bar

logger = get_logger("cogs.leveling")

# Level-Up-Karte braucht Pillow — bei fehlender Lib auf Embed zurueckfallen,
# damit der Admin-Bot nicht crasht (Defense-in-Depth gegen fehlende Dependency).
try:
    from modules.levelup_card import render_levelup_card, render_leaderboard_card
    _CARD_AVAILABLE = True
except Exception as _card_import_err:  # noqa: BLE001
    render_levelup_card = None  # type: ignore[assignment]
    render_leaderboard_card = None  # type: ignore[assignment]
    _CARD_AVAILABLE = False
    logger.warning(
        f"Level-Up-Karte nicht verfuegbar (Pillow fehlt?): {_card_import_err}"
    )

# Ablage fuer hochgeladene Level-Up-Karten-Hintergruende
LEVELUP_BG_DIR = ADMIN_DATA_DIR / "levelup_backgrounds"

# Leaderboard-Pagination
LEADERBOARD_PER_PAGE = 10

# Zeilen in der Leaderboard-Bild-Karte (B3)
LEADERBOARD_CARD_ROWS = 10

# Fester Dateiname des Leaderboard-Hintergrunds (1-Admin-Template)
LEADERBOARD_BG_FILE = "leaderboard_bg.png"


def build_leaderboard_embed(
    entries: list[dict],
    guild: Optional[discord.Guild],
    page: int,
    per_page: int,
    own_rank: Optional[int],
    own_id: int,
) -> discord.Embed:
    """
    Eine Leaderboard-Seite als Embed rendern.

    Markiert den anfragenden User (◄ du) und zeigt im Footer Seite + eigene
    Position. Rang ist die absolute Position (seitenuebergreifend).
    """
    total = len(entries)
    max_page = max(0, (total - 1) // per_page) if total else 0
    page = max(0, min(page, max_page))
    start = page * per_page
    page_entries = entries[start:start + per_page]

    lines: list[str] = []
    for offset, entry in enumerate(page_entries):
        rank = start + offset + 1
        if rank == 1:
            medal = "\U0001f947"
        elif rank == 2:
            medal = "\U0001f948"
        elif rank == 3:
            medal = "\U0001f949"
        else:
            medal = f"**{rank}.**"

        member = guild.get_member(entry["user_id"]) if guild else None
        raw_name = member.display_name if member else f"User {entry['user_id']}"
        # Markdown-Injection-Schutz: Anzeigename im Embed-Description-Markdown escapen
        name = discord.utils.escape_markdown(raw_name)
        marker = " ◄ **du**" if entry["user_id"] == own_id else ""
        lines.append(
            f"{medal} {name} — Level {entry['level']} ({entry['xp']:,} XP){marker}"
        )

    embed = discord.Embed(
        title="Leaderboard",
        color=0xF1C40F,
        description="\n".join(lines) if lines else "Noch keine Daten.",
    )
    footer = f"Seite {page + 1}/{max_page + 1} · {total} Spieler"
    if own_rank:
        footer += f" · Dein Rang: #{own_rank}"
    embed.set_footer(text=footer)
    return embed


class LeaderboardView(discord.ui.View):
    """Blaetterbare Leaderboard-Ansicht (nur der Aufrufer darf blaettern)."""

    def __init__(
        self,
        entries: list[dict],
        guild: Optional[discord.Guild],
        requester_id: int,
        own_rank: Optional[int],
        per_page: int = LEADERBOARD_PER_PAGE,
        timeout: float = 120.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self.entries = entries
        self.guild = guild
        self.requester_id = requester_id
        self.own_rank = own_rank
        self.per_page = per_page
        self.page = 0
        self.max_page = max(0, (len(entries) - 1) // per_page) if entries else 0
        self.message: Optional[discord.Message] = None
        self._sync_buttons()

    def render(self) -> discord.Embed:
        return build_leaderboard_embed(
            self.entries, self.guild, self.page, self.per_page,
            self.own_rank, self.requester_id,
        )

    def _sync_buttons(self) -> None:
        self.prev_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= self.max_page

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "Das ist nicht deine Ansicht — ruf `/leaderboard` selbst auf.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.secondary)
    async def prev_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.page = max(0, self.page - 1)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.render(), view=self)

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.secondary)
    async def next_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.page = min(self.max_page, self.page + 1)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.render(), view=self)

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


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
            config_file=ADMIN_DATA_DIR / "leveling_config.json",
        )
        # Echte Voice-Sessions (Anti-Cheat) — ersetzt das 5-Min-Tick-Modell.
        self.voice = VoiceSessionTracker()

    async def cog_load(self) -> None:
        """DB-Daten laden, verwaiste Voice-Sessions schliessen, Sampler starten."""
        # Leveling-Daten aus SQLite laden
        try:
            await self.leveling.load_from_db()
            logger.info("Leveling-Daten aus SQLite geladen")
        except Exception as e:
            logger.warning(f"SQLite-Load fehlgeschlagen: {e}")
        # Per-Guild-Config der Haupt-Guild vorladen (Hot-Path hat sie dann sync;
        # weitere Guilds werden bei Bedarf lazy nachgeladen, siehe _gcfg).
        primary = get_primary_guild_id()
        if primary is not None:
            try:
                await self.leveling.load_guild_config(primary)
            except Exception as e:
                logger.warning(f"Guild-Config-Vorladen fehlgeschlagen: {e}")
        # Sessions die einen Neustart "ueberlebt" haben (leave_time NULL) schliessen.
        try:
            await self.voice.close_orphans()
        except Exception as e:
            logger.warning(f"close_orphans fehlgeschlagen: {e}")
        self.voice_sample_task.start()
        logger.info("Leveling-Cog geladen, Voice-Session-Sampler gestartet")

    async def cog_unload(self) -> None:
        """Sampler stoppen wenn Cog entladen wird."""
        self.voice_sample_task.cancel()

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
        # Multi-Tenant: Modul kann pro Guild deaktiviert sein (15s-TTL-Cache).
        if not await self._leveling_enabled(message.guild.id):
            return

        channel_id = message.channel.id if message.channel else None
        xp_gained, leveled_up = self.leveling.add_message_xp(
            message.guild.id, message.author.id, channel_id=channel_id
        )

        # E3: Member-Cache bei echter XP-Vergabe aktualisieren (60s-throttled
        # durch den XP-Cooldown) -> Web-Leaderboard zeigt Name+Avatar statt ID.
        if xp_gained > 0 and isinstance(message.author, discord.Member):
            self._cache_member(message.author)

        if not leveled_up:
            return

        # Level-Up Benachrichtigung (Bild-Karte falls aktiv, sonst Embed)
        user_data = self.leveling.get_user(message.guild.id, message.author.id)
        new_level = user_data["level"]

        file, embed = await self._build_levelup_message(
            message.guild.id, message.author, new_level
        )

        # Announce-Channel (Dashboard) bevorzugen, sonst Ausloese-Kanal.
        target = self._resolve_levelup_channel(message.guild, message.channel)
        if target is not None and isinstance(message.author, discord.Member):
            await self._send_levelup(
                target, message.author, file, embed, message.guild.id
            )

        # Rollen-Belohnung vergeben
        await self._apply_role_reward(message.author, new_level, message.guild)

    # ==================================================================
    # Event + Task: echte Voice-Sessions (Anti-Cheat)
    # ==================================================================

    def _voice_valid(
        self,
        member: Optional[discord.Member],
        channel: Optional[discord.VoiceChannel | discord.StageChannel],
        guild: discord.Guild,
    ) -> bool:
        """
        Ob der aktuelle Voice-Zustand XP-wuerdig ist:
        - Member ist im genannten Channel
        - Channel ist nicht der AFK-Channel
        - Member ist nicht self-deafened / server-deafened
        - mindestens 2 menschliche Teilnehmer (kein Solo-Farming)
        """
        if member is None or channel is None:
            return False
        if guild.afk_channel and channel.id == guild.afk_channel.id:
            return False
        vs = member.voice
        if vs is None or vs.channel is None or vs.channel.id != channel.id:
            return False
        if vs.self_deaf or vs.deaf:
            return False
        humans = [m for m in channel.members if not m.bot]
        return len(humans) >= 2

    async def _leveling_enabled(self, guild_id: int) -> bool:
        """Ob das Leveling-Modul fuer die Guild aktiv ist (15s-TTL-Cache)."""
        return await GuildConfig.is_module_enabled(guild_id, "leveling")

    def _cache_member(self, member: discord.Member) -> None:
        """
        E3: Anzeigename + Avatar-URL fire-and-forget in den member_cache schreiben.

        Damit zeigt das Web-Leaderboard Namen + Avatare statt roher User-IDs.
        Best-Effort (DB-Fehler im Cache-Modul geschluckt), blockiert den Hot-Path
        nicht (getrackter Background-Task gegen GC-Verlust).
        """
        try:
            schedule_from_sync(
                upsert_member(
                    member.guild.id,
                    member.id,
                    member.display_name,
                    str(member.display_avatar.url),
                ),
                name="member_cache.upsert",
            )
        except Exception as e:  # noqa: BLE001 — Cache nie fatal
            logger.debug(f"member_cache schedule fehlgeschlagen: {e}")

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """
        Voice-Session-Lifecycle: oeffnet/schliesst Sessions bei Channel-Wechsel.

        Reine mute/deaf-Aenderungen (gleicher Channel) ignoriert der Handler —
        die werden vom Sampler (`voice_sample_task`) erfasst.
        """
        if member.bot:
            return

        guild = member.guild
        before_ch = before.channel
        after_ch = after.channel
        if before_ch == after_ch:
            return  # nur State-Aenderung (mute/deaf/stream) — Sampler regelt das

        # Vorherigen Channel verlassen -> Session schliessen + XP vergeben
        if before_ch is not None and self.voice.is_open(guild.id, member.id):
            await self._end_voice_session(member, guild)

        # Neuen (nicht-AFK) Channel betreten -> Session oeffnen
        afk = guild.afk_channel
        if after_ch is not None and (afk is None or after_ch.id != afk.id):
            await self.voice.start(guild.id, member.id, after_ch.id)

    async def _end_voice_session(
        self, member: discord.Member, guild: discord.Guild
    ) -> None:
        """Session schliessen, XP aus validen Minuten vergeben, ggf. Level-Up."""
        result = await self.voice.end(guild.id, member.id, min_seconds=60)
        if not result:
            return
        minutes = result["valid_minutes"]
        if minutes <= 0:
            return  # alleine / deaf / zu kurz -> 0 valide Minuten -> kein XP
        # Modul deaktiviert: Session ist geschlossen (kein Waisen-Row), aber kein XP.
        if not await self._leveling_enabled(guild.id):
            return

        xp_gained, leveled_up = self.leveling.add_voice_xp(
            guild.id, member.id, minutes
        )
        await self.voice.set_xp(result["db_id"], xp_gained)
        # E3: Voice-aktive Member ebenfalls cachen (Web-Leaderboard Name+Avatar)
        self._cache_member(member)

        if leveled_up:
            await self._send_voice_level_up(member, guild)

    @tasks.loop(minutes=1)
    async def voice_sample_task(self) -> None:
        """
        Jede Minute jede offene Session bewerten: valide Sekunden akkumulieren
        (nur wenn Bedingungen erfuellt). XP wird erst bei Session-Ende vergeben.

        Robustheit: pro Session in try/except, damit ein Fehler nicht den
        ganzen @tasks.loop killt (discord.py startet ihn nicht automatisch neu).
        """
        for gid, uid in self.voice.open_keys():
            try:
                guild = self.bot.get_guild(int(gid))
                member = guild.get_member(int(uid)) if guild else None

                # Member nicht mehr im Voice/auffindbar (verlassen ohne Event,
                # nicht gecacht) -> Session schliessen statt ewig offen halten.
                if guild is None or member is None or member.voice is None:
                    if member is not None and guild is not None:
                        await self._end_voice_session(member, guild)
                    else:
                        await self._close_voice_session_silent(gid, uid)
                    continue

                valid = self._voice_valid(member, member.voice.channel, guild)
                self.voice.sample(gid, uid, valid)
            except Exception as e:  # noqa: BLE001 — eine Session darf den Loop nicht stoppen
                logger.warning(f"Voice-Sample {gid}/{uid} fehlgeschlagen: {e}")

    async def _close_voice_session_silent(
        self, guild_id: str, user_id: str
    ) -> None:
        """Session schliessen + XP vergeben ohne Level-Up-Nachricht (Member weg)."""
        result = await self.voice.end(guild_id, user_id, min_seconds=60)
        if (
            result
            and result["valid_minutes"] > 0
            and await self._leveling_enabled(int(guild_id))
        ):
            xp_gained, _ = self.leveling.add_voice_xp(
                guild_id, user_id, result["valid_minutes"]
            )
            await self.voice.set_xp(result["db_id"], xp_gained)

    @voice_sample_task.before_loop
    async def before_voice_sample(self) -> None:
        """Warten bis Bot bereit ist."""
        await self.bot.wait_until_ready()

    @voice_sample_task.error
    async def voice_sample_error(self, error: BaseException) -> None:
        """Loop-Fehler loggen + neu starten (sonst faellt Sampling dauerhaft aus)."""
        logger.error(f"voice_sample_task gestoppt: {error}", exc_info=error)
        if not self.voice_sample_task.is_running():
            self.voice_sample_task.restart()

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
        user_data = self.leveling.get_user(guild.id, member.id)
        new_level = user_data["level"]

        file, embed = await self._build_levelup_message(
            guild.id, member, new_level, via_voice=True
        )

        # Announce-Channel (Dashboard) bevorzugen, sonst System-Channel,
        # sonst ersten beschreibbaren Text-Channel.
        channel: Optional[discord.abc.Messageable] = self._resolve_levelup_channel(
            guild, guild.system_channel
        )
        if not channel:
            for tc in guild.text_channels:
                perms = tc.permissions_for(guild.me)
                if perms.send_messages and perms.embed_links:
                    channel = tc
                    break

        if channel:
            await self._send_levelup(channel, member, file, embed, guild.id)

        # Rollen-Belohnung vergeben
        await self._apply_role_reward(member, new_level, guild)

    # ==================================================================
    # Hilfsmethoden
    # ==================================================================

    def _xp_progress(self, user_data: dict, level: int) -> tuple[int, int]:
        """XP im aktuellen Level + XP-Spanne zum naechsten Level berechnen."""
        current_xp = user_data.get("xp", 0)
        xp_needed = self.leveling.xp_for_level(level)
        if level > 0:
            xp_prev = self.leveling.xp_for_level(level - 1)
            return current_xp - xp_prev, xp_needed - xp_prev
        return current_xp, xp_needed

    async def _build_levelup_message(
        self,
        guild_id: int,
        member: discord.Member | discord.User,
        new_level: int,
        *,
        via_voice: bool = False,
    ) -> tuple[Optional[discord.File], discord.Embed]:
        """
        Level-Up-Nachricht bauen.

        Wenn die Bild-Karte aktiv ist und ein Hintergrund gesetzt wurde:
        rendert eine PNG-Karte (Wallpaper + Avatar + Progress) und gibt
        (discord.File, Embed mit set_image) zurueck. Sonst (None, Standard-Embed).

        Args:
            guild_id: Discord-Guild-ID (fuer per-Guild-User-Daten + Karten-Config)
            member: Der Member der gelevelt hat
            new_level: Das erreichte Level
            via_voice: True wenn durch Voice-Aktivitaet ausgeloest

        Returns:
            (file_or_None, embed)
        """
        user_data = self.leveling.get_user(guild_id, member.id)
        xp_in_level, xp_for_next = self._xp_progress(user_data, new_level)

        # --- Bild-Karte versuchen ---
        if _CARD_AVAILABLE and self.leveling.is_card_enabled(guild_id):
            bg_name = self.leveling.get_card_bg(guild_id)
            if bg_name:
                bg_path = LEVELUP_BG_DIR / bg_name
                if bg_path.exists():
                    try:
                        avatar_bytes = await member.display_avatar.read()
                        accent = self.leveling.get_card_accent(guild_id)
                        png = await asyncio.to_thread(
                            render_levelup_card,
                            bg_path,
                            avatar_bytes,
                            member.display_name,
                            new_level,
                            xp_in_level,
                            xp_for_next,
                            accent,
                        )
                        file = discord.File(io.BytesIO(png), filename="levelup.png")
                        try:
                            color = int(accent.lstrip("#"), 16)
                        except ValueError:
                            color = 0xF1C40F
                        embed = discord.Embed(color=color)
                        embed.set_image(url="attachment://levelup.png")
                        return file, embed
                    except Exception as e:  # noqa: BLE001 — Fallback auf Embed
                        logger.warning(
                            f"Level-Up-Karte fehlgeschlagen, nutze Embed: {e}"
                        )

        # --- Fallback: klassisches Embed ---
        suffix = " durch Voice-Aktivitaet" if via_voice else ""
        embed = discord.Embed(
            title="Level Up!",
            description=(
                f"{member.mention} hat{suffix} **Level {new_level}** erreicht!"
            ),
            color=0xF1C40F,
        )
        bar = progress_bar(xp_in_level, xp_for_next, length=10)
        embed.add_field(
            name="Naechstes Level",
            value=f"{bar} ({xp_in_level}/{xp_for_next} XP)",
            inline=False,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        return None, embed

    def _resolve_levelup_channel(
        self,
        guild: discord.Guild,
        fallback: Optional[discord.abc.Messageable],
    ) -> Optional[discord.abc.Messageable]:
        """
        Ziel-Channel fuer Level-Up-Posts ermitteln (B2, Dashboard-konfigurierbar).

        Bevorzugt den im Dashboard gesetzten Announce-Channel (wenn vorhanden,
        existent und fuer den Bot beschreibbar). Sonst den uebergebenen Fallback:
        Ausloese-Kanal bei Nachrichten, System-Channel bei Voice.
        """
        cid = self.leveling.get_announce_channel(guild.id)
        if cid:
            ch = guild.get_channel(cid)
            if isinstance(ch, discord.TextChannel):
                perms = ch.permissions_for(guild.me)
                if perms.send_messages and perms.embed_links:
                    return ch
        return fallback

    async def _send_levelup(
        self,
        channel: discord.abc.Messageable,
        member: discord.Member,
        file: Optional[discord.File],
        embed: discord.Embed,
        guild_id: int,
    ) -> None:
        """
        Level-Up-Nachricht senden — mit optionalem Member-Ping (B1, Dashboard-Toggle).

        allowed_mentions erlaubt gezielt nur den einen User-Ping
        (kein @everyone/@here/Rollen-Ping — Defense-in-Depth).
        """
        ping = self.leveling.is_levelup_ping_enabled(guild_id)
        content = member.mention if ping else None
        mentions = discord.AllowedMentions(
            everyone=False, roles=False, users=[member] if ping else False
        )
        try:
            if file is not None:
                await channel.send(
                    content=content, embed=embed, file=file,
                    allowed_mentions=mentions,
                )
            else:
                await channel.send(
                    content=content, embed=embed, allowed_mentions=mentions,
                )
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.warning(f"Level-Up Nachricht konnte nicht gesendet werden: {e}")

    async def _apply_role_reward(
        self,
        member: discord.Member,
        level: int,
        guild: discord.Guild,
    ) -> None:
        """
        Rollen-Belohnungen anwenden (falls konfiguriert).

        Vergibt jede Belohnungs-Rolle deren Level erreicht ist (auch nachtraeglich
        konfigurierte). Ist `remove_lower_rewards` aktiv, bleibt nur die hoechste
        erreichte Belohnungs-Rolle — alle anderen Belohnungs-Rollen werden entfernt
        (Rang-Aufstieg statt Rollen-Sammlung).

        Args:
            member: Discord-Member
            level: Das erreichte Level
            guild: Discord-Guild
        """
        rewards = self.leveling.get_role_rewards(guild.id)  # {level: role_id}
        if not rewards:
            return

        earned_levels = sorted(lvl for lvl in rewards if lvl <= level)
        if not earned_levels:
            return

        remove_lower = self.leveling.is_remove_lower_enabled(guild.id)

        if remove_lower:
            keep_level = earned_levels[-1]
            add_levels = [keep_level]
            # alle ANDEREN konfigurierten Belohnungs-Rollen entfernen
            remove_role_ids = {
                rewards[lvl] for lvl in rewards if lvl != keep_level
            }
        else:
            add_levels = earned_levels
            remove_role_ids = set()

        # --- hinzufuegen ---
        for lvl in add_levels:
            role = guild.get_role(rewards[lvl])
            if role is None:
                logger.warning(
                    f"Belohnungs-Rolle {rewards[lvl]} für Level {lvl} nicht gefunden"
                )
                continue
            if role in member.roles:
                continue
            try:
                await member.add_roles(role, reason=f"Leveling: Level {lvl} erreicht")
                logger.info(
                    f"Rolle '{role.name}' an {member.display_name} vergeben (Level {lvl})"
                )
            except (discord.Forbidden, discord.HTTPException) as e:
                logger.warning(f"Rolle '{role.name}' nicht vergebbar: {e}")

        # --- entfernen (nur bei remove_lower) ---
        for role_id in remove_role_ids:
            role = guild.get_role(role_id)
            if role is None or role not in member.roles:
                continue
            try:
                await member.remove_roles(
                    role, reason="Leveling: niedrigere Belohnungs-Rolle ersetzt"
                )
                logger.info(
                    f"Rolle '{role.name}' von {member.display_name} entfernt (Aufstieg)"
                )
            except (discord.Forbidden, discord.HTTPException) as e:
                logger.warning(f"Rolle '{role.name}' nicht entfernbar: {e}")

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

    async def _try_render_card(
        self,
        guild_id: int,
        member: discord.Member | discord.User,
        level: int,
        xp_in_level: int,
        xp_for_next: int,
        header: str,
    ) -> Optional[discord.File]:
        """
        Karte rendern wenn die Bild-Karte der Guild aktiviert ist + ein
        Hintergrund existiert (1-Admin-Template). Sonst None (Embed-Fallback).
        """
        if not (_CARD_AVAILABLE and self.leveling.is_card_enabled(guild_id)):
            return None
        bg_name = self.leveling.get_card_bg(guild_id)
        if not bg_name:
            return None
        bg_path = LEVELUP_BG_DIR / bg_name
        if not bg_path.exists():
            return None
        try:
            avatar_bytes = await member.display_avatar.read()
            accent = self.leveling.get_card_accent(guild_id)
            png = await asyncio.to_thread(
                render_levelup_card,
                bg_path,
                avatar_bytes,
                member.display_name,
                level,
                xp_in_level,
                xp_for_next,
                accent,
                header,
            )
            return discord.File(io.BytesIO(png), filename="card.png")
        except Exception as e:  # noqa: BLE001 — Fallback auf Embed
            logger.warning(f"Karten-Render fehlgeschlagen: {e}")
            return None

    async def _build_leaderboard_image(
        self,
        guild: discord.Guild,
        entries: list[dict],
    ) -> Optional[discord.File]:
        """
        Leaderboard als Bild-Karte rendern (B3) — Top-N mit Avataren.

        Nur wenn die Leaderboard-Karte aktiviert ist + ein Hintergrund existiert,
        sonst None (Embed-Fallback). Holt die Avatare der Top-N parallel; ein
        fehlender Avatar fuehrt zu None statt Fehler.
        """
        if not (
            _CARD_AVAILABLE
            and self.leveling.is_leaderboard_card_enabled(guild.id)
        ):
            return None
        bg_name = self.leveling.get_leaderboard_bg(guild.id)
        if not bg_name:
            return None
        bg_path = LEVELUP_BG_DIR / bg_name
        if not bg_path.exists():
            return None

        top = entries[:LEADERBOARD_CARD_ROWS]

        async def _row(rank: int, entry: dict) -> dict:
            member = guild.get_member(entry["user_id"])
            name = member.display_name if member else f"User {entry['user_id']}"
            avatar: bytes | None = None
            if member is not None:
                try:
                    avatar = await member.display_avatar.read()
                except (discord.HTTPException, discord.NotFound) as e:
                    logger.debug(f"Leaderboard-Avatar-Read fehlgeschlagen: {e}")
            return {
                "rank": rank,
                "name": name,
                "level": entry.get("level", 0),
                "xp": entry.get("xp", 0),
                "avatar": avatar,
            }

        rows = await asyncio.gather(
            *[_row(i, e) for i, e in enumerate(top, start=1)]
        )

        accent = self.leveling.get_card_accent(guild.id)
        subtitle = f"{guild.name} · {len(entries)} Spieler"
        try:
            png = await asyncio.to_thread(
                render_leaderboard_card,
                list(rows),
                bg_path,
                accent,
                "LEADERBOARD",
                subtitle,
            )
        except Exception as e:  # noqa: BLE001 — Fallback auf Embed
            logger.warning(f"Leaderboard-Karte fehlgeschlagen: {e}")
            return None
        return discord.File(io.BytesIO(png), filename="leaderboard.png")

    # ==================================================================
    # /rank [user]
    # ==================================================================

    @app_commands.command(
        name="rank",
        description="Zeigt deinen Rang oder den eines anderen Users an",
    )
    @app_commands.describe(user="User dessen Rang angezeigt werden soll (leer = eigener)")
    @app_commands.checks.cooldown(1, 10.0)
    async def rank_command(
        self,
        interaction: discord.Interaction,
        user: Optional[discord.Member] = None,
    ) -> None:
        """Rang-Karte mit Level, XP, Fortschrittsbalken und Position anzeigen."""
        await interaction.response.defer()

        if interaction.guild is None:
            await interaction.followup.send(
                "Dieser Befehl funktioniert nur auf einem Server.", ephemeral=True
            )
            return

        target = user or interaction.user
        # Sicherstellen dass wir ein Member-Objekt haben
        if isinstance(target, discord.User):
            target = interaction.guild.get_member(target.id) or target

        user_data = self.leveling.get_user(interaction.guild.id, target.id)
        rank = self.leveling.get_rank(interaction.guild.id, target.id)

        # Bild-Karte (gleiches 1-Admin-Template wie Level-Up) wenn aktiviert,
        # sonst klassisches Embed.
        level = user_data.get("level", 0)
        xp_in_level, xp_for_next = self._xp_progress(user_data, level)
        card = await self._try_render_card(
            interaction.guild.id, target, level, xp_in_level, xp_for_next,
            header=f"RANG #{rank}",
        )
        if card is not None:
            await interaction.followup.send(file=card)
        else:
            embed = self._build_rank_embed(target, user_data, rank)
            await interaction.followup.send(embed=embed)

    # ==================================================================
    # /leaderboard
    # ==================================================================

    @app_commands.command(
        name="leaderboard",
        description="Zeigt das XP-Ranking (blaetterbar, mit deiner Position)",
    )
    @app_commands.checks.cooldown(1, 10.0)
    async def leaderboard_command(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Blaetterbares Leaderboard mit eigener Position anzeigen."""
        await interaction.response.defer()

        if interaction.guild is None:
            await interaction.followup.send(
                "Dieser Befehl funktioniert nur auf einem Server.", ephemeral=True
            )
            return

        entries = self.leveling.get_leaderboard(interaction.guild.id, limit=None)

        if not entries:
            await interaction.followup.send(
                "Noch keine Leveling-Daten vorhanden.",
                ephemeral=True,
            )
            return

        own_rank = self.leveling.get_rank(interaction.guild.id, interaction.user.id)

        # B3: Bild-Karte bevorzugen (wenn aktiviert + Hintergrund), sonst Embed.
        image = await self._build_leaderboard_image(interaction.guild, entries)
        if image is not None:
            await interaction.followup.send(file=image)
            return

        view = LeaderboardView(
            entries,
            interaction.guild,
            requester_id=interaction.user.id,
            own_rank=own_rank,
        )
        # Bei nur einer Seite keine Buttons noetig
        if view.max_page == 0:
            await interaction.followup.send(
                embed=view.render(),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        msg = await interaction.followup.send(
            embed=view.render(),
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        view.message = msg

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

        if interaction.guild is None:
            await interaction.followup.send(
                "Nur auf einem Server nutzbar.", ephemeral=True
            )
            return

        if amount < 0:
            await interaction.followup.send(
                "XP muessen >= 0 sein.", ephemeral=True
            )
            return

        self.leveling.set_xp(interaction.guild.id, user.id, amount)
        user_data = self.leveling.get_user(interaction.guild.id, user.id)

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

        if interaction.guild is None:
            await interaction.followup.send(
                "Nur auf einem Server nutzbar.", ephemeral=True
            )
            return

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

        await self.leveling.set_channel_multiplier(
            interaction.guild.id, channel.id, factor
        )

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
    # /xp noxp <channel> <aktiv>
    # ==================================================================

    @xp_grp.command(
        name="noxp",
        description="Channel zur No-XP-Liste hinzufügen/entfernen (keine XP dort)",
    )
    @app_commands.describe(
        channel="Der Channel der von XP ausgeschlossen werden soll",
        aktiv="True = kein XP in diesem Channel, False = wieder erlauben",
    )
    @admin_only()
    async def xp_noxp(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        aktiv: bool,
    ) -> None:
        """Channel von der XP-Vergabe ausschliessen (Spam-/Bot-/Command-Kanaele)."""
        await interaction.response.defer(ephemeral=True)

        if interaction.guild is None:
            await interaction.followup.send(
                "Nur auf einem Server nutzbar.", ephemeral=True
            )
            return

        await self.leveling.set_no_xp_channel(interaction.guild.id, channel.id, aktiv)

        if aktiv:
            await interaction.followup.send(
                f"In {channel.mention} wird ab jetzt **kein XP** mehr vergeben.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"{channel.mention} ist wieder XP-berechtigt.",
                ephemeral=True,
            )

    # ==================================================================
    # /xp announce <channel> [entfernen]
    # ==================================================================

    @xp_grp.command(
        name="announce",
        description="Festen Kanal fuer Level-Up-Nachrichten setzen (Dashboard-Fallback)",
    )
    @app_commands.describe(
        channel="Kanal in dem Level-Ups gepostet werden",
        entfernen="True = Kanal entfernen (wieder im Ausloese-Kanal posten)",
    )
    @admin_only()
    async def xp_announce(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
        entfernen: bool = False,
    ) -> None:
        """Announce-Channel fuer Level-Up-Posts setzen/entfernen (auch im Dashboard)."""
        await interaction.response.defer(ephemeral=True)

        if interaction.guild is None:
            await interaction.followup.send(
                "Nur auf einem Server nutzbar.", ephemeral=True
            )
            return

        if entfernen:
            await self.leveling.set_announce_channel(interaction.guild.id, None)
            await interaction.followup.send(
                "Announce-Kanal entfernt — Level-Ups werden wieder im "
                "Ausloese-Kanal gepostet.",
                ephemeral=True,
            )
            return

        if channel is None:
            await interaction.followup.send(
                "Bitte einen Kanal angeben (oder `entfernen:True`).", ephemeral=True
            )
            return

        await self.leveling.set_announce_channel(interaction.guild.id, channel.id)
        await interaction.followup.send(
            f"Level-Up-Nachrichten werden ab jetzt in {channel.mention} gepostet.",
            ephemeral=True,
        )

    # ==================================================================
    # /xp ping <aktiv>
    # ==================================================================

    @xp_grp.command(
        name="ping",
        description="Member bei Level-Up anpingen an-/ausschalten (Dashboard-Fallback)",
    )
    @app_commands.describe(aktiv="True = Member anpingen, False = nur Nachricht")
    @admin_only()
    async def xp_ping(
        self,
        interaction: discord.Interaction,
        aktiv: bool,
    ) -> None:
        """Member-Ping bei Level-Up umschalten (auch im Dashboard editierbar)."""
        await interaction.response.defer(ephemeral=True)

        if interaction.guild is None:
            await interaction.followup.send(
                "Nur auf einem Server nutzbar.", ephemeral=True
            )
            return

        await self.leveling.set_levelup_ping(interaction.guild.id, aktiv)
        if aktiv:
            await interaction.followup.send(
                "Member werden bei Level-Up jetzt **angepingt**.", ephemeral=True
            )
        else:
            await interaction.followup.send(
                "Level-Up-Nachrichten **ohne** Member-Ping.", ephemeral=True
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

        if interaction.guild is None:
            await interaction.followup.send(
                "Nur auf einem Server nutzbar.", ephemeral=True
            )
            return

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

        await self.leveling.set_role_reward(interaction.guild.id, level, rolle.id)

        await interaction.followup.send(
            f"Rollen-Belohnung gesetzt: Bei **Level {level}** wird die Rolle "
            f"**{rolle.name}** vergeben.",
            ephemeral=True,
        )

    # ==================================================================
    # /xp rewardremove <level>
    # ==================================================================

    @xp_grp.command(
        name="rewardremove",
        description="Rollen-Belohnung für ein Level entfernen",
    )
    @app_commands.describe(level="Das Level dessen Belohnung entfernt wird")
    @admin_only()
    async def xp_rewardremove(
        self,
        interaction: discord.Interaction,
        level: int,
    ) -> None:
        """Konfigurierte Rollen-Belohnung eines Levels loeschen."""
        await interaction.response.defer(ephemeral=True)

        if interaction.guild is None:
            await interaction.followup.send(
                "Nur auf einem Server nutzbar.", ephemeral=True
            )
            return

        removed = await self.leveling.remove_role_reward(interaction.guild.id, level)
        if removed:
            await interaction.followup.send(
                f"Rollen-Belohnung für **Level {level}** entfernt.", ephemeral=True
            )
        else:
            await interaction.followup.send(
                f"Für Level {level} war keine Belohnung konfiguriert.", ephemeral=True
            )

    # ==================================================================
    # /xp removelower <aktiv>
    # ==================================================================

    @xp_grp.command(
        name="removelower",
        description="Beim Aufstieg niedrigere Belohnungs-Rollen entfernen",
    )
    @app_commands.describe(
        aktiv="True = nur höchste Belohnungs-Rolle behalten, False = alle behalten",
    )
    @admin_only()
    async def xp_removelower(
        self,
        interaction: discord.Interaction,
        aktiv: bool,
    ) -> None:
        """'Niedrigere Belohnungs-Rolle bei Aufstieg entfernen' umschalten."""
        await interaction.response.defer(ephemeral=True)

        if interaction.guild is None:
            await interaction.followup.send(
                "Nur auf einem Server nutzbar.", ephemeral=True
            )
            return

        await self.leveling.set_remove_lower(interaction.guild.id, aktiv)
        if aktiv:
            await interaction.followup.send(
                "Beim Aufstieg wird ab jetzt nur die **höchste** erreichte "
                "Belohnungs-Rolle behalten.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "Belohnungs-Rollen werden **gesammelt** (niedrigere bleiben).",
                ephemeral=True,
            )

    # ==================================================================
    # /levelrewards  (öffentlich — Rang-Belohnungs-Leiter anzeigen)
    # ==================================================================

    @app_commands.command(
        name="levelrewards",
        description="Zeigt die konfigurierten Level-Rollen-Belohnungen",
    )
    async def levelrewards_command(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Konfigurierte Rollen-Belohnungen (Level -> Rolle) auflisten."""
        await interaction.response.defer()

        if interaction.guild is None:
            await interaction.followup.send(
                "Dieser Befehl funktioniert nur auf einem Server.", ephemeral=True
            )
            return

        rewards = self.leveling.get_role_rewards(interaction.guild.id)
        if not rewards:
            await interaction.followup.send(
                "Es sind noch keine Rollen-Belohnungen konfiguriert.",
                ephemeral=True,
            )
            return

        lines: list[str] = []
        for lvl, role_id in rewards.items():
            role = interaction.guild.get_role(role_id)
            role_text = role.mention if role else f"(gelöschte Rolle {role_id})"
            lines.append(f"**Level {lvl}** → {role_text}")

        embed = discord.Embed(
            title="Level-Belohnungen",
            description="\n".join(lines),
            color=0xF1C40F,
        )
        if self.leveling.is_remove_lower_enabled(interaction.guild.id):
            embed.set_footer(text="Nur die höchste erreichte Rolle wird behalten.")
        await interaction.followup.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # ==================================================================
    # /xp levelcard <aktiv> [hintergrund] [akzentfarbe]
    # ==================================================================

    @staticmethod
    def _save_bg(raw: bytes, dst) -> None:
        """Hochgeladenes Bild speichern, auf max 1200px Breite begrenzen (blocking)."""
        from PIL import Image

        im = Image.open(io.BytesIO(raw)).convert("RGB")
        if im.width > 1200:
            new_h = round(im.height * 1200 / im.width)
            im = im.resize((1200, new_h), Image.LANCZOS)
        dst.parent.mkdir(parents=True, exist_ok=True)
        im.save(dst, "PNG")

    @xp_grp.command(
        name="levelcard",
        description="Level-Up-Karte mit Bild-Hintergrund konfigurieren",
    )
    @app_commands.describe(
        aktiv="Bild-Karte an- oder ausschalten",
        hintergrund="Bild (PNG/JPG) als Hintergrund hochladen",
        akzentfarbe="Akzentfarbe als Hex, z.B. #ff2b6b",
    )
    @admin_only()
    async def xp_levelcard(
        self,
        interaction: discord.Interaction,
        aktiv: bool,
        hintergrund: Optional[discord.Attachment] = None,
        akzentfarbe: Optional[str] = None,
    ) -> None:
        """Level-Up-Karte (Bild-Hintergrund) ein-/ausschalten, Hintergrund + Akzent setzen."""
        await interaction.response.defer(ephemeral=True)

        if interaction.guild is None:
            await interaction.followup.send(
                "Nur auf einem Server nutzbar.", ephemeral=True
            )
            return
        guild_id = interaction.guild.id

        if not _CARD_AVAILABLE:
            await interaction.followup.send(
                "Bild-Karten nicht verfuegbar — Pillow ist auf dem Server nicht "
                "installiert. Installiere `pip install Pillow` und starte den Bot neu.",
                ephemeral=True,
            )
            return

        notes: list[str] = []

        # Akzentfarbe validieren + setzen
        if akzentfarbe:
            h = akzentfarbe.strip()
            if not h.startswith("#"):
                h = "#" + h
            hex_part = h.lstrip("#")
            if len(hex_part) == 6 and all(c in "0123456789abcdefABCDEF" for c in hex_part):
                await self.leveling.set_card_accent(guild_id, h)
                notes.append(f"Akzent {h}")
            else:
                await interaction.followup.send(
                    "Ungueltige Hex-Farbe. Format: #RRGGBB", ephemeral=True
                )
                return

        # Hintergrund-Upload verarbeiten
        if hintergrund is not None:
            content_type = hintergrund.content_type or ""
            if not content_type.startswith("image/"):
                await interaction.followup.send(
                    "Hintergrund muss ein Bild sein (PNG/JPG).", ephemeral=True
                )
                return
            if hintergrund.size > 8 * 1024 * 1024:
                await interaction.followup.send(
                    "Bild zu gross (max 8 MB).", ephemeral=True
                )
                return
            try:
                raw = await hintergrund.read()
                dst = LEVELUP_BG_DIR / "bg.png"
                await asyncio.to_thread(self._save_bg, raw, dst)
                await self.leveling.set_card_bg(guild_id, "bg.png")
                notes.append("Hintergrund gesetzt")
            except Exception as e:  # noqa: BLE001
                logger.error(f"Karten-Hintergrund speichern fehlgeschlagen: {e}")
                await interaction.followup.send(
                    f"Hintergrund speichern fehlgeschlagen: {e}", ephemeral=True
                )
                return

        # Enable-Flag setzen
        await self.leveling.set_card_enabled(guild_id, aktiv)
        notes.append("aktiv" if aktiv else "deaktiviert")

        # Aktiv aber kein Hintergrund -> Hinweis
        if aktiv and not self.leveling.get_card_bg(guild_id):
            await interaction.followup.send(
                "Karte aktiviert, aber noch kein Hintergrund gesetzt. "
                "Lade mit der Option `hintergrund:` ein Bild hoch.",
                ephemeral=True,
            )
            return

        # Vorschau rendern (falls aktiv + Hintergrund vorhanden)
        bg_name = self.leveling.get_card_bg(guild_id)
        if aktiv and bg_name:
            bg_path = LEVELUP_BG_DIR / bg_name
            if bg_path.exists():
                try:
                    user_data = self.leveling.get_user(guild_id, interaction.user.id)
                    lvl = user_data.get("level", 0)
                    avatar_bytes = await interaction.user.display_avatar.read()
                    png = await asyncio.to_thread(
                        render_levelup_card,
                        bg_path,
                        avatar_bytes,
                        interaction.user.display_name,
                        lvl,
                        30,
                        100,
                        self.leveling.get_card_accent(guild_id),
                    )
                    file = discord.File(io.BytesIO(png), filename="preview.png")
                    await interaction.followup.send(
                        content="Vorschau Level-Up-Karte (" + ", ".join(notes) + "):",
                        file=file,
                        ephemeral=True,
                    )
                    return
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"Karten-Vorschau fehlgeschlagen: {e}")

        await interaction.followup.send(
            "Level-Up-Karte: " + ", ".join(notes), ephemeral=True
        )

    # ==================================================================
    # /xp leaderboardcard <aktiv> [hintergrund]
    # ==================================================================

    @xp_grp.command(
        name="leaderboardcard",
        description="Leaderboard als Bild-Karte mit Hintergrund (Dashboard-Fallback)",
    )
    @app_commands.describe(
        aktiv="Bild-Leaderboard an- oder ausschalten (statt Text-Embed)",
        hintergrund="Bild (PNG/JPG) als Hintergrund hochladen",
    )
    @admin_only()
    async def xp_leaderboardcard(
        self,
        interaction: discord.Interaction,
        aktiv: bool,
        hintergrund: Optional[discord.Attachment] = None,
    ) -> None:
        """Leaderboard-Bild-Karte ein-/ausschalten + Hintergrund setzen (B3)."""
        await interaction.response.defer(ephemeral=True)

        if interaction.guild is None:
            await interaction.followup.send(
                "Nur auf einem Server nutzbar.", ephemeral=True
            )
            return
        guild_id = interaction.guild.id

        if not _CARD_AVAILABLE:
            await interaction.followup.send(
                "Bild-Karten nicht verfuegbar — Pillow ist auf dem Server nicht "
                "installiert. `pip install Pillow` und Bot neu starten.",
                ephemeral=True,
            )
            return

        notes: list[str] = []

        # Hintergrund-Upload verarbeiten (eigener Dateiname, getrennt von Level-Up)
        if hintergrund is not None:
            content_type = hintergrund.content_type or ""
            if not content_type.startswith("image/"):
                await interaction.followup.send(
                    "Hintergrund muss ein Bild sein (PNG/JPG).", ephemeral=True
                )
                return
            if hintergrund.size > 8 * 1024 * 1024:
                await interaction.followup.send(
                    "Bild zu gross (max 8 MB).", ephemeral=True
                )
                return
            try:
                raw = await hintergrund.read()
                dst = LEVELUP_BG_DIR / LEADERBOARD_BG_FILE
                await asyncio.to_thread(self._save_bg, raw, dst)
                await self.leveling.set_leaderboard_bg(guild_id, LEADERBOARD_BG_FILE)
                notes.append("Hintergrund gesetzt")
            except Exception as e:  # noqa: BLE001
                logger.error(f"Leaderboard-Hintergrund speichern fehlgeschlagen: {e}")
                await interaction.followup.send(
                    f"Hintergrund speichern fehlgeschlagen: {e}", ephemeral=True
                )
                return

        await self.leveling.set_leaderboard_card_enabled(guild_id, aktiv)
        notes.append("aktiv" if aktiv else "deaktiviert")

        # Aktiv aber kein Hintergrund -> Hinweis
        if aktiv and not self.leveling.get_leaderboard_bg(guild_id):
            await interaction.followup.send(
                "Leaderboard-Karte aktiviert, aber noch kein Hintergrund gesetzt. "
                "Lade mit der Option `hintergrund:` ein Bild hoch.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            "Leaderboard-Karte: " + ", ".join(notes)
            + " (Akzentfarbe teilt sich mit der Level-Up-Karte).",
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
        if isinstance(error, app_commands.CommandOnCooldown):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"Bitte warte noch {error.retry_after:.0f}s.", ephemeral=True
                )
            return
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
