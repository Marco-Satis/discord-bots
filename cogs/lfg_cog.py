"""
LFG-Cog („Looking for Group", #spielersuche).

Leichtgewichtig (Marco-Scope): keine Spielsuche-Datenbank, kein Auto-Voice —
nur eine **selbst-zuweisbare Ping-Rolle** plus ein Ping-Befehl, damit man
Mitspieler findet ohne @everyone zu nutzen.

  - `/lfg ping [nachricht]` — pingt die LFG-Rolle (Cooldown, kein @everyone).
  - `/lfg an` / `/lfg aus`   — LFG-Ping-Rolle selbst geben / entfernen.
  - `/lfg panel [channel]`   — (Admin) persistenten An/Aus-Button posten.
  - `/lfg setup …`           — (Admin) Rolle/Channel/Cooldown setzen.
  - `/lfg status`            — (Admin) aktuelle Config.

Config je Guild in `guild_config` (Prefix `lfg.`, Dashboard-editierbar):
  lfg.role_id, lfg.channel_id (optional Beschränkung), lfg.cooldown_seconds.

„Talk ist offen" (03.11.2026): betritt jemand den Voice, waehrend sonst
niemand drin ist, pingt der Bot nach kurzer Wartezeit eine freiwillige Rolle.
Wird der Voice wieder leer, verschwindet die Meldung. Keys:
  lfg.talk_aktiv, lfg.talk_role_id, lfg.talk_channel_id, lfg.talk_pause_minuten.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from modules.guild_context import GuildConfig
from utils.logger import get_logger
from utils.embeds import info_embed

logger = get_logger("cogs.lfg")

# Standard-Cooldown fuer /lfg ping (Sekunden, per-Guild ueberschreibbar).
_DEFAULT_COOLDOWN = 300
# Obergrenze fuer die Ping-Nachricht (vor Escape).
_MSG_CAP = 200
# Talk ist offen: so lange muss jemand im Voice bleiben, bevor gepingt wird
# (kurzes Rein-und-Raus und der Sprung vom Hub in den Temp-Talk pingen nicht).
_TALK_WARTEN_SEKUNDEN = 30
# Standard-Pause zwischen zwei Talk-Pings (Minuten).
_TALK_PAUSE_MINUTEN = 60


def menschen_im_voice(guild: discord.Guild, ohne: Optional[int] = None) -> list[discord.Member]:
    """Menschen in Voice-Channels der Guild (ohne AFK-Kanal und ohne `ohne`)."""
    afk = guild.afk_channel.id if guild.afk_channel else None
    return [
        m
        for ch in guild.voice_channels
        if ch.id != afk
        for m in ch.members
        if not m.bot and m.id != ohne
    ]


async def _get_talk_role(guild: discord.Guild) -> Optional[discord.Role]:
    """Konfigurierte Rolle fuer „Talk ist offen" (oder None)."""
    role_id = await GuildConfig.get(guild.id, "lfg.talk_role_id")
    try:
        return guild.get_role(int(role_id)) if role_id else None
    except (TypeError, ValueError):
        return None


async def _get_role(guild: discord.Guild) -> Optional[discord.Role]:
    """Konfigurierte LFG-Rolle der Guild auflösen (oder None)."""
    role_id = await GuildConfig.get(guild.id, "lfg.role_id")
    if not role_id:
        return None
    try:
        return guild.get_role(int(role_id))
    except (TypeError, ValueError):
        return None


