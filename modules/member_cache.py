"""
Member-Cache (E3) — Anzeigename + Avatar-URL pro Guild-Member.

Der Web-Prozess kann Discord-Namen/Avatare nicht ohne Bot/Gateway aufloesen.
Der Bot pflegt deshalb diesen kleinen Cache bei XP-Aktivitaet (guild-scoped),
das Dashboard joint ihn fuer ein lesbares Leaderboard (Name + Avatar statt
roher User-ID).

Backed by Migration v8: Tabelle `member_cache`
  (guild_id, user_id, display_name, avatar_url, updated_at,
   PRIMARY KEY(guild_id, user_id)).

Privacy: nur Anzeigename + oeffentliche Avatar-CDN-URL, per-Guild isoliert
(kein Cross-Guild-Leak). Keine sensible PII ueber das hinaus was das
Leaderboard ohnehin oeffentlich zeigt.
"""
from __future__ import annotations

from typing import Any

from modules.database.db_manager import DBHelper, get_db, get_read_db
from utils.logger import get_logger

logger = get_logger("member_cache")


async def upsert_member(
    guild_id: int | str,
    user_id: int | str,
    display_name: str | None,
    avatar_url: str | None,
) -> None:
    """
    Anzeigename + Avatar-URL eines Members cachen (Upsert), guild-scoped.

    Fehler werden geloggt, nie geworfen — der Cache ist Best-Effort und darf
    XP-Vergabe / Hot-Path nie blockieren.
    """
    try:
        # Ueber DBHelper.execute -> beinhaltet den Cross-Prozess-Write-Retry bei
        # "database is locked" (execute+commit). Cache bleibt Best-Effort.
        db = await get_db()
        await DBHelper(db).execute(
            "INSERT INTO member_cache "
            "(guild_id, user_id, display_name, avatar_url, updated_at) "
            "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(guild_id, user_id) DO UPDATE SET "
            "display_name = excluded.display_name, "
            "avatar_url = excluded.avatar_url, "
            "updated_at = CURRENT_TIMESTAMP",
            (str(guild_id), str(user_id), display_name, avatar_url),
        )
    except Exception as e:  # noqa: BLE001 — Cache nie fatal
        logger.debug(f"member_cache Upsert {guild_id}/{user_id} fehlgeschlagen: {e}")


async def get_members(
    guild_id: int | str, user_ids: list[str] | list[int]
) -> dict[str, dict[str, Any]]:
    """
    Cache-Eintraege fuer mehrere User einer Guild holen (Batch).

    Args:
        guild_id: Discord-Guild-ID
        user_ids: Liste von User-IDs (str/int)

    Returns:
        Map user_id(str) -> {"display_name": str|None, "avatar_url": str|None}.
        Nicht gecachte User fehlen in der Map (Aufrufer faellt auf ID zurueck).
    """
    ids = [str(u) for u in user_ids]
    if not ids:
        return {}
    try:
        conn = await get_read_db()
        # Parametrisierte IN-Liste (kein f-string-SQL) — nur Platzhalter sind dynamisch.
        placeholders = ",".join("?" for _ in ids)
        cursor = await conn.execute(
            "SELECT user_id, display_name, avatar_url FROM member_cache "
            # Dynamisch ist ausschliesslich die ZAHL der Platzhalter;
            # jeder Wert wird gebunden. Von zwei Reviews geprueft und
            # als Fehlalarm bestaetigt — die Markierung steht bewusst
            # allein am Zeilenende, sonst liest bandit die Woerter
            # dahinter als Test-Namen.
            f"WHERE guild_id = ? AND user_id IN ({placeholders})",  # nosec B608
            (str(guild_id), *ids),
        )
        rows = await cursor.fetchall()
    except Exception as e:  # noqa: BLE001
        logger.error(f"member_cache Batch-Read fehlgeschlagen: {e}")
        return {}

    return {
        str(row[0]): {"display_name": row[1], "avatar_url": row[2]}
        for row in rows
    }
