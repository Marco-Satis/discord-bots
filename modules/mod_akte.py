"""
Moderations-Akte — alles, was die Moderation zu einem Mitglied weiss.

Fuehrt drei Quellen zu einer Zeitleiste zusammen:
  - Verwarnungen      -> Tabelle `warns` (WarnManager, alle inkl. abgelaufener)
  - Moderations-Taten -> Tabelle `audit_log`, action `mod_*` (Kick, Bann,
                         Bann aufgehoben, Timeout, Timeout aufgehoben), geschrieben
                         von AuditLogger.log_mod_action. Das Ziel steht dort als
                         Text "<name> (<id>)" — gesucht wird ueber das Suffix.
  - Notizen           -> Tabelle `mod_notes` (Migration v15), optional mit
                         Beweisbild unter data/admin/evidence/<uuid>.<ext>

Hinweis Guild-Scope: `warns` und `audit_log` kennen keine guild_id (das System
laeuft auf einer Haupt-Guild). Nur die Notizen sind guild-scoped.

Beweisbilder werden heruntergeladen und lokal abgelegt, weil Discord-Anhang-
Links nach einiger Zeit ablaufen — eine gespeicherte URL waere spaeter tot.
"""
from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import aiohttp

from modules.database.db_manager import get_db, get_read_db
from modules.member_cache import get_members
from utils.config import ADMIN_DATA_DIR
from utils.logger import get_logger

logger = get_logger("mod_akte")

# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------

EVIDENCE_DIR: Path = ADMIN_DATA_DIR / "evidence"
ALLOWED_EXTENSIONS: frozenset[str] = frozenset({"png", "jpg", "jpeg", "gif", "webp"})
MAX_EVIDENCE_BYTES = 8 * 1024 * 1024
MAX_NOTE_LEN = 1000
MAX_REASON_LEN = 500

# Nur von uns erzeugte Namen: 32 Hex-Zeichen (uuid4.hex) + erlaubte Endung.
# Alles andere (Pfadtrenner, Punkte, Gross-/Sonderzeichen) faellt durch.
EVIDENCE_NAME_RE = re.compile(r"^[0-9a-f]{32}\.(?:png|jpe?g|gif|webp)$")

# SSRF-Schutz: Beweisbilder nur von Discords eigenem CDN laden.
ALLOWED_EVIDENCE_HOSTS: frozenset[str] = frozenset({
    "cdn.discordapp.com",
    "media.discordapp.net",
})

DISPLAY_TZ = ZoneInfo("Europe/Berlin")

# audit_log.action -> Anzeige-Text + Chip-Art (c3-chip--<art>)
ACTION_LABELS: dict[str, tuple[str, str]] = {
    "mod_kick": ("Kick", "check"),
    "mod_bann": ("Bann", "check"),
    "mod_ban": ("Bann", "check"),
    "mod_bann aufgehoben": ("Bann aufgehoben", "off"),
    "mod_unban": ("Bann aufgehoben", "off"),
    "mod_timeout": ("Timeout", "setup"),
    "mod_mute": ("Timeout", "setup"),
    "mod_timeout aufgehoben": ("Timeout aufgehoben", "off"),
    "mod_warn": ("Verwarnung (Log)", "setup"),
}

# Bereichsgrenzen fuer "action beginnt mit mod_" — nutzt idx_audit_action,
# anders als LIKE 'mod_%' (dort waere '_' zudem ein Platzhalter).
_MOD_ACTION_LO = "mod_"
_MOD_ACTION_HI = "mod`"  # '`' (0x60) folgt direkt auf '_' (0x5F)

_TARGET_ID_RE = re.compile(r"\((\d{5,25})\)\s*$")
_TARGET_NAME_RE = re.compile(r"^(.*?)\s*\(\d{5,25}\)\s*$")

# Magic Bytes -> kanonische Endung (Endung allein ist kein Beweis fuer den Inhalt)
_MAGIC: list[tuple[bytes, str]] = [
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
]