class LFGToggleView(discord.ui.View):
    """Persistenter An/Aus-Button für die LFG-Ping-Rolle (timeout=None)."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="LFG-Pings an/aus",
        style=discord.ButtonStyle.primary,
        custom_id="lfg:toggle",
        emoji="🎮",
    )
    async def toggle_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        """Gibt dem Klickenden die LFG-Rolle oder nimmt sie ihm (Toggle)."""
        if interaction.guild is None or not isinstance(
            interaction.user, discord.Member
        ):
            await interaction.response.send_message(
                "Nur auf einem Server nutzbar.", ephemeral=True
            )
            return

        role = await _get_role(interaction.guild)
        if role is None:
            await interaction.response.send_message(
                "LFG ist nicht eingerichtet — bitte Admin informieren.",
                ephemeral=True,
            )
            return

        member = interaction.user
        try:
            if role in member.roles:
                await member.remove_roles(role, reason="LFG-Pings abbestellt")
                await interaction.response.send_message(
                    "Du erhältst **keine** LFG-Pings mehr.", ephemeral=True
                )
            else:
                await member.add_roles(role, reason="LFG-Pings abonniert")
                await interaction.response.send_message(
                    "Du erhältst jetzt **LFG-Pings**.", ephemeral=True
                )
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.warning(f"LFG-Rolle nicht setzbar: {e}")
            await interaction.response.send_message(
                "Konnte die Rolle nicht ändern (Bot-Rechte / Rollen-Hierarchie?).",
                ephemeral=True,
            )


class TalkToggleView(discord.ui.View):
    """Persistenter An/Aus-Button für die „Talk ist offen"-Rolle (timeout=None)."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Talk-Pings an/aus",
        style=discord.ButtonStyle.primary,
        custom_id="lfg:talk_toggle",
        emoji="🔊",
    )
    async def toggle_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        """Gibt dem Klickenden die Talk-Rolle oder nimmt sie ihm."""
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Nur auf einem Server nutzbar.", ephemeral=True)
            return
        role = await _get_talk_role(interaction.guild)
        if role is None:
            await interaction.response.send_message(
                "„Talk ist offen“ ist nicht eingerichtet — bitte Admin informieren.",
                ephemeral=True,
            )
            return
        member = interaction.user
        try:
            if role in member.roles:
                await member.remove_roles(role, reason="Talk-Pings abbestellt")
                text = "Du bekommst **keine** Meldung mehr, wenn der Talk aufgeht."
            else:
                await member.add_roles(role, reason="Talk-Pings abonniert")
                text = "Du bekommst jetzt eine Meldung, sobald jemand im Talk ist."
            await interaction.response.send_message(text, ephemeral=True)
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.warning(f"Talk-Rolle nicht setzbar: {e}")
            await interaction.response.send_message(
                "Konnte die Rolle nicht ändern (Bot-Rechte / Rollen-Hierarchie?).",
                ephemeral=True,
            )


