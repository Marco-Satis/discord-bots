"""
Moderation Cog — Wortfilter + Anti-Spam für Discord (Admin Bot)

Command-Struktur:
  /filter add <wort>      — Wort zur Filterliste hinzufuegen (Admin)
  /filter remove <wort>   — Wort aus der Filterliste entfernen (Admin)
  /filter list             — Aktuelle Filterliste anzeigen (Admin)
  /filter toggle           — Filter ein-/ausschalten (Admin)

on_message Listener:
  - Prueft jede Nachricht gegen Wortfilter + Anti-Spam
  - Loescht gefilterte/Spam-Nachrichten
  - Warnung per ephemeraler Antwort (wo möglich)
  - Auto-Mute bei Spam-Erkennung

NICHT für RCON/In-Game — der bestehende modules/word_filter.py
und modules/anti_spam.py bleiben unveraendert.
"""

import re as _re
from datetime import timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from modules.moderation.word_filter import DiscordWordFilter
from modules.moderation.anti_spam import DiscordAntiSpam
from utils.logger import get_logger
from utils.permissions import admin_only, is_admin

logger = get_logger("cogs.moderation")

# Keine Mentions in Bot-Nachrichten
_NO_MENTIONS = discord.AllowedMentions.none()


def _sanitize_input(text: str, max_length: int = 200) -> str:
    """Sanitisiert User-Input. Erlaubt nur sichere Zeichen."""
    sanitized = _re.sub(r'[^\w\s\-.:!?]', '', text, flags=_re.UNICODE)
    return sanitized[:max_length].strip()


