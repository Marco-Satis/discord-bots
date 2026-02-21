"""
Blacklist Manager for Satisfactory Server — Dual-Read Mode

Primary source of truth: SQLite (via modules.database.db_manager)
Fallback / cache layer:   JSON file on disk

load()         — reads from JSON (initial bootstrap / fallback)
load_from_db() — reads from SQLite blacklist table and overwrites in-memory data
add() / remove() — write to both JSON *and* SQLite so the two stay in sync
"""

import asyncio
import json
import aiofiles
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime
from utils.logger import get_logger
from utils.config import DATA_DIR
from modules.database.db_manager import get_db

logger = get_logger("satisfactory.blacklist")

BLACKLIST_FILE = DATA_DIR / "blacklist.json"


SERVER_TYPE = "satisfactory"


class BlacklistManager:
    """Manage server blacklist/banlist (Dual-Read: SQLite + JSON fallback)"""

    def __init__(self, filepath: Optional[Path] = None) -> None:
        self.filepath = filepath or BLACKLIST_FILE
        self._data: Dict[str, Any] = {"players": []}
        self._lock = asyncio.Lock()

    async def load(self) -> None:
        """Load blacklist from disk"""
        try:
            if self.filepath.exists():
                async with aiofiles.open(self.filepath, "r", encoding="utf-8") as f:
                    content = await f.read()
                    self._data = json.loads(content)
            else:
                await self.save()
        except (json.JSONDecodeError, FileNotFoundError, IOError) as e:
            logger.error(f"Failed to load blacklist: {e}")

    async def save(self) -> None:
        """Save blacklist to disk"""
        try:
            self.filepath.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(self.filepath, "w", encoding="utf-8") as f:
                await f.write(json.dumps(self._data, indent=2, ensure_ascii=False))
        except (json.JSONDecodeError, FileNotFoundError, IOError) as e:
            logger.error(f"Failed to save blacklist: {e}")

    async def load_from_db(self) -> None:
        """Load blacklist from SQLite (WHERE server_type = 'satisfactory').

        Overwrites the in-memory players list so that SQLite becomes the
        authoritative source.  Falls back to the existing in-memory data
        (loaded via JSON) if the database query fails.
        """
        try:
            db = await get_db()
            cursor = await db.execute(
                "SELECT player_name, ip_address, reason, added_by, added_at "
                "FROM blacklist WHERE server_type = ?",
                (SERVER_TYPE,),
            )
            rows = await cursor.fetchall()

            players: List[Dict[str, Any]] = []
            for row in rows:
                players.append({
                    "name": row["player_name"] or "",
                    "ip": row["ip_address"] or "",
                    "reason": row["reason"] or "",
                    "banned_by": row["added_by"] or "",
                    "banned_at": row["added_at"] or "",
                })

            self._data["players"] = players
            logger.info(f"Loaded {len(players)} blacklist entries from SQLite")
        except Exception as e:
            logger.error(f"Failed to load blacklist from SQLite, keeping JSON data: {e}")

    @property
    def players(self) -> List[Dict[str, Any]]:
        return self._data.get("players", [])

    async def add(self, player_name: str, reason: str, banned_by: str) -> bool:
        """Add player to blacklist. Returns False if already banned.

        Writes to both JSON (fallback) and SQLite (primary).
        """
        async with self._lock:
            name_lower = player_name.strip().lower()
            for p in self.players:
                if p["name"].lower() == name_lower:
                    return False

            now = datetime.now().isoformat()
            entry = {
                "name": player_name.strip(),
                "reason": reason,
                "banned_by": banned_by,
                "banned_at": now,
            }
            self._data.setdefault("players", []).append(entry)
            await self.save()

            # --- SQLite sync ---
            try:
                db = await get_db()
                await db.execute(
                    "INSERT INTO blacklist (player_name, server_type, reason, added_by, added_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (player_name.strip(), SERVER_TYPE, reason, banned_by, now),
                )
                await db.commit()
            except Exception as e:
                logger.error(f"Failed to insert blacklist entry into SQLite: {e}")

            logger.info(f"Blacklist: {player_name} banned by {banned_by} - {reason}")
            return True

    async def remove(self, player_name: str) -> bool:
        """Remove player from blacklist (unban). Returns False if not found.

        Deletes from both JSON (fallback) and SQLite (primary).
        """
        async with self._lock:
            name_lower = player_name.strip().lower()
            original_len = len(self.players)
            self._data["players"] = [
                p for p in self.players if p["name"].lower() != name_lower
            ]
            if len(self._data["players"]) < original_len:
                await self.save()

                # --- SQLite sync ---
                try:
                    db = await get_db()
                    await db.execute(
                        "DELETE FROM blacklist WHERE player_name = ? AND server_type = ?",
                        (player_name.strip(), SERVER_TYPE),
                    )
                    await db.commit()
                except Exception as e:
                    logger.error(f"Failed to delete blacklist entry from SQLite: {e}")

                logger.info(f"Blacklist: {player_name} unbanned")
                return True
            return False

    def is_banned(self, player_name: str) -> bool:
        """Check if player is on blacklist"""
        name_lower = player_name.strip().lower()
        return any(p["name"].lower() == name_lower for p in self.players)

    def get_ban_info(self, player_name: str) -> Optional[Dict[str, Any]]:
        """Get ban details for a player"""
        name_lower = player_name.strip().lower()
        for p in self.players:
            if p["name"].lower() == name_lower:
                return p
        return None

    def get_list(self) -> List[Dict[str, Any]]:
        """Get all banned players"""
        return self.players.copy()

    def count(self) -> int:
        return len(self.players)