class EvidenceError(ValueError):
    """Beweisbild abgelehnt (Typ, Groesse, Quelle) — Text ist nutzerlesbar."""


# ---------------------------------------------------------------------------
# Hilfen: IDs, Zeit
# ---------------------------------------------------------------------------

def _uid(value: int | str) -> str:
    """Discord-ID als String, nur Ziffern (sonst ValueError)."""
    s = str(value).strip()
    if not s.isdigit() or len(s) > 25:
        raise ValueError(f"Ungueltige Discord-ID: {s!r}")
    return s


_LOCAL_AUTHOR_RE = re.compile(r"^local:[\w.\-]{1,64}$")


def _author(value: int | str) -> str:
    """
    Autor einer Notiz: Discord-ID oder 'local:<name>' (Passwort-Login im Dashboard).
    """
    s = str(value).strip()
    if _LOCAL_AUTHOR_RE.fullmatch(s):
        return s
    return _uid(s)


def _now_iso() -> str:
    """Aktuelle Zeit als ISO-8601 in UTC."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_ts(raw: Any, naive_is_utc: bool) -> Optional[datetime]:
    """
    Zeitstempel aus der DB in ein zeitzonen-bewusstes datetime wandeln.

    Die Quellen speichern unterschiedlich:
      - audit_log.timestamp: CURRENT_TIMESTAMP -> naiv, UTC
      - warns.created_at:    datetime.now().isoformat() -> naiv, Serverzeit
      - mod_notes:           ISO mit +00:00
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        dt = raw
    else:
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc) if naive_is_utc else dt.astimezone()
    return dt


def format_ts(dt: Optional[datetime]) -> str:
    """Anzeigeformat 21.10.2026 09:15 (deutsche Zeit)."""
    if dt is None:
        return "unbekannt"
    return dt.astimezone(DISPLAY_TZ).strftime("%d.%m.%Y %H:%M")


def _sort_key(dt: Optional[datetime]) -> float:
    return dt.timestamp() if dt else 0.0


# ---------------------------------------------------------------------------
# Beweisbilder
# ---------------------------------------------------------------------------

def is_valid_evidence_name(name: Any) -> bool:
    """True nur fuer von uns vergebene Dateinamen (<32 hex>.<ext>)."""
    return isinstance(name, str) and bool(EVIDENCE_NAME_RE.fullmatch(name))


def evidence_path(name: str) -> Optional[Path]:
    """
    Absoluter Pfad eines Beweisbildes oder None.

    Doppelt abgesichert: strikte Namens-Whitelist UND Pruefung, dass der
    aufgeloeste Pfad im Evidence-Ordner liegt (kein Traversal, kein Symlink
    nach draussen).
    """
    if not is_valid_evidence_name(name):
        return None
    base = EVIDENCE_DIR.resolve()
    path = (EVIDENCE_DIR / name).resolve()
    if path.parent != base:
        return None
    return path


def _sniff_ext(data: bytes) -> Optional[str]:
    """Bildtyp aus den ersten Bytes bestimmen (png/jpg/gif/webp) oder None."""
    for magic, ext in _MAGIC:
        if data.startswith(magic):
            return ext
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def store_evidence_bytes(data: bytes) -> str:
    """
    Bilddaten pruefen und unter einem neuen Namen ablegen (synchron).

    Returns:
        Dateiname (<uuid>.<ext>)

    Raises:
        EvidenceError: zu gross, leer oder kein erlaubtes Bildformat
    """
    if not data:
        raise EvidenceError("Die Datei ist leer.")
    if len(data) > MAX_EVIDENCE_BYTES:
        raise EvidenceError("Das Bild ist größer als 8 MB.")
    ext = _sniff_ext(data)
    if ext is None:
        raise EvidenceError("Nur Bilder sind erlaubt (PNG, JPG, GIF, WEBP).")
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.{ext}"
    path = EVIDENCE_DIR / name
    # 'xb': nie eine vorhandene Datei ueberschreiben
    with open(path, "xb") as fh:
        fh.write(data)
    try:
        path.chmod(0o640)
    except OSError as e:
        logger.debug(f"chmod Beweisbild fehlgeschlagen: {e}")
    return name


