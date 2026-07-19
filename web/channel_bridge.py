"""
Dashboard-Seite der Channel-Snapshot-Bruecke.

Liest den vom recon-bot geschriebenen `data/guild_channels.json`
(`modules.channel_snapshot`) und liefert gefilterte Channel-Listen fuer die
Dashboard-Dropdowns (Namen statt roher IDs). Reine Stdlib-Kette — kein Bot-Token,
kein Discord-Import im Web-Prozess.
"""
from __future__ import annotations

from typing import Any, Optional

from modules.channel_snapshot import read_snapshot

# Discord-ChannelType-Namen -> UI-Gruppen
_TEXTLIKE = {"text", "news", "announcement", "forum", "public_thread", "private_thread"}
_VOICELIKE = {"voice", "stage_voice"}


def get_channels(kind: Optional[str] = None) -> dict[str, Any]:
    """
    Channel-Snapshot lesen + optional nach Gruppe filtern.

    kind: None (alle) | 'text' | 'voice' | 'category'. Unbekannte Werte -> alle.
    """
    snap = read_snapshot()
    channels = snap.get("channels", []) or []

    if kind == "text":
        channels = [c for c in channels if c.get("type") in _TEXTLIKE]
    elif kind == "voice":
        channels = [c for c in channels if c.get("type") in _VOICELIKE]
    elif kind == "category":
        channels = [c for c in channels if c.get("type") == "category"]

    return {
        "guild_id": snap.get("guild_id", ""),
        "guild_name": snap.get("guild_name", ""),
        "updated_at": snap.get("updated_at"),
        "count": len(channels),
        "channels": channels,
    }
