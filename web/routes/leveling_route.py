"""
Level-Seite (D5) — Web-Leaderboard.

Zeigt die Top-Spieler der Haupt-Guild (XP/Level/Nachrichten/Voice) aus der
guild-scoped `leveling`-Tabelle (Migration v6). Read-only, Dashboard-Auth.

Hinweis: Der Web-Prozess kann Discord-Anzeigenamen nicht ohne Bot/API
aufloesen -> es wird die User-ID angezeigt (Namens-Anreicherung = spaeter,
z.B. via Username-Spalte in leveling oder Discord-API-Cache).
"""
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from modules.database.db_manager import get_read_db
from modules.guild_context import get_primary_guild_id
from utils.logger import get_logger
from web.auth import require_auth

logger = get_logger("web.routes.leveling")

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter(tags=["Leveling"])

LEADERBOARD_LIMIT = 100


def _format_voice(minutes: int) -> str:
    """Voice-Minuten in lesbares Format (z.B. '3h 20m')."""
    minutes = int(minutes or 0)
    if minutes < 60:
        return f"{minutes}m"
    h, m = divmod(minutes, 60)
    return f"{h}h {m}m"


async def _load_leaderboard(guild_id: int) -> list[dict]:
    """Top-Spieler der Guild aus SQLite (XP absteigend)."""
    try:
        db = await get_read_db()
        cursor = await db.execute(
            "SELECT user_id, xp, level, messages, voice_minutes "
            "FROM leveling WHERE guild_id = ? "
            "ORDER BY xp DESC LIMIT ?",
            (str(guild_id), LEADERBOARD_LIMIT),
        )
        rows = await cursor.fetchall()
    except Exception as e:
        logger.error(f"Leaderboard-Query fehlgeschlagen: {e}")
        return []

    entries: list[dict] = []
    for idx, row in enumerate(rows, start=1):
        entries.append({
            "rank": idx,
            "user_id": str(row[0]),
            "xp": row[1] or 0,
            "level": row[2] or 0,
            "messages": row[3] or 0,
            "voice": _format_voice(row[4] or 0),
        })
    return entries


@router.get("/leveling", response_class=HTMLResponse)
async def leveling_page(request: Request, current_user: dict = Depends(require_auth)):
    """Web-Leaderboard der Haupt-Guild anzeigen."""
    guild_id = get_primary_guild_id()
    entries: list[dict] = []
    if guild_id is not None:
        entries = await _load_leaderboard(guild_id)

    return templates.TemplateResponse("leveling.html", {
        "request": request,
        "user": current_user,
        "entries": entries,
        "guild_set": guild_id is not None,
        "total": len(entries),
    })