class ModerationCog(commands.Cog):
    """Wortfilter + Anti-Spam für Discord-Nachrichten"""

    # Slash-Command-Gruppe: /filter
    filter_grp = app_commands.Group(
        name="filter",
        description="Wortfilter verwalten (Admin)",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        config = getattr(bot, "config", {})

        # Wortfilter initialisieren
        self.word_filter = DiscordWordFilter(config)

        # Anti-Spam initialisieren (Werte aus Config oder Defaults)
        spam_config = config.get("anti_spam", {})
        self.anti_spam = DiscordAntiSpam(
            max_messages=spam_config.get("max_messages", 5),
            window_seconds=spam_config.get("window_seconds", 10),
            cooldown_seconds=spam_config.get("cooldown_seconds", 30),
        )

    async def cog_load(self) -> None:
        """Persistierte Filterliste laden + Cleanup-Task starten"""
        await self.word_filter.load()
        self._cleanup_task.start()
        logger.info(
            f"ModerationCog geladen: {self.word_filter.count} Filterwoerter, "
            f"Anti-Spam aktiv"
        )

    async def cog_unload(self) -> None:
        """Cleanup-Task stoppen"""
        self._cleanup_task.cancel()

    # ==================================================================
    # Background-Task: Anti-Spam Daten regelmaessig bereinigen
    # ==================================================================

    @tasks.loop(minutes=30)
    async def _cleanup_task(self) -> None:
        """Abgelaufene Anti-Spam Tracking-Daten entfernen"""
        self.anti_spam.cleanup()

    @_cleanup_task.before_loop
    async def _before_cleanup(self) -> None:
        await self.bot.wait_until_ready()

    # ==================================================================
    # on_message Listener
    # ==================================================================

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """
        Prueft jede Nachricht gegen Wortfilter und Anti-Spam.

        Ablauf:
        1. Bot-Nachrichten und DMs überspringen
        2. Admins/Owner von Anti-Spam ausnehmen (Wortfilter gilt für alle)
        3. Wortfilter prüfen -> Nachricht löschen + Warnung
        4. Anti-Spam prüfen -> Nachricht löschen + Auto-Mute
        """
        # Bot-Nachrichten ignorieren
        if message.author.bot:
            return

        # DMs ignorieren (kein Guild-Kontext)
        if not message.guild:
            return

        # --- Wortfilter prüfen (gilt für alle User) ---
        is_filtered, matched_word = self.word_filter.check_message(
            message.content
        )
        if is_filtered:
            await self._handle_filtered_message(message, matched_word)
            return

        # Admins/Owner von Anti-Spam ausnehmen
        if self._is_privileged(message):
            return

        # --- Anti-Spam prüfen ---
        # Mention-Anzahl: User-Mentions + Role-Mentions
        mention_count = len(message.mentions) + len(message.role_mentions)

        is_spam, reason = self.anti_spam.check_message(
            user_id=message.author.id,
            content=message.content,
            mention_count=mention_count,
        )
        if is_spam:
            await self._handle_spam_message(message, reason)

    # ------------------------------------------------------------------
    # Handler: Gefilterte Nachricht
    # ------------------------------------------------------------------

    async def _handle_filtered_message(
        self,
        message: discord.Message,
        matched_word: str | None,
    ) -> None:
        """Gefilterte Nachricht löschen und User warnen"""
        # Nachricht löschen
        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as e:
            logger.warning(
                f"Gefilterte Nachricht konnte nicht gelöscht werden: {e}"
            )

        # Warnung senden (verschwindet nach 10 Sekunden)
        try:
            await message.channel.send(
                f"{message.author.mention}, deine Nachricht wurde entfernt "
                f"(Wortfilter).",
                allowed_mentions=_NO_MENTIONS,
                delete_after=10.0,
            )
        except (discord.Forbidden, discord.HTTPException):
            pass

        logger.info(
            f"Wortfilter: Nachricht von {message.author} "
            f"(ID: {message.author.id}) in #{message.channel.name} gelöscht. "
            f"Match: '{matched_word}'"
        )

    # ------------------------------------------------------------------
    # Handler: Spam-Nachricht
    # ------------------------------------------------------------------

    async def _handle_spam_message(
        self,
        message: discord.Message,
        reason: str | None,
    ) -> None:
        """Spam-Nachricht löschen und User temporaer muten"""
        # Nachricht löschen
        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as e:
            logger.warning(
                f"Spam-Nachricht konnte nicht gelöscht werden: {e}"
            )

        # Auto-Mute via Discord Timeout
        cooldown = self.anti_spam.cooldown_seconds
        try:
            if isinstance(message.author, discord.Member):
                await message.author.timeout(
                    timedelta(seconds=cooldown),
                    reason=f"Anti-Spam: {reason}",
                )
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.warning(
                f"Auto-Mute fehlgeschlagen für {message.author}: {e}"
            )

        # Warnung senden
        try:
            await message.channel.send(
                f"{message.author.mention}, Spam erkannt — "
                f"{cooldown}s Timeout. Grund: {reason}",
                allowed_mentions=_NO_MENTIONS,
                delete_after=15.0,
            )
        except (discord.Forbidden, discord.HTTPException):
            pass

        logger.info(
            f"Anti-Spam: Nachricht von {message.author} "
            f"(ID: {message.author.id}) in #{message.channel.name} gelöscht. "
            f"Grund: {reason}"
        )

    # ------------------------------------------------------------------
    # Hilfsmethoden
    # ------------------------------------------------------------------

    @staticmethod
    def _is_privileged(message: discord.Message) -> bool:
        """Prueft ob der Autor Admin oder Owner ist (Anti-Spam-Ausnahme)"""
        if not message.guild or not isinstance(message.author, discord.Member):
            return False

        # Guild-Permissions prüfen (Administrator-Berechtigung)
        if message.author.guild_permissions.administrator:
            return True

        # ADMIN_ROLE_ID aus Umgebungsvariable prüfen
        import os
        admin_role_id = int(os.getenv("ADMIN_ROLE_ID", "0"))
        if admin_role_id:
            return any(r.id == admin_role_id for r in message.author.roles)

        return False

    # ==================================================================
    # /filter add <wort>
    # ==================================================================

    @filter_grp.command(
        name="add",
        description="Wort zur Filterliste hinzufuegen",
    )
    @app_commands.describe(
        wort="Wort oder regex:<pattern> das gefiltert werden soll"
    )
    @admin_only()
    async def filter_add(
        self,
        interaction: discord.Interaction,
        wort: str,
    ) -> None:
        # Input sanitisieren (aber regex:-Prefix beibehalten)
        if wort.startswith("regex:"):
            # Regex-Pattern: nur Laenge begrenzen, nicht sanitisieren
            safe_word = wort[:200]
        else:
            safe_word = _sanitize_input(wort, max_length=100)

        if not safe_word:
            await interaction.response.send_message(
                "Ungueltiges Wort.", ephemeral=True
            )
            return

        success = await self.word_filter.add_word(safe_word)

        if success:
            await interaction.response.send_message(
                f"Filterwort hinzugefuegt: `{safe_word}` "
                f"({self.word_filter.count} Woerter gesamt)",
                ephemeral=True,
                allowed_mentions=_NO_MENTIONS,
            )
        else:
            await interaction.response.send_message(
                f"Wort `{safe_word}` ist bereits in der Filterliste.",
                ephemeral=True,
                allowed_mentions=_NO_MENTIONS,
            )

    # ==================================================================
    # /filter remove <wort>
    # ==================================================================

    @filter_grp.command(
        name="remove",
        description="Wort aus der Filterliste entfernen",
    )
    @app_commands.describe(
        wort="Wort das aus der Filterliste entfernt werden soll"
    )
    @admin_only()
    async def filter_remove(
        self,
        interaction: discord.Interaction,
        wort: str,
    ) -> None:
        success = await self.word_filter.remove_word(wort)

        if success:
            await interaction.response.send_message(
                f"Filterwort entfernt: `{wort}` "
                f"({self.word_filter.count} Woerter verbleibend)",
                ephemeral=True,
                allowed_mentions=_NO_MENTIONS,
            )
        else:
            await interaction.response.send_message(
                f"Wort `{wort}` wurde nicht in der Filterliste gefunden.",
                ephemeral=True,
                allowed_mentions=_NO_MENTIONS,
            )

    # ==================================================================
    # /filter list
    # ==================================================================

    @filter_grp.command(
        name="list",
        description="Aktuelle Filterliste anzeigen",
    )
    @admin_only()
    async def filter_list(
        self,
        interaction: discord.Interaction,
    ) -> None:
        words = self.word_filter.get_words()
        status = "aktiviert" if self.word_filter.enabled else "deaktiviert"

        if not words:
            await interaction.response.send_message(
                f"Filterliste ist leer (Filter: {status}).",
                ephemeral=True,
            )
            return

        # Wortliste formatieren
        # Aufteilen in normale Woerter und Regex-Muster
        normal_words: list[str] = []
        regex_patterns: list[str] = []

        for w in words:
            if w.startswith("regex:"):
                regex_patterns.append(f"`{w}`")
            else:
                normal_words.append(f"`{w}`")

        embed = discord.Embed(
            title=f"Wortfilter ({status})",
            color=0x2ecc71 if self.word_filter.enabled else 0x95a5a6,
        )

        if normal_words:
            # Auf mehrere Fields aufteilen falls noetig (1024 Zeichen Limit)
            word_text = ", ".join(normal_words)
            if len(word_text) <= 1024:
                embed.add_field(
                    name=f"Woerter ({len(normal_words)})",
                    value=word_text,
                    inline=False,
                )
            else:
                # Aufteilen in Chunks
                chunks: list[str] = []
                current_chunk: list[str] = []
                current_len = 0
                for w in normal_words:
                    addition = len(w) + 2  # +2 für ", "
                    if current_len + addition > 1000:
                        chunks.append(", ".join(current_chunk))
                        current_chunk = [w]
                        current_len = len(w)
                    else:
                        current_chunk.append(w)
                        current_len += addition
                if current_chunk:
                    chunks.append(", ".join(current_chunk))

                for i, chunk in enumerate(chunks):
                    embed.add_field(
                        name=f"Woerter ({i + 1}/{len(chunks)})",
                        value=chunk,
                        inline=False,
                    )

        if regex_patterns:
            regex_text = "\n".join(regex_patterns)
            if len(regex_text) > 1024:
                regex_text = regex_text[:1020] + "..."
            embed.add_field(
                name=f"Regex-Muster ({len(regex_patterns)})",
                value=regex_text,
                inline=False,
            )

        embed.set_footer(text=f"Gesamt: {len(words)} Eintraege")

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=_NO_MENTIONS,
        )

    # ==================================================================
    # /filter toggle
    # ==================================================================

    @filter_grp.command(
        name="toggle",
        description="Wortfilter ein-/ausschalten",
    )
    @admin_only()
    async def filter_toggle(
        self,
        interaction: discord.Interaction,
    ) -> None:
        new_state = self.word_filter.toggle()
        # Persistieren
        await self.word_filter._save()

        status = "aktiviert" if new_state else "deaktiviert"
        color_hex = "🟢" if new_state else "🔴"

        await interaction.response.send_message(
            f"Wortfilter wurde **{status}** {color_hex}",
            ephemeral=True,
            allowed_mentions=_NO_MENTIONS,
        )
        logger.info(
            f"Wortfilter {status} von {interaction.user} "
            f"(ID: {interaction.user.id})"
        )

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
                    "Keine Berechtigung für diesen Befehl.",
                    ephemeral=True,
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
    """Cog zum Bot hinzufuegen"""
    await bot.add_cog(ModerationCog(bot))
