"""
Ticket-Verlauf im Dashboard — Liste und Transcript je Ticket.

Bisher landete ein geschlossenes Ticket nur als ``ticket-<n>-transcript.txt`` im
Log-Kanal. Diese Seite zeigt dieselben Daten lesbar im Browser:

  GET /tickets              Liste (neueste zuerst, Filter offen/geschlossen)
  GET /tickets/{ticket_id}  Verlauf im Chat-Stil

Datenquelle: Tabellen ``tickets`` und ``ticket_messages`` (rein lesend ueber
``get_read_db``). Namen und Avatare kommen aus ``member_cache``; wer dort fehlt,
erscheint als „Unbekanntes Mitglied" — rohe Discord-IDs zeigt die Seite nie.

Zugriff: Tickets enthalten private Gespraeche zwischen Mitglied und Team. Der
Member-Default (``view`` auf alle nicht-sensiblen Bereiche, modules/rbac.py)
waere hier zu weit, deshalb verlangt die Seite ``marshal_bot``/``edit`` — das
haben nur der Owner und Rollen, denen das Team-Recht ausdruecklich gegeben wurde.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from modules.database.db_manager import get_read_db
from modules.guild_context import get_primary_guild_id
from modules.member_cache import get_members
from utils.logger import get_logger
from web.auth import require_perm
from web.templates_setup import erstelle_templates

try:  # Python 3.9+ — tzdata liegt auf dem Server (Ubuntu)
    from zoneinfo import ZoneInfo
    _TZ: Optional[Any] = ZoneInfo("Europe/Berlin")
except Exception:  # noqa: BLE001 — ohne Zeitzone bleibt die Serverzeit stehen
    _TZ = None

logger = get_logger("web.routes.tickets")

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
templates = erstelle_templates(TEMPLATE_DIR)

router = APIRouter(tags=["Tickets"])

# Recht fuer beide Seiten (Begruendung im Modul-Docstring).
TICKET_RESOURCE = "marshal_bot"
TICKET_ACTION = "edit"

LIST_LIMIT = 300

# Filter-Wert in der URL -> Status in der DB (None = alle)
STATUS_FILTER: dict[str, Optional[str]] = {
    "alle": None,
    "offen": "open",
    "geschlossen": "closed",
}

# Gespiegelt aus cogs/tickets_cog.py TICKET_ARTEN (dort haengt discord.py dran,
# das der Web-Prozess nicht importieren soll).
ART_LABELS: dict[str, str] = {
    "allgemein": "Frage / Anliegen",
    "bug": "Fehler melden",
}

UNKNOWN_MEMBER = "Unbekanntes Mitglied"
SYSTEM_AUTHOR = "System"

# Der Cog haengt Anhaenge als „[Anhaenge: url1, url2]" an den Text an.
_ATTACH_RE = re.compile(r"\[Anh(?:ae|ä)nge: ([^\]]*)\]\s*$")
_URL_RE = re.compile(r"https?://[^\s<>\"']+")


# ===========================================================================
# Hilfsfunktionen (rein, ohne DB — direkt testbar)
# ===========================================================================

def format_time(raw: Any) -> str:
    """
    DB-Zeitstempel als „21.10.2026, 14:05" in Europe/Berlin.

    Der Bot schreibt ``datetime.now().isoformat()`` (naiv, Serverzeit =
    Europe/Berlin); SQLite-Defaults (``CURRENT_TIMESTAMP``) sind UTC ohne Zone,
    mit Leerzeichen statt „T". Naive Werte werden als Serverzeit gelesen, Werte
    mit Zone umgerechnet.
    """
    if not raw:
        return ""
    text = str(raw).strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if dt.tzinfo is not None and _TZ is not None:
        dt = dt.astimezone(_TZ)
    return dt.strftime("%d.%m.%Y, %H:%M")


def _safe_url(url: str) -> Optional[str]:
    """Nur http(s)-Adressen werden zu Links — alles andere bleibt Text."""
    url = url.strip().rstrip(".,;)")
    if url.startswith(("https://", "http://")) and len(url) <= 2000:
        return url
    return None


def split_content(content: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """
    Nachrichtentext in Abschnitte (Text/Link) und Anhaenge zerlegen.

    Die Ausgabe wird im Template einzeln und mit Autoescape gerendert — es gibt
    kein ``|safe``. Links entstehen nur aus gespeicherten http(s)-Adressen.

    Returns:
        (segmente, anhaenge) — Segment: {"text": str, "href": str} (href leer =
        reiner Text); Anhang: {"name": str, "href": str}.
    """
    content = content or ""
    attachments: list[dict[str, str]] = []

    match = _ATTACH_RE.search(content)
    if match:
        for raw_url in match.group(1).split(","):
            href = _safe_url(raw_url)
            if href:
                name = href.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1] or "Anhang"
                attachments.append({"name": name, "href": href})
        content = content[: match.start()].rstrip()

    segments: list[dict[str, str]] = []
    pos = 0
    for m in _URL_RE.finditer(content):
        href = _safe_url(m.group(0))
        if not href:
            continue
        if m.start() > pos:
            segments.append({"text": content[pos:m.start()], "href": ""})
        segments.append({"text": href, "href": href})
        pos = m.start() + len(href)
    if pos < len(content):
        segments.append({"text": content[pos:], "href": ""})
    return segments, attachments


def _safe_avatar(url: Optional[str]) -> str:
    """Avatar nur von https-Adressen uebernehmen (CDN-URL aus dem Cache)."""
    if url and str(url).startswith("https://"):
        return str(url)
    return ""


def _person(user_id: Any, cache: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Anzeigename + Avatar zu einer User-ID — nie die ID selbst."""
    uid = str(user_id or "").strip()
    if uid in ("", "0"):
        return {"name": SYSTEM_AUTHOR, "avatar": "", "initial": "S", "system": "1"}
    entry = cache.get(uid) or {}
    name = (entry.get("display_name") or "").strip() or UNKNOWN_MEMBER
    return {
        "name": name,
        "avatar": _safe_avatar(entry.get("avatar_url")),
        "initial": name[:1].upper() if name != UNKNOWN_MEMBER else "?",
        "system": "",
    }


