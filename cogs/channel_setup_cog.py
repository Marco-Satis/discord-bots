"""
Channel-Setup Cog (Phase setup-topics).

`/setup_topics` setzt Text-Channel-Topics automatisch per API. Standard-Topics
sind aus `docs/DISCORD_KANAL_BESCHREIBUNGEN.md` abgeleitet (DEFAULT_TOPICS);
optional ueberschreibbar via `data/channel_topics.json` ({channel_name: topic}).

Matching case-insensitive ueber den Channel-Namen. Voice-Channels haben keine
Topics -> nur Text-Channels. Default dry-run (Vorschau) gegen versehentliche
Massen-Edits.
"""
from __future__ import annotations

import json
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.config import DATA_DIR
from utils.logger import get_logger
from utils.permissions import admin_only

logger = get_logger("cogs.channel_setup")

# Standard-Topics (Text-Channels) — aus docs/DISCORD_KANAL_BESCHREIBUNGEN.md.
# Keys sind Channel-Namen (lowercase). Voice/Privat-Pipeline-Channels ausgelassen.
DEFAULT_TOPICS: dict[str, str] = {
    "live-server-status": (
        "📊 Live-Status aller Gameserver — Online/Offline, Spielerzahl, "
        "Performance. Auto-aktualisiert, nur lesen."
    ),
    "willkommen": (
        "👋 Automatische Begrüßung neuer Mitglieder. Schau ins #regelwerk und "
        "hol dir deine Rollen."
    ),
    "regelwerk": (
        "📜 Serverregeln + Rollen per Reaktion: klick die Emojis um Spiel-/"
        "Benachrichtigungs-Rollen zu bekommen."
    ),
    "news": (
        "📰 Server-Ankündigungen, Updates & Events. Benachrichtigungen über die "
        "News-Rolle (#regelwerk)."
    ),
    "chat": (
        "💬 Haupt-Chat. Hier sammelst du XP & Level beim Schreiben — Fortschritt "
        "mit /rank, Rangliste /leaderboard."
    ),
    "bot-spam": "🤖 Spam-Zone für Bot-Ausgaben & längere Command-Tests.",
    "bilder-chat": "🖼️ Nur Bilder & Screenshots. Quatsch dazu bitte in #chat.",
    "memes": "😂 Memes rein, lachen raus.",
    "bot-commands": (
        "⌨️ Slash-Commands des Bots: /help, /rank, /leaderboard, /accounts, "
        "Server-Infos /mcstats & /world."
    ),
    "factorysatis": (
        "🏭 Alles rund um den Satisfactory-Server: Bauten, Logistik, Updates, "
        "Mitspieler."
    ),
    "bmc-chat-bridge": (
        "🌉 2-Wege-Chat-Bridge zum Minecraft BMC5-Server: was du hier schreibst "
        "landet in-game — und umgekehrt."
    ),
    "euer-setup": "🖥️ Zeig dein Gaming-Setup: PC, Peripherie, Zimmer.",
    "eure-clips": "🎬 Deine besten Clips & Highlights.",
    "spielersuche": (
        "🔎 Mitspieler suchen (LFG). Schreib welches Spiel, Uhrzeit & wie viele — "
        "dann ab in einen Voice (Join2Create)."
    ),
    "lieblings-games": "🕹️ Empfehlungen & Diskussion über eure Lieblingsspiele.",
    "logs": (
        "🛠️ Bot- & Moderations-Logs (Joins/Leaves, Mod-Aktionen, Befehle). "
        "Team-intern."
    ),
    "logs-satis-mc": (
        "🛠️ Server-Logs Satisfactory & Minecraft (Updates, Crashes, Backups, "
        "Auto-Restart)."
    ),
    "teamchat": "💼 Interne Team-Absprachen.",
}

TOPICS_OVERRIDE_FILE = DATA_DIR / "channel_topics.json"
# Discord-Topic-Limit
MAX_TOPIC_LEN = 1024


def load_topic_mapping() -> dict[str, str]:
    """DEFAULT_TOPICS, optional ueberschrieben durch data/channel_topics.json."""
    mapping = dict(DEFAULT_TOPICS)
    try:
        if TOPICS_OVERRIDE_FILE.exists():
            with open(TOPICS_OVERRIDE_FILE, "r", encoding="utf-8") as f:
                override = json.load(f)
            if isinstance(override, dict):
                for key, value in override.items():
                    if isinstance(value, str):
                        mapping[key.lower()] = value
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"channel_topics.json nicht ladbar: {e}")
    return {k.lower(): v[:MAX_TOPIC_LEN] for k, v in mapping.items()}


def resolve_topic(channel_name: str, mapping: dict[str, str]) -> Optional[str]:
    """Topic fuer einen Channel-Namen (case-insensitive) oder None."""
    return mapping.get(channel_name.lower().strip())


class ChannelSetupCog(commands.Cog):
    """Automatisches Setzen von Channel-Topics."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="setup_topics",
        description="Setzt Text-Channel-Topics automatisch (Standard: nur Vorschau)",
    )
    @app_commands.describe(
        anwenden="True = wirklich setzen, False = nur Vorschau (Standard)",
    )
    @admin_only()
    async def setup_topics(
        self,
        interaction: discord.Interaction,
        anwenden: bool = False,
    ) -> None:
        """Topics aus der Mapping-Tabelle auf passende Text-Channels anwenden."""
        await interaction.response.defer(ephemeral=True)
        if interaction.guild is None:
            await interaction.followup.send("Nur auf einem Server.", ephemeral=True)
            return

        mapping = load_topic_mapping()
        planned: list[tuple[discord.TextChannel, str]] = []
        for channel in interaction.guild.text_channels:
            topic = resolve_topic(channel.name, mapping)
            if topic is None:
                continue
            if (channel.topic or "") == topic:
                continue  # schon korrekt
            planned.append((channel, topic))

        if not planned:
            await interaction.followup.send(
                "Keine Channels zu aktualisieren (alles passt oder kein Match).",
                ephemeral=True,
            )
            return

        if not anwenden:
            preview = "\n".join(f"• #{ch.name}" for ch, _ in planned[:25])
            extra = f"\n… +{len(planned) - 25} weitere" if len(planned) > 25 else ""
            await interaction.followup.send(
                f"**Vorschau** — {len(planned)} Channel(s) wuerden ein Topic "
                f"bekommen:\n{preview}{extra}\n\nMit `anwenden:True` ausfuehren.",
                ephemeral=True,
            )
            return

        ok, failed = 0, 0
        for channel, topic in planned:
            try:
                await channel.edit(topic=topic, reason="setup_topics")
                ok += 1
            except (discord.Forbidden, discord.HTTPException) as e:
                failed += 1
                logger.warning(f"Topic setzen fuer #{channel.name} fehlgeschlagen: {e}")

        await interaction.followup.send(
            f"Topics gesetzt: **{ok}**" + (f", fehlgeschlagen: {failed}" if failed else ""),
            ephemeral=True,
        )

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
        logger.error(f"Command-Fehler: {error}", exc_info=True)


async def setup(bot: commands.Bot) -> None:
    """Registriert den Channel-Setup-Cog beim Bot."""
    await bot.add_cog(ChannelSetupCog(bot))
