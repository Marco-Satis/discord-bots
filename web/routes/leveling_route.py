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
from modules.guild_context import GuildConfig, get_primary_guild_id
from utils.logger import get_logger
from web.auth import require_auth

logger = get_logger("web.routes.leveling")

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter(tags=["Leveling"])

LEADERBOARD_LIMIT = 100


async def _load_levelup_config(guild_id: int) -> dict:
    """
    Level-Up-Benachrichtigungs- + Leaderboard-Config der Guild aus `guild_config`.

    Liefert announce_channel (str|"" fuer Template), levelup_ping (bool),
    leaderboard_card (bool) + leaderboard_bg (str|"" — Anzeige, Upload via Slash).
    """
    default = {
        "announce_channel": "", "levelup_ping": True,
        "leaderboard_card": False, "leaderboard_bg": "",
    }
    try:
        announce = await GuildConfig.get(guild_id, "leveling.announce_channel", None)
        ping = await GuildConfig.get(guild_id, "leveling.levelup_ping", True)
        lb_card = await GuildConfig.get(
            guild_id, "leveling.leaderboard_card_enabled", False
        )
        lb_bg = await GuildConfig.get(guild_id, "leveling.leaderboard_bg", None)
    except Exception as e:
        logger.error(f"Level-Up-Config laden fehlgeschlagen: {e}")
        return default
    return {
        "announce_channel": str(announce) if announce else "",
        "levelup_ping": bool(ping),
        "leaderboard_card": bool(lb_card),
        "leaderboard_bg": str(lb_bg) if lb_bg else "",
    }


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
    """Web-Leaderboard + Level-Up-Benachrichtigungs-Config der Haupt-Guild."""
    guild_id = get_primary_guild_id()
    entries: list[dict] = []
    cfg = {"announce_channel": "", "levelup_ping": True}
    if guild_id is not None:
        entries = await _load_leaderboard(guild_id)
        cfg = await _load_levelup_config(guild_id)

    return templates.TemplateResponse("leveling.html", {
        "request": request,
        "user": current_user,
        "entries": entries,
        "guild_set": guild_id is not None,
        "total": len(entries),
        "cfg": cfg,
        "success": "",
        "error": "",
    })


@router.post("/leveling/config", response_class=HTMLResponse)
async def leveling_config_save(
    request: Request, current_user: dict = Depends(require_auth)
):
    """
    Level-Up-Benachrichtigung speichern: Announce-Channel + Member-Ping.

    Schreibt nach `guild_config` (Keys `leveling.announce_channel` /
    `leveling.levelup_ping`). Der Bot liest sie ueber den 15s-TTL-Cache —
    Aenderung greift spaetestens nach TTL ohne Bot-Neustart.
    """
    guild_id = get_primary_guild_id()
    success = ""
    error = ""

    if guild_id is None:
        error = "Keine Haupt-Guild gesetzt (ENV GUILD_ID)."
        cfg = {"announce_channel": "", "levelup_ping": True}
    else:
        try:
            form = await request.form()
            raw_channel = str(form.get("announce_channel", "")).strip()
            ping = "levelup_ping" in form
            lb_card = "leaderboard_card" in form

            # Channel-ID validieren: leer = entfernen, sonst nur Ziffern (Snowflake)
            channel_val: int | None = None
            if raw_channel:
                if raw_channel.isdigit():
                    channel_val = int(raw_channel)
                else:
                    raise ValueError("Channel-ID muss eine reine Zahl sein.")

            await GuildConfig.set(
                guild_id, "leveling.announce_channel", channel_val,
                updated_by="dashboard",
            )
            await GuildConfig.set(
                guild_id, "leveling.levelup_ping", ping, updated_by="dashboard",
            )
            await GuildConfig.set(
                guild_id, "leveling.leaderboard_card_enabled", lb_card,
                updated_by="dashboard",
            )
            logger.info(
                f"Level-Up-Config gespeichert von "
                f"{current_user.get('username', 'Unbekannt')}: "
                f"channel={channel_val} ping={ping} lb_card={lb_card}"
            )
            success = "Level-Up-Benachrichtigung gespeichert."
        except ValueError as e:
            error = f"Ungueltige Eingabe: {e}"
        except Exception as e:
            logger.error(f"Level-Up-Config speichern fehlgeschlagen: {e}")
            error = "Fehler beim Speichern. Details im Server-Log."

        cfg = await _load_levelup_config(guild_id)

    # Partial zurueck (HTMX-Swap des Config-Blocks) — CSRF-Header kommt via
    # body[hx-headers] aus base_v5.html, daher HTMX statt nativem Form-Post.
    return templates.TemplateResponse("partials/leveling_config.html", {
        "request": request,
        "guild_set": guild_id is not None,
        "cfg": cfg,
        "success": success,
        "error": error,
    })
