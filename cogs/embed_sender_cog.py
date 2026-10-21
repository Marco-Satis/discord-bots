"""
Embed Sender Cog — Verarbeitet Embed-Auftraege aus dem Web-Dashboard.

Liest periodisch die Queue in data/admin/embed_queue/ und sendet
die Embeds in die angegebenen Discord-Kanaele.
"""

import json
import asyncio
import discord
from pathlib import Path
from discord.ext import commands, tasks

from modules.platzhalter import render
from utils.config import DATA_DIR
from utils.embeds import COLOR_BRAND
from utils.logger import get_logger
from utils.loop_guard import guard

logger = get_logger("cogs.embed_sender")

QUEUE_DIR = DATA_DIR / "admin" / "embed_queue"


def _hex_to_color(hex_str: str) -> discord.Color:
    """Konvertiert einen Hex-Farbwert (#RRGGBB) in discord.Color."""
    hex_str = hex_str.lstrip("#")
    try:
        value = int(hex_str, 16)
        return discord.Color(value)
    except (ValueError, TypeError):
        return discord.Color(COLOR_BRAND)  # ungueltige Eingabe -> C3-Violett


class EmbedSenderCog(commands.Cog, name="Embed Sender"):
    """Verarbeitet Embed-Auftraege aus der Web-Dashboard-Queue."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        QUEUE_DIR.mkdir(parents=True, exist_ok=True)
        logger.info("EmbedSenderCog geladen")

    async def cog_load(self) -> None:
        """Startet den Queue-Worker beim Laden des Cogs."""
        guard(self.process_queue, name="process_queue")
        self.process_queue.start()
        logger.info("Embed-Queue-Worker gestartet")

    async def cog_unload(self) -> None:
        """Stoppt den Queue-Worker beim Entladen."""
        self.process_queue.cancel()
        logger.info("Embed-Queue-Worker gestoppt")

    @tasks.loop(seconds=10)
    async def process_queue(self) -> None:
        """Prueft alle 10 Sekunden die Queue auf neue Embed-Auftraege."""
        try:
            if not QUEUE_DIR.exists():
                return

            for queue_file in sorted(QUEUE_DIR.glob("embed_*.json")):
                await self._process_embed_file(queue_file)
        except Exception as e:
            logger.error(f"Fehler beim Verarbeiten der Embed-Queue: {e}", exc_info=True)

    @process_queue.before_loop
    async def before_process_queue(self) -> None:
        """Wartet bis der Bot bereit ist."""
        await self.bot.wait_until_ready()

    async def _process_embed_file(self, filepath: Path) -> None:
        """Verarbeitet eine einzelne Embed-Queue-Datei."""
        try:
            def _read():
                with open(filepath, "r", encoding="utf-8") as f:
                    return json.load(f)
            data = await asyncio.to_thread(_read)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Fehlerhafte Queue-Datei {filepath.name}: {e}")
            # Fehlerhafte Datei entfernen
            filepath.unlink(missing_ok=True)
            return

        # Nur ausstehende Embeds verarbeiten
        if data.get("status") != "pending":
            filepath.unlink(missing_ok=True)
            return

        channel_id = data.get("channel_id", "").strip()
        if not channel_id or not channel_id.isdigit():
            logger.warning(f"Ungueltige Channel-ID in {filepath.name}: '{channel_id}'")
            filepath.unlink(missing_ok=True)
            return

        # Discord-Kanal finden
        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(int(channel_id))
            except discord.NotFound:
                logger.warning(f"Kanal {channel_id} nicht gefunden")
                filepath.unlink(missing_ok=True)
                return
            except discord.Forbidden:
                logger.warning(f"Kein Zugriff auf Kanal {channel_id}")
                filepath.unlink(missing_ok=True)
                return

        # Platzhalter: ohne ausloesendes Mitglied gibt es nur Server-/Zeit-Werte
        # ({server}, {membercount}, {datum}, {zeit}); {user} usw. bleiben stehen.
        guild = getattr(channel, "guild", None)

        def _txt(value, limit: int) -> str:
            text = render(str(value or "").strip(), guild=guild)
            return text[:limit]

        # Embed erstellen
        embed = discord.Embed()

        title = _txt(data.get("title", ""), 256)
        if title:
            embed.title = title

        description = _txt(data.get("description", ""), 4096)
        if description:
            embed.description = description

        color_hex = data.get("color", "#B84DFF")  # Standard = C3-Violett
        embed.color = _hex_to_color(color_hex)

        thumbnail = data.get("thumbnail_url", "").strip()
        if thumbnail:
            embed.set_thumbnail(url=thumbnail)

        image = data.get("image_url", "").strip()
        if image:
            embed.set_image(url=image)

        footer = _txt(data.get("footer_text", ""), 2048)
        if footer:
            embed.set_footer(text=footer)

        author = _txt(data.get("author_name", ""), 256)
        if author:
            embed.set_author(name=author)

        # Felder hinzufuegen
        for field in data.get("fields", []):
            fname = _txt(field.get("name", ""), 256)
            fvalue = _txt(field.get("value", ""), 1024)
            if fname and fvalue:
                embed.add_field(
                    name=fname,
                    value=fvalue,
                    inline=field.get("inline", True),
                )

        # Embed senden
        try:
            await channel.send(embed=embed)
            logger.info(f"Embed gesendet in Kanal {channel_id} ('{title}')")
        except discord.Forbidden:
            logger.error(f"Keine Berechtigung zum Senden in Kanal {channel_id}")
        except discord.HTTPException as e:
            logger.error(f"HTTP-Fehler beim Senden des Embeds: {e}")

        # Queue-Datei nach Verarbeitung entfernen
        filepath.unlink(missing_ok=True)


async def setup(bot: commands.Bot) -> None:
    """Registriert den EmbedSenderCog beim Bot."""
    await bot.add_cog(EmbedSenderCog(bot))
