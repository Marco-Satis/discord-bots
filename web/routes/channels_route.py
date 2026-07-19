"""
Route: Guild-Channel-Liste fuer die Dashboard-Dropdowns.

`GET /api/guild/channels[?kind=text|voice|category]` liefert die vom recon-bot
gesnapshotteten Channels (id + name + type) — damit Settings-Formulare Channel-
NAMEN zur Auswahl anbieten statt roher IDs. Auth-gated (require_auth).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from web.auth import require_auth
from web.channel_bridge import get_channels

router = APIRouter()


@router.get("/api/guild/channels", dependencies=[Depends(require_auth)])
async def guild_channels(
    kind: Optional[str] = Query(None, pattern="^(text|voice|category)$"),
):
    """Gefilterte Channel-Liste (JSON) fuer die Channel-Select-Komponente."""
    return get_channels(kind)