def _status_label(status: Optional[str]) -> tuple[str, bool]:
    """(Anzeige, ist_offen) fuer den DB-Status."""
    if (status or "open") == "open":
        return "Offen", True
    return "Geschlossen", False


async def _names(guild_id: Optional[int], user_ids: set[str]) -> dict[str, dict[str, Any]]:
    """member_cache-Batch; ohne Guild oder bei Fehler leer (Fallback-Name greift)."""
    ids = sorted(u for u in user_ids if u and u != "0")
    if guild_id is None or not ids:
        return {}
    return await get_members(guild_id, ids)


# ===========================================================================
# Daten laden (rein lesend)
# ===========================================================================

async def load_ticket_list(
    status_filter: str = "alle",
    guild_id: Optional[int] = None,
    limit: int = LIST_LIMIT,
) -> dict[str, Any]:
    """
    Ticket-Liste fuer /tickets.

    Args:
        status_filter: "alle" | "offen" | "geschlossen" (Unbekanntes = "alle")
        guild_id: Guild fuer die Namensaufloesung (None = keine Namen)
        limit: maximale Zeilenzahl

    Returns:
        {"tickets": [...], "counts": {"alle", "offen", "geschlossen"},
         "filter": str, "error": str}
    """
    if status_filter not in STATUS_FILTER:
        status_filter = "alle"
    db_status = STATUS_FILTER[status_filter]
    result: dict[str, Any] = {
        "tickets": [],
        "counts": {"alle": 0, "offen": 0, "geschlossen": 0},
        "filter": status_filter,
        "error": "",
    }

    try:
        conn = await get_read_db()
        cursor = await conn.execute(
            "SELECT COALESCE(status, 'open') AS st, COUNT(*) AS n FROM tickets GROUP BY st"
        )
        for row in await cursor.fetchall():
            n = int(row["n"])
            result["counts"]["alle"] += n
            if row["st"] == "open":
                result["counts"]["offen"] += n
            else:
                result["counts"]["geschlossen"] += n

        sql = (
            "SELECT t.id, t.user_id, t.subject, t.status, t.created_at, t.closed_at, "
            "t.art, (SELECT COUNT(*) FROM ticket_messages m WHERE m.ticket_id = t.id) "
            "AS msg_count FROM tickets t "
        )
        params: list[Any] = []
        if db_status == "open":
            sql += "WHERE COALESCE(t.status, 'open') = 'open' "
        elif db_status == "closed":
            sql += "WHERE COALESCE(t.status, 'open') <> 'open' "
        sql += "ORDER BY t.id DESC LIMIT ?"
        params.append(int(limit))
        cursor = await conn.execute(sql, tuple(params))
        rows = await cursor.fetchall()
    except Exception as e:  # noqa: BLE001 — Seite zeigt Hinweis statt 500
        logger.error(f"Ticket-Liste laden fehlgeschlagen: {e}")
        result["error"] = "Die Tickets konnten nicht geladen werden. Details im Server-Log."
        return result

    cache = await _names(guild_id, {str(r["user_id"] or "") for r in rows})
    for r in rows:
        label, is_open = _status_label(r["status"])
        result["tickets"].append({
            "id": int(r["id"]),
            "creator": _person(r["user_id"], cache),
            "subject": (r["subject"] or "").strip(),
            "art": ART_LABELS.get(r["art"] or "", ART_LABELS["allgemein"]),
            "status": label,
            "is_open": is_open,
            "created": format_time(r["created_at"]),
            "closed": format_time(r["closed_at"]),
            "msg_count": int(r["msg_count"] or 0),
        })
    return result


