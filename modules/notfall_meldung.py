"""
Notfall-Erwähnung für Marshal (Vorbild DraftBot, 21.10.2026).

Fehlt Marshal ein Recht (discord.Forbidden) oder stirbt eine Hintergrund-Schleife,
erscheint im Moderations-Log eine Meldung, die den Owner erwähnt — bisher landete
so etwas nur im Journal, das niemand liest.

Gegen Pingflut: dieselbe Meldung (Schlüssel) höchstens einmal je SPERRE_SEKUNDEN.
Erwähnt wird ausschließlich der Owner (AllowedMentions), nie @everyone oder Rollen.
"""
from __future__ import annotations

import time
from typing import Optional

import discord

from utils.config import get_env
from utils.embeds import error_embed
from utils.logger import get_logger

logger = get_logger("notfall_meldung")

SPERRE_SEKUNDEN = 3600
_zuletzt: dict[str, float] = {}


def owner_id() -> Optional[int]:
    """Owner aus OWNER_ID (Fallback BOT_OWNER_ID); None wenn nicht gesetzt."""
    for key in ("OWNER_ID", "BOT_OWNER_ID"):
        wert = str(get_env(key, "") or "").strip()
        if wert.isdigit():
            return int(wert)
    return None


def darf_melden(schluessel: str, jetzt: Optional[float] = None) -> bool:
    """Rate-Limit: True und merken, wenn der Schlüssel länger als SPERRE_SEKUNDEN still war."""
    jetzt = time.time() if jetzt is None else jetzt
    letzte = _zuletzt.get(schluessel)
    if letzte is not None and jetzt - letzte < SPERRE_SEKUNDEN:
        return False
    _zuletzt[schluessel] = jetzt
    return True


async def melden(bot: discord.Client, titel: str, text: str, schluessel: Optional[str] = None) -> bool:
    """Notfall ins Moderations-Log (Fallback Server-Log) mit Owner-Erwähnung. Nie werfend."""
    if not darf_melden(schluessel or titel):
        return False
    try:
        audit = getattr(bot, "audit_logger", None)
        kanal = None
        if audit is not None:
            kanal = (await audit._get_channel(bot, "mod_log_channel")
                     or await audit._get_channel(bot, "server_log_channel"))
        if kanal is None:
            logger.warning(f"Notfall ohne Log-Kanal: {titel}")
            return False
        oid = owner_id()
        embed = error_embed(title=f"⚠️ {titel}"[:256], description=text[:3900], timestamp=False)
        await kanal.send(
            content=f"<@{oid}>" if oid else None,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(
                everyone=False, roles=False, users=[discord.Object(oid)] if oid else False
            ),
        )
        return True
    except Exception as e:  # noqa: BLE001 — eine Notfall-Meldung darf selbst keinen Notfall auslösen
        logger.warning(f"Notfall-Meldung fehlgeschlagen ({titel}): {e}")
        return False