def check_evidence_meta(filename: str, size: Optional[int], url: str) -> None:
    """
    Vorpruefung eines Discord-Anhangs vor dem Download.

    Raises:
        EvidenceError: falsche Endung, zu gross oder fremder Host
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise EvidenceError("Nur Bilder sind erlaubt (PNG, JPG, GIF, WEBP).")
    if size is not None and size > MAX_EVIDENCE_BYTES:
        raise EvidenceError("Das Bild ist größer als 8 MB.")
    parts = urlsplit(url)
    if parts.scheme != "https" or (parts.hostname or "").lower() not in ALLOWED_EVIDENCE_HOSTS:
        raise EvidenceError("Der Anhang stammt nicht von Discord.")


async def download_evidence(
    url: str,
    filename: str,
    size: Optional[int] = None,
    session: Optional[aiohttp.ClientSession] = None,
) -> str:
    """
    Discord-Anhang herunterladen und lokal ablegen.

    Liest hoechstens MAX_EVIDENCE_BYTES + 1 Byte (die Groessenangabe des
    Servers allein reicht nicht), prueft den Inhalt per Magic Bytes und
    schreibt dann ueber `store_evidence_bytes`.

    Returns:
        Dateiname unter EVIDENCE_DIR

    Raises:
        EvidenceError: abgelehnt oder Download fehlgeschlagen
    """
    check_evidence_meta(filename, size, url)
    own_session = session is None
    sess = session or aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
    try:
        async with sess.get(url, allow_redirects=False) as resp:
            if resp.status != 200:
                raise EvidenceError("Der Anhang konnte nicht geladen werden.")
            buf = bytearray()
            async for chunk in resp.content.iter_chunked(64 * 1024):
                buf.extend(chunk)
                if len(buf) > MAX_EVIDENCE_BYTES:
                    raise EvidenceError("Das Bild ist größer als 8 MB.")
    except aiohttp.ClientError as e:
        logger.warning(f"Beweisbild-Download fehlgeschlagen: {e}")
        raise EvidenceError("Der Anhang konnte nicht geladen werden.") from e
    except asyncio.TimeoutError as e:
        raise EvidenceError("Der Anhang konnte nicht geladen werden (Zeitüberschreitung).") from e
    finally:
        if own_session:
            await sess.close()
    return await asyncio.to_thread(store_evidence_bytes, bytes(buf))


def delete_evidence_file(name: Optional[str]) -> bool:
    """Beweisbild loeschen (best effort). True wenn eine Datei entfernt wurde."""
    if not name:
        return False
    path = evidence_path(name)
    if path is None:
        logger.warning("Beweisbild-Name ungueltig, nichts geloescht")
        return False
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as e:
        logger.error(f"Beweisbild loeschen fehlgeschlagen: {e}")
        return False


# ---------------------------------------------------------------------------
# Notizen
# ---------------------------------------------------------------------------

def _clean_text(text: str, limit: int) -> str:
    """Whitespace trimmen, leere Eingabe ablehnen, Laenge begrenzen."""
    cleaned = (text or "").strip()
    if not cleaned:
        raise ValueError("Der Text darf nicht leer sein.")
    if len(cleaned) > limit:
        raise ValueError(f"Der Text ist länger als {limit} Zeichen.")
    return cleaned


async def add_note(
    guild_id: int | str,
    user_id: int | str,
    author_id: int | str,
    text: str,
    evidence_file: Optional[str] = None,
) -> int:
    """
    Notiz zur Akte eines Mitglieds hinzufuegen.

    Returns:
        ID der neuen Notiz

    Raises:
        ValueError: ungueltige ID, leerer/zu langer Text, ungueltiger Dateiname
    """
    gid, uid, aid = _uid(guild_id), _uid(user_id), _author(author_id)
    body = _clean_text(text, MAX_NOTE_LEN)
    if evidence_file is not None and not is_valid_evidence_name(evidence_file):
        raise ValueError("Ungültiger Dateiname für das Beweisbild.")
    conn = await get_db()
    cursor = await conn.execute(
        "INSERT INTO mod_notes (guild_id, user_id, author_id, text, evidence_file, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (gid, uid, aid, body, evidence_file, _now_iso()),
    )
    await conn.commit()
    return int(cursor.lastrowid)


def _note_row(row: Any) -> dict[str, Any]:
    created = _parse_ts(row[6], naive_is_utc=True)
    updated = _parse_ts(row[7], naive_is_utc=True)
    return {
        "id": int(row[0]),
        "guild_id": str(row[1]),
        "user_id": str(row[2]),
        "author_id": str(row[3]),
        "text": row[4] or "",
        "evidence_file": row[5],
        "created_at": row[6],
        "updated_at": row[7],
        "ts": created,
        "ts_display": format_ts(created),
        "updated_display": format_ts(updated) if updated else "",
    }


_NOTE_COLS = "id, guild_id, user_id, author_id, text, evidence_file, created_at, updated_at"


async def list_notes(guild_id: int | str, user_id: int | str) -> list[dict[str, Any]]:
    """Alle Notizen eines Mitglieds, neueste zuerst."""
    gid, uid = _uid(guild_id), _uid(user_id)
    conn = await get_read_db()
    cursor = await conn.execute(
        f"SELECT {_NOTE_COLS} FROM mod_notes "  # nosec B608 - feste Spaltenliste
        "WHERE guild_id = ? AND user_id = ? ORDER BY id DESC",
        (gid, uid),
    )
    return [_note_row(r) for r in await cursor.fetchall()]


async def get_note(note_id: int) -> Optional[dict[str, Any]]:
    """Eine Notiz per ID oder None."""
    conn = await get_read_db()
    cursor = await conn.execute(
        f"SELECT {_NOTE_COLS} FROM mod_notes WHERE id = ?",  # nosec B608 - feste Spaltenliste
        (int(note_id),),
    )
    row = await cursor.fetchone()
    return _note_row(row) if row else None


async def delete_note(
    note_id: int, guild_id: Optional[int | str] = None
) -> Optional[dict[str, Any]]:
    """
    Notiz loeschen (inkl. Beweisbild).

    Args:
        note_id: ID der Notiz
        guild_id: wenn gesetzt, nur loeschen wenn die Notiz zu dieser Guild gehoert

    Returns:
        Die geloeschte Notiz (fuer Audit/Antwort) oder None wenn nicht gefunden
    """
    conn = await get_db()
    cursor = await conn.execute(
        f"SELECT {_NOTE_COLS} FROM mod_notes WHERE id = ?",  # nosec B608 - feste Spaltenliste
        (int(note_id),),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    note = _note_row(row)
    if guild_id is not None and note["guild_id"] != _uid(guild_id):
        return None
    await conn.execute("DELETE FROM mod_notes WHERE id = ?", (note["id"],))
    await conn.commit()
    if note["evidence_file"]:
        await asyncio.to_thread(delete_evidence_file, note["evidence_file"])
    return note


# ---------------------------------------------------------------------------
# Verwarnungen
# ---------------------------------------------------------------------------

async def update_warn_reason(
    warn_id: int,
    new_reason: str,
    warn_manager: Any = None,
) -> Optional[dict[str, Any]]:
    """
    Grund einer Verwarnung aendern.

    Schreibt SQLite und — wenn der WarnManager des laufenden Bots uebergeben
    wird — dessen In-Memory-Cache, damit /warn list sofort den neuen Grund
    zeigt. Aus dem Dashboard-Prozess gibt es keinen Zugriff auf den Bot-Cache;
    dort greift die Aenderung im Bot erst nach dessen naechstem load_from_db.

    Returns:
        {"id", "user_id", "old_reason", "new_reason", "cache_updated"} oder None
        wenn es den Warn nicht gibt

    Raises:
        ValueError: leerer oder zu langer Grund
    """
    reason = _clean_text(new_reason, MAX_REASON_LEN)
    wid = int(warn_id)
    conn = await get_db()
    cursor = await conn.execute(
        "SELECT id, user_id, reason, created_at FROM warns WHERE id = ?", (wid,)
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    await conn.execute("UPDATE warns SET reason = ? WHERE id = ?", (reason, wid))
    await conn.commit()

    cache_updated = False
    if warn_manager is not None:
        try:
            cache_updated = bool(
                warn_manager.update_cached_reason(str(row[1]), wid, row[3], reason)
            )
        except Exception as e:  # noqa: BLE001 — Cache ist nachrangig, DB ist geschrieben
            logger.error(f"Warn-Cache-Update fehlgeschlagen (Warn {wid}): {e}")
    return {
        "id": wid,
        "user_id": str(row[1]),
        "old_reason": row[2] or "",
        "new_reason": reason,
        "cache_updated": cache_updated,
    }


async def list_warns(user_id: int | str) -> list[dict[str, Any]]:
    """Alle Verwarnungen eines Mitglieds (aktive und abgelaufene), neueste zuerst."""
    uid = _uid(user_id)
    conn = await get_read_db()
    cursor = await conn.execute(
        "SELECT id, moderator_id, reason, points, created_at, active "
        "FROM warns WHERE user_id = ? ORDER BY id DESC",
        (uid,),
    )
    out: list[dict[str, Any]] = []
    for r in await cursor.fetchall():
        ts = _parse_ts(r[4], naive_is_utc=False)
        out.append({
            "id": int(r[0]),
            "moderator": str(r[1] or ""),
            "reason": r[2] or "",
            "points": int(r[3] or 1),
            "created_at": r[4],
            "active": bool(r[5]) if r[5] is not None else True,
            "ts": ts,
            "ts_display": format_ts(ts),
        })
    return out


# ---------------------------------------------------------------------------
# Moderations-Taten (audit_log)
# ---------------------------------------------------------------------------

def target_id_from(target: Optional[str]) -> Optional[str]:
    """User-ID aus audit_log.target ("<name> (<id>)") oder None."""
    m = _TARGET_ID_RE.search(target or "")
    return m.group(1) if m else None


def target_name_from(target: Optional[str]) -> Optional[str]:
    """Name aus audit_log.target ("<name> (<id>)") oder None."""
    m = _TARGET_NAME_RE.match(target or "")
    name = m.group(1).strip() if m else ""
    return name or None


def action_label(action: str) -> tuple[str, str]:
    """(Anzeige-Text, Chip-Art) fuer eine audit_log-Aktion."""
    if action in ACTION_LABELS:
        return ACTION_LABELS[action]
    rest = action[4:] if action.startswith("mod_") else action
    return (rest.capitalize() or "Aktion", "setup")


async def list_mod_actions(user_id: int | str) -> list[dict[str, Any]]:
    """Moderations-Taten gegen ein Mitglied aus audit_log, neueste zuerst."""
    uid = _uid(user_id)
    conn = await get_read_db()
    cursor = await conn.execute(
        "SELECT id, timestamp, user_id, user_name, action, target, details "
        "FROM audit_log WHERE action >= ? AND action < ? AND target LIKE ? "
        "ORDER BY id DESC LIMIT 500",
        (_MOD_ACTION_LO, _MOD_ACTION_HI, f"%({uid})"),
    )
    out: list[dict[str, Any]] = []
    for r in await cursor.fetchall():
        if target_id_from(r[5]) != uid:  # LIKE-Treffer exakt nachpruefen
            continue
        ts = _parse_ts(r[1], naive_is_utc=True)
        label, art = action_label(str(r[4]))
        out.append({
            "id": int(r[0]),
            "action": str(r[4]),
            "label": label,
            "chip": art,
            "moderator_id": str(r[2]) if r[2] else None,
            "moderator_name": r[3] or None,
            "target_name": target_name_from(r[5]),
            "reason": r[6] or "",
            "ts": ts,
            "ts_display": format_ts(ts),
        })
    return out


# ---------------------------------------------------------------------------
# Namen
# ---------------------------------------------------------------------------

async def ensure_member_name(
    guild_id: int | str, user_id: int | str, display_name: Optional[str]
) -> None:
    """
    Namen in member_cache eintragen, falls dort noch keiner steht.

    member_cache wird sonst nur bei XP-Aktivitaet gepflegt; Verwarnte und
    Moderatoren aus dem Dashboard fehlen dort oft. Ueberschreibt nie einen
    vorhandenen Eintrag (der Bot kennt den echten Anzeigenamen besser).
    """
    if not display_name:
        return
    try:
        conn = await get_db()
        await conn.execute(
            "INSERT OR IGNORE INTO member_cache "
            "(guild_id, user_id, display_name, avatar_url, updated_at) "
            "VALUES (?, ?, ?, NULL, CURRENT_TIMESTAMP)",
            (_uid(guild_id), _uid(user_id), display_name[:100]),
        )
        await conn.commit()
    except Exception as e:  # noqa: BLE001 — Name ist Komfort, nie fatal
        logger.debug(f"ensure_member_name fehlgeschlagen: {e}")


def _name_for(cache: dict[str, dict[str, Any]], user_id: Optional[str],
              fallback: Optional[str] = None) -> str:
    """Anzeigename aus dem Cache, sonst Fallback, nie die rohe ID."""
    if user_id and user_id.startswith("local:"):
        return f"{user_id[6:]} (Dashboard)"
    if user_id and user_id in cache and cache[user_id].get("display_name"):
        return str(cache[user_id]["display_name"])
    if fallback and not fallback.isdigit():
        return fallback
    return "Unbekanntes Mitglied"


# ---------------------------------------------------------------------------
# Akte + Uebersicht
# ---------------------------------------------------------------------------

async def akte(guild_id: int | str, user_id: int | str) -> dict[str, Any]:
    """
    Vollstaendige Akte eines Mitglieds.

    Returns:
        {
          "user_id", "name", "avatar_url",
          "warns": [...], "actions": [...], "notes": [...],
          "timeline": [{"kind": "warn"|"action"|"note", "ts", "ts_display", ...}],
          "counts": {"warns", "warns_active", "points_active", "actions", "notes"},
        }
    """
    gid, uid = _uid(guild_id), _uid(user_id)
    warns = await list_warns(uid)
    actions = await list_mod_actions(uid)
    notes = await list_notes(gid, uid)

    ids: set[str] = {uid}
    ids.update(n["author_id"] for n in notes if n["author_id"].isdigit())
    ids.update(w["moderator"] for w in warns if w["moderator"].isdigit())
    ids.update(a["moderator_id"] for a in actions if a["moderator_id"])
    cache = await get_members(gid, sorted(ids))

    fallback_name = next((a["target_name"] for a in actions if a["target_name"]), None)
    for w in warns:
        mod = w["moderator"]
        w["moderator_name"] = _name_for(cache, mod if mod.isdigit() else None,
                                        None if mod.isdigit() else (mod or None))
    for a in actions:
        a["moderator_display"] = (
            _name_for(cache, a["moderator_id"], a["moderator_name"])
            if (a["moderator_id"] or a["moderator_name"]) else "unbekannt"
        )
    for n in notes:
        n["author_name"] = _name_for(cache, n["author_id"])

    timeline: list[dict[str, Any]] = []
    timeline.extend({"kind": "warn", **w} for w in warns)
    timeline.extend({"kind": "action", **a} for a in actions)
    timeline.extend({"kind": "note", **n} for n in notes)
    timeline.sort(key=lambda e: _sort_key(e.get("ts")), reverse=True)

    active = [w for w in warns if w["active"]]
    return {
        "user_id": uid,
        "name": _name_for(cache, uid, fallback_name),
        "avatar_url": (cache.get(uid) or {}).get("avatar_url"),
        "warns": warns,
        "actions": actions,
        "notes": notes,
        "timeline": timeline,
        "counts": {
            "warns": len(warns),
            "warns_active": len(active),
            "points_active": sum(w["points"] for w in active),
            "actions": len(actions),
            "notes": len(notes),
        },
    }


async def list_akten(guild_id: int | str, search: str = "") -> list[dict[str, Any]]:
    """
    Alle Mitglieder mit mindestens einem Eintrag, neueste Aktivitaet zuerst.

    Args:
        guild_id: Haupt-Guild (fuer Notizen + Namen)
        search: optionaler Teilstring, gegen den Anzeigenamen geprueft
                (Gross-/Kleinschreibung egal); eine exakte User-ID trifft auch

    Returns:
        [{"user_id", "name", "avatar_url", "warns", "warns_active",
          "actions", "notes", "last_ts", "last_display"}]
    """
    gid = _uid(guild_id)
    conn = await get_read_db()
    rows: dict[str, dict[str, Any]] = {}

    def _slot(uid: str) -> dict[str, Any]:
        return rows.setdefault(uid, {
            "user_id": uid, "warns": 0, "warns_active": 0, "actions": 0,
            "notes": 0, "last": None, "fallback": None,
        })

    def _touch(slot: dict[str, Any], ts: Optional[datetime]) -> None:
        if ts and (slot["last"] is None or ts > slot["last"]):
            slot["last"] = ts

    cursor = await conn.execute(
        "SELECT user_id, COUNT(*), SUM(CASE WHEN active THEN 1 ELSE 0 END), MAX(created_at) "
        "FROM warns GROUP BY user_id"
    )
    for r in await cursor.fetchall():
        uid = str(r[0] or "")
        if not uid.isdigit():
            continue
        s = _slot(uid)
        s["warns"] = int(r[1] or 0)
        s["warns_active"] = int(r[2] or 0)
        _touch(s, _parse_ts(r[3], naive_is_utc=False))

    cursor = await conn.execute(
        "SELECT user_id, COUNT(*), MAX(created_at) FROM mod_notes "
        "WHERE guild_id = ? GROUP BY user_id",
        (gid,),
    )
    for r in await cursor.fetchall():
        s = _slot(str(r[0]))
        s["notes"] = int(r[1] or 0)
        _touch(s, _parse_ts(r[2], naive_is_utc=True))

    cursor = await conn.execute(
        "SELECT target, timestamp FROM audit_log WHERE action >= ? AND action < ?",
        (_MOD_ACTION_LO, _MOD_ACTION_HI),
    )
    for r in await cursor.fetchall():
        uid = target_id_from(r[0])
        if not uid:
            continue
        s = _slot(uid)
        s["actions"] += 1
        s["fallback"] = s["fallback"] or target_name_from(r[0])
        _touch(s, _parse_ts(r[1], naive_is_utc=True))

    cache = await get_members(gid, list(rows.keys())) if rows else {}
    needle = (search or "").strip().casefold()
    out: list[dict[str, Any]] = []
    for uid, s in rows.items():
        name = _name_for(cache, uid, s["fallback"])
        if needle and needle not in name.casefold() and needle != uid:
            continue
        out.append({
            "user_id": uid,
            "name": name,
            "avatar_url": (cache.get(uid) or {}).get("avatar_url"),
            "warns": s["warns"],
            "warns_active": s["warns_active"],
            "actions": s["actions"],
            "notes": s["notes"],
            "last_ts": s["last"],
            "last_display": format_ts(s["last"]),
        })
    out.sort(key=lambda e: _sort_key(e["last_ts"]), reverse=True)
    return out
