"""Helper fuer typsichere Channel-Lookups in Discord-Cogs.

Hintergrund: `bot.get_channel(channel_id)` gibt `Optional[GuildChannel]` zurueck —
eine Union aus TextChannel, VoiceChannel, CategoryChannel, ForumChannel, Thread,
ggf. None. Aufrufer die direkt `.send(...)` aufrufen crashen mit `AttributeError`
wenn die Config einen falschen Channel-Typ liefert (z.B. eine Category-ID statt
einer Text-Channel-ID).

Dieser Helper kapselt den isinstance-Check zentral. Bei falschem Typ wird
None zurueckgegeben + ein Error-Log mit klarer Diagnose, sodass die aufrufende
Funktion gracefully degradiert statt zu crashen.

Audit-Lesson 2026-05-17 (review-agent Pattern 1, 40+ Call-Sites, 226 mypy
union-attr-Fehler).
"""

from __future__ import annotations

from typing import Optional, Union

import discord
from discord.ext import commands

from utils.logger import get_logger

logger = get_logger(__name__)


def get_text_channel(
    bot: Union[commands.Bot, discord.Client],
    channel_id: Optional[int],
    *,
    context: str = "",
) -> Optional[discord.TextChannel]:
    """Holt einen TextChannel typsicher, mit isinstance-Guard.

    Args:
        bot: Discord-Bot- oder Client-Instanz.
        channel_id: Channel-ID aus Config oder Datenbank. None erlaubt
            (gibt direkt None zurueck ohne Log).
        context: Optionaler Kontext-String fuer Log-Messages, z.B. Cog-Name
            oder Config-Feld. Hilft Marco im Audit zu finden welche Stelle
            den falschen Channel-Typ konfiguriert hat.

    Returns:
        Den TextChannel falls ID gueltig + Channel existiert + ist TextChannel.
        None in allen anderen Faellen (Channel nicht gefunden, falscher Typ,
        oder ID war None).
    """
    if channel_id is None:
        return None

    channel = bot.get_channel(channel_id)
    if channel is None:
        logger.warning(
            f"Channel {channel_id} nicht gefunden"
            + (f" ({context})" if context else "")
        )
        return None

    if not isinstance(channel, discord.TextChannel):
        actual_type = type(channel).__name__
        logger.error(
            f"Channel {channel_id} ist {actual_type}, erwartet TextChannel"
            + (f" ({context})" if context else "")
            + " — pruefe Config-Eintrag"
        )
        return None

    return channel


def get_voice_channel(
    bot: Union[commands.Bot, discord.Client],
    channel_id: Optional[int],
    *,
    context: str = "",
) -> Optional[discord.VoiceChannel]:
    """Analog zu get_text_channel, aber fuer VoiceChannel.

    Use-Case: Voice-Stats-Cog, Temp-Voice, Notify-Voice.
    """
    if channel_id is None:
        return None

    channel = bot.get_channel(channel_id)
    if channel is None:
        logger.warning(
            f"VoiceChannel {channel_id} nicht gefunden"
            + (f" ({context})" if context else "")
        )
        return None

    if not isinstance(channel, discord.VoiceChannel):
        actual_type = type(channel).__name__
        logger.error(
            f"Channel {channel_id} ist {actual_type}, erwartet VoiceChannel"
            + (f" ({context})" if context else "")
        )
        return None

    return channel


def get_messageable_channel(
    bot: Union[commands.Bot, discord.Client],
    channel_id: Optional[int],
    *,
    context: str = "",
) -> Optional[Union[discord.TextChannel, discord.Thread, discord.VoiceChannel]]:
    """Lockerer Check: alle Channel-Typen die `.send(...)` unterstuetzen.

    Erlaubt TextChannel, Thread, VoiceChannel (seit discord.py 2.x kann auch
    VoiceChannel Text-Messages empfangen). Schliesst CategoryChannel und
    ForumChannel aus (haben kein `.send()` direkt).

    Nuetzlich bei Cogs die in Threads UND TextChannels posten koennen
    (z.B. Welcome-Cog, Audit-Log-Cog).
    """
    if channel_id is None:
        return None

    channel = bot.get_channel(channel_id)
    if channel is None:
        logger.warning(
            f"Channel {channel_id} nicht gefunden"
            + (f" ({context})" if context else "")
        )
        return None

    if not isinstance(channel, (discord.TextChannel, discord.Thread, discord.VoiceChannel)):
        actual_type = type(channel).__name__
        logger.error(
            f"Channel {channel_id} ist {actual_type}, erwartet "
            f"TextChannel/Thread/VoiceChannel"
            + (f" ({context})" if context else "")
        )
        return None

    return channel