class LFGCog(commands.Cog):
    """LFG: selbst-zuweisbare Ping-Rolle + Ping-Befehl + „Talk ist offen"."""

    lfg = app_commands.Group(
        name="lfg",
        description="Mitspieler suchen (Looking for Group)",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # (guild_id, user_id) -> letzter Ping-Zeitpunkt (monotonic) fuer Cooldown
        self._last_ping: dict[tuple[int, int], float] = {}
        # Talk ist offen, je Guild: letzter Ping (monotonic), gesendete Meldung,
        # laufende Wartezeit
        self._talk_zuletzt: dict[int, float] = {}
        self._talk_meldung: dict[int, discord.Message] = {}
        self._talk_warten: dict[int, asyncio.Task] = {}

    async def cog_load(self) -> None:
        """Persistente Toggle-Views registrieren (überleben Bot-Restart)."""
        self.bot.add_view(LFGToggleView())
        self.bot.add_view(TalkToggleView())
        logger.info("LFG-Cog geladen, persistente Views registriert")

    async def cog_unload(self) -> None:
        for task in self._talk_warten.values():
            task.cancel()

    # ==================================================================
    # Talk ist offen
    # ==================================================================

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Erster Mensch im Voice -> nach Wartezeit pingen; Voice leer -> Meldung weg."""
        if member.bot or before.channel == after.channel:
            return
        guild = member.guild
        if after.channel is not None and not before.channel:
            if not await GuildConfig.get(guild.id, "lfg.talk_aktiv", False):
                return
            if guild.afk_channel and after.channel.id == guild.afk_channel.id:
                return
            # Nur wenn vorher niemand im Voice war und keine Wartezeit laeuft
            if menschen_im_voice(guild, ohne=member.id) or guild.id in self._talk_warten:
                return
            self._talk_warten[guild.id] = asyncio.create_task(self._talk_pruefen(guild, member))
        elif before.channel is not None and not menschen_im_voice(guild):
            # Letzter Mensch ist raus (oder in den AFK-Kanal gewechselt)
            await self._talk_aufraeumen(guild)

    async def _talk_pruefen(self, guild: discord.Guild, member: discord.Member) -> None:
        """Nach der Wartezeit pruefen und ggf. die Meldung senden."""
        try:
            await asyncio.sleep(_TALK_WARTEN_SEKUNDEN)
            await self._talk_melden(guild, member)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — Listener darf nicht sterben
            logger.error(f"[{guild.id}] Talk-ist-offen fehlgeschlagen: {e}", exc_info=True)
        finally:
            self._talk_warten.pop(guild.id, None)

    async def _talk_melden(self, guild: discord.Guild, member: discord.Member) -> None:
        voice = member.voice.channel if member.voice else None
        if voice is None or (guild.afk_channel and voice.id == guild.afk_channel.id):
            return
        try:
            pause = int(await GuildConfig.get(guild.id, "lfg.talk_pause_minuten", _TALK_PAUSE_MINUTEN))
        except (TypeError, ValueError):
            pause = _TALK_PAUSE_MINUTEN
        zuletzt = self._talk_zuletzt.get(guild.id)
        if zuletzt is not None and time.monotonic() - zuletzt < max(0, pause) * 60:
            return
        role = await _get_talk_role(guild)
        try:
            kanal = guild.get_channel(int(await GuildConfig.get(guild.id, "lfg.talk_channel_id", 0) or 0))
        except (TypeError, ValueError):
            kanal = None
        if role is None or not isinstance(kanal, discord.TextChannel):
            logger.warning(f"[{guild.id}] Talk ist offen: Rolle oder Kanal fehlt")
            return
        name = discord.utils.escape_markdown(member.display_name)
        embed = info_embed(
            title="🔊 Talk ist offen",
            description=f"**{name}** ist gerade in {voice.mention}. Komm dazu!",
        )
        embed.set_footer(text="Meldung an/aus: Button „Talk-Pings an/aus“")
        try:
            msg = await kanal.send(
                content=role.mention, embed=embed,
                allowed_mentions=discord.AllowedMentions(everyone=False, users=False, roles=[role]),
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.warning(f"[{guild.id}] Talk-ist-offen nicht sendbar: {e}")
            return
        self._talk_zuletzt[guild.id] = time.monotonic()
        self._talk_meldung[guild.id] = msg
        logger.info(f"[{guild.id}] Talk ist offen gemeldet ({voice.id})")

    async def _talk_aufraeumen(self, guild: discord.Guild) -> None:
        """Voice ist leer: Wartezeit abbrechen, Meldung loeschen."""
        task = self._talk_warten.pop(guild.id, None)
        if task is not None:
            task.cancel()
        msg = self._talk_meldung.pop(guild.id, None)
        if msg is not None:
            try:
                await msg.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
                logger.debug(f"Talk-Meldung nicht loeschbar: {e}")

    @lfg.command(name="talkpanel", description="(Admin) An/Aus-Button für „Talk ist offen“ posten")
    @app_commands.describe(channel="Channel für das Panel (leer = hier)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def lfg_talkpanel(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        """Postet ein Embed mit persistentem An/Aus-Button für die Talk-Rolle."""
        await interaction.response.defer(ephemeral=True)
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.followup.send("Bitte einen Text-Channel angeben.", ephemeral=True)
            return
        embed = info_embed(
            title="🔊 Talk ist offen",
            description=(
                "Willst du wissen, wann jemand im Talk sitzt?\n"
                "Klick den Button, dann pingt dich der Bot, sobald der Talk aufgeht."
            ),
        )
        try:
            await target.send(embed=embed, view=TalkToggleView())
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.followup.send(f"Panel konnte nicht gepostet werden: {e}", ephemeral=True)
            return
        await interaction.followup.send(f"Talk-Panel in {target.mention} gepostet.", ephemeral=True)

    async def _cooldown_left(self, guild_id: int, user_id: int) -> int:
        """Verbleibende Cooldown-Sekunden für /lfg ping (0 = frei)."""
        try:
            cd = int(
                await GuildConfig.get(guild_id, "lfg.cooldown_seconds", _DEFAULT_COOLDOWN)
                or _DEFAULT_COOLDOWN
            )
        except (TypeError, ValueError):
            cd = _DEFAULT_COOLDOWN
        cd = max(0, cd)
        last = self._last_ping.get((guild_id, user_id))
        if last is None:
            return 0
        elapsed = time.monotonic() - last
        return max(0, int(cd - elapsed))

    # ==================================================================
    # /lfg ping
    # ==================================================================

    @lfg.command(name="ping", description="Mitspieler suchen — pingt die LFG-Rolle")
    @app_commands.describe(nachricht="Optionaler Text, z.B. 2 für Ranked gesucht")
    async def lfg_ping(
        self, interaction: discord.Interaction, nachricht: Optional[str] = None
    ) -> None:
        """Pingt die LFG-Rolle (kein @everyone), mit per-Guild-Cooldown."""
        if interaction.guild is None or not isinstance(
            interaction.user, discord.Member
        ):
            await interaction.response.send_message(
                "Nur auf einem Server nutzbar.", ephemeral=True
            )
            return

        guild = interaction.guild
        role = await _get_role(guild)
        if role is None:
            await interaction.response.send_message(
                "LFG ist nicht eingerichtet (`/lfg setup`).", ephemeral=True
            )
            return

        # Optionale Channel-Beschränkung
        channel_id = await GuildConfig.get(guild.id, "lfg.channel_id")
        if channel_id:
            try:
                allowed = int(channel_id)
            except (TypeError, ValueError):
                allowed = None
            if allowed and interaction.channel_id != allowed:
                ch = guild.get_channel(allowed)
                hint = ch.mention if isinstance(ch, discord.TextChannel) else "den LFG-Kanal"
                await interaction.response.send_message(
                    f"LFG-Pings bitte in {hint}.", ephemeral=True
                )
                return

        # Cooldown
        left = await self._cooldown_left(guild.id, interaction.user.id)
        if left > 0:
            await interaction.response.send_message(
                f"Bitte noch **{left}s** warten bis zum nächsten LFG-Ping.",
                ephemeral=True,
            )
            return

        # Nachricht escapen (Markdown-Injection-Schutz)
        text = ""
        if nachricht:
            text = discord.utils.escape_markdown(nachricht.strip()[:_MSG_CAP])

        desc = f"{interaction.user.mention} sucht Mitspieler!"
        if text:
            desc += f"\n> {text}"
        embed = info_embed(
            title="🎮 Looking for Group", description=desc,
        )

        # Nur die LFG-Rolle pingen — kein @everyone/@here, keine User-Pings.
        allowed_mentions = discord.AllowedMentions(
            everyone=False, users=False, roles=[role]
        )
        try:
            await interaction.response.send_message(
                content=role.mention, embed=embed, allowed_mentions=allowed_mentions
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.warning(f"LFG-Ping nicht sendbar: {e}")
            await interaction.response.send_message(
                "Konnte den Ping nicht senden.", ephemeral=True
            )
            return

        self._last_ping[(guild.id, interaction.user.id)] = time.monotonic()
        logger.info(f"LFG-Ping Guild {guild.id} von {interaction.user.id}")

    # ==================================================================
    # /lfg an / aus  (Self-Subscribe)
    # ==================================================================

    @lfg.command(name="an", description="LFG-Pings abonnieren (Rolle selbst geben)")
    async def lfg_on(self, interaction: discord.Interaction) -> None:
        """Gibt dem Aufrufer die LFG-Rolle."""
        await self._self_role(interaction, add=True)

    @lfg.command(name="aus", description="LFG-Pings abbestellen (Rolle entfernen)")
    async def lfg_off(self, interaction: discord.Interaction) -> None:
        """Entfernt dem Aufrufer die LFG-Rolle."""
        await self._self_role(interaction, add=False)

    async def _self_role(self, interaction: discord.Interaction, add: bool) -> None:
        """LFG-Rolle dem Aufrufer geben/entfernen."""
        if interaction.guild is None or not isinstance(
            interaction.user, discord.Member
        ):
            await interaction.response.send_message(
                "Nur auf einem Server nutzbar.", ephemeral=True
            )
            return
        role = await _get_role(interaction.guild)
        if role is None:
            await interaction.response.send_message(
                "LFG ist nicht eingerichtet (`/lfg setup`).", ephemeral=True
            )
            return
        member = interaction.user
        try:
            if add:
                if role in member.roles:
                    await interaction.response.send_message(
                        "Du erhältst bereits LFG-Pings.", ephemeral=True
                    )
                    return
                await member.add_roles(role, reason="LFG-Pings abonniert")
                await interaction.response.send_message(
                    "Du erhältst jetzt **LFG-Pings**.", ephemeral=True
                )
            else:
                if role not in member.roles:
                    await interaction.response.send_message(
                        "Du hattest die LFG-Rolle nicht.", ephemeral=True
                    )
                    return
                await member.remove_roles(role, reason="LFG-Pings abbestellt")
                await interaction.response.send_message(
                    "Du erhältst **keine** LFG-Pings mehr.", ephemeral=True
                )
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.warning(f"LFG-Rolle nicht setzbar: {e}")
            await interaction.response.send_message(
                "Konnte die Rolle nicht ändern (Bot-Rechte / Rollen-Hierarchie?).",
                ephemeral=True,
            )

    # ==================================================================
    # /lfg panel  (Admin) — persistenter Toggle-Button
    # ==================================================================

    @lfg.command(
        name="panel", description="(Admin) An/Aus-Button für LFG-Pings posten"
    )
    @app_commands.describe(channel="Channel für das Panel (leer = hier)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def lfg_panel(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        """Postet ein Embed mit persistentem An/Aus-Button."""
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
        embed = info_embed(
            title="🎮 LFG — Mitspieler-Pings",
            description=(
                "Klicke den Button, um **LFG-Pings an/aus** zu schalten.\n"
                "Wer die Rolle hat, wird bei `/lfg ping` benachrichtigt."
            ),
        )
        try:
            await target.send(embed=embed, view=LFGToggleView())
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.followup.send(
                f"Panel konnte nicht gepostet werden: {e}", ephemeral=True
            )
            return
        await interaction.followup.send(
            f"LFG-Panel in {target.mention} gepostet.", ephemeral=True
        )

    # ==================================================================
    # /lfg setup / status  (Admin)
    # ==================================================================

    @lfg.command(name="setup", description="(Admin) LFG-Rolle/Channel/Cooldown setzen")
    @app_commands.describe(
        rolle="Rolle die bei /lfg ping benachrichtigt wird",
        channel="Optional: Channel auf den /lfg ping beschränkt wird",
        cooldown="Cooldown für /lfg ping in Sekunden (Standard 300)",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def lfg_setup(
        self,
        interaction: discord.Interaction,
        rolle: discord.Role,
        channel: Optional[discord.TextChannel] = None,
        cooldown: Optional[int] = None,
    ) -> None:
        """LFG-Config in guild_config schreiben (Dashboard-kompatibel)."""
        await interaction.response.defer(ephemeral=True)
        if interaction.guild is None:
            await interaction.followup.send("Nur auf einem Server.", ephemeral=True)
            return
        gid = interaction.guild.id
        await GuildConfig.set(gid, "lfg.role_id", str(rolle.id), updated_by="command")
        await GuildConfig.set(
            gid, "lfg.channel_id", str(channel.id) if channel else "",
            updated_by="command",
        )
        if cooldown is not None:
            await GuildConfig.set(
                gid, "lfg.cooldown_seconds", max(0, min(3600, cooldown)),
                updated_by="command",
            )
        ch_txt = channel.mention if channel else "überall"
        await interaction.followup.send(
            f"LFG eingerichtet — Rolle **{rolle.name}**, Ping-Kanal: {ch_txt}.",
            ephemeral=True,
        )
        logger.info(f"LFG-Setup Guild {gid} von {interaction.user.id}")

    @lfg.command(name="status", description="(Admin) LFG-Einstellungen anzeigen")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def lfg_status(self, interaction: discord.Interaction) -> None:
        """Aktuelle LFG-Config anzeigen."""
        await interaction.response.defer(ephemeral=True)
        if interaction.guild is None:
            await interaction.followup.send("Nur auf einem Server.", ephemeral=True)
            return
        guild = interaction.guild
        role = await _get_role(guild)
        channel_id = await GuildConfig.get(guild.id, "lfg.channel_id")
        cd = await GuildConfig.get(guild.id, "lfg.cooldown_seconds", _DEFAULT_COOLDOWN)
        ch = None
        if channel_id:
            try:
                ch = guild.get_channel(int(channel_id))
            except (TypeError, ValueError):
                ch = None
        embed = info_embed(title="LFG-Status")
        embed.add_field(
            name="Rolle", value=role.name if role else "Nicht gesetzt", inline=False
        )
        embed.add_field(
            name="Ping-Kanal",
            value=ch.mention if isinstance(ch, discord.TextChannel) else "überall",
            inline=False,
        )
        embed.add_field(name="Cooldown", value=f"{cd}s", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Zentrale Fehlerbehandlung."""
        if isinstance(error, (app_commands.CheckFailure, app_commands.MissingPermissions)):
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
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Exception swallowed: {e}")


async def setup(bot: commands.Bot) -> None:
    """Registriert den LFG-Cog beim Bot."""
    await bot.add_cog(LFGCog(bot))