async def load_ticket_detail(
    ticket_id: int,
    guild_id: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    """
    Ein Ticket samt Verlauf fuer /tickets/{id}.

    Returns:
        Ticket-Dict mit "messages" oder None, wenn es die Nummer nicht gibt.
    """
    conn = await get_read_db()
    cursor = await conn.execute(
        "SELECT id, user_id, subject, status, created_at, closed_at, closed_by, art "
        "FROM tickets WHERE id = ?",
        (int(ticket_id),),
    )
    t = await cursor.fetchone()
    if t is None:
        return None

    cursor = await conn.execute(
        "SELECT id, user_id, content, timestamp FROM ticket_messages "
        "WHERE ticket_id = ? ORDER BY id ASC",
        (int(ticket_id),),
    )
    msg_rows = await cursor.fetchall()

    ids = {str(t["user_id"] or ""), str(t["closed_by"] or "")}
    ids |= {str(m["user_id"] or "") for m in msg_rows}
    cache = await _names(guild_id, ids)

    creator_id = str(t["user_id"] or "")
    messages: list[dict[str, Any]] = []
    for m in msg_rows:
        segments, attachments = split_content(m["content"] or "")
        uid = str(m["user_id"] or "")
        messages.append({
            "author": _person(uid, cache),
            "is_creator": uid == creator_id and uid not in ("", "0"),
            "time": format_time(m["timestamp"]),
            "segments": segments,
            "attachments": attachments,
        })

    label, is_open = _status_label(t["status"])
    return {
        "id": int(t["id"]),
        "creator": _person(t["user_id"], cache),
        "closed_by": _person(t["closed_by"], cache) if t["closed_by"] else None,
        "subject": (t["subject"] or "").strip(),
        "art": ART_LABELS.get(t["art"] or "", ART_LABELS["allgemein"]),
        "status": label,
        "is_open": is_open,
        "created": format_time(t["created_at"]),
        "closed": format_time(t["closed_at"]),
        "msg_count": len(messages),
        "messages": messages,
    }


# ===========================================================================
# Routen
# ===========================================================================

@router.get("/tickets", response_class=HTMLResponse)
async def tickets_page(
    request: Request,
    status: str = "alle",
    current_user: dict = Depends(require_perm(TICKET_RESOURCE, TICKET_ACTION)),
):
    """Ticket-Liste (neueste zuerst) mit Filter offen/geschlossen."""
    data = await load_ticket_list(status, guild_id=get_primary_guild_id())
    return templates.TemplateResponse(request, "tickets.html", {
        "request": request,
        "user": current_user,
        "tickets": data["tickets"],
        "counts": data["counts"],
        "f_status": data["filter"],
        "error": data["error"],
        "limit": LIST_LIMIT,
    })


@router.get("/tickets/{ticket_id}", response_class=HTMLResponse)
async def ticket_detail_page(
    request: Request,
    ticket_id: int,
    current_user: dict = Depends(require_perm(TICKET_RESOURCE, TICKET_ACTION)),
):
    """Verlauf eines Tickets im Chat-Stil."""
    if ticket_id < 1 or ticket_id > 2**63 - 1:  # ausserhalb SQLite-INTEGER
        raise HTTPException(status_code=404, detail="Ticket nicht gefunden")
    try:
        ticket = await load_ticket_detail(ticket_id, guild_id=get_primary_guild_id())
    except Exception as e:  # noqa: BLE001
        logger.error(f"Ticket #{ticket_id} laden fehlgeschlagen: {e}")
        raise HTTPException(status_code=500, detail="Ticket konnte nicht geladen werden")
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket nicht gefunden")
    return templates.TemplateResponse(request, "ticket_detail.html", {
        "request": request,
        "user": current_user,
        "ticket": ticket,
    })
