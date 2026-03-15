"""
Whitelist Manager for Satisfactory Server — SQLite-Persistenz

Alleinige Datenquelle: SQLite (via modules.database.db_manager)
"""

import asyncio
from typing import List, Optional, Dict, Any
from datetime import datetime
from utils.logger import get_logger
from modules.database.db_manager import get_db

logger = get_logger("satisfactory.whitelist")


SERVER_TYPE = "satisfactory"


class WhitelistManager:
    """Manage server whitelist (SQLite)"""

    def __init__(self, **kwargs) -> None:
        self._data: Dict[str, Any] = {"enabled": False, "players": []}
        self._lock = asyncio.Lock()

    async def load_from_db(self) -> None:
        """Load whitelist from SQLite."""
        try:
            db = await get_db()
            cursor = await db.execute(
                "SELECT player_name, added_by, added_at "
                "FROM whitelist WHERE server_type = ?",
                (SERVER_TYPE,),
            )
            rows = await cursor.fetchall()

            players = []
            for row in rows:
                players.append({
                    "name": row[0],
                    "added_by": row[1] or "unknown",
                    "added_at": row[2] or datetime.now().isoformat(),
                })

            self._data["players"] = players
            logger.info(f"Whitelist: loaded {len(players)} players from SQLite")
        except Exception as e:
            logger.error(f"Failed to load whitelist from DB: {e}")

    @property
    def enabled(self) -> bool:
        return self._data.get("enabled", False)

    @enabled.setter
    def enabled(self, value: bool):
        self._data["enabled"] = value

    @property
    def players(self) -> List[Dict[str, Any]]:
        return self._data.get("players", [])

    async def add(self, player_name: str, added_by: str) -> bool:
        """Add player to whitelist. Returns False if already exists."""
        async with self._lock:
            name_lower = player_name.strip().lower()
            for p in self.players:
                if p["name"].lower() == name_lower:
                    return False

            clean_name = player_name.strip()
            self._data.setdefault("players", []).append({
                "name": clean_name,
                "added_by": added_by,
                "added_at": datetime.now().isoformat()
            })

            try:
                db = await get_db()
                await db.execute(
                    "INSERT OR IGNORE INTO whitelist "
                    "(player_name, server_type, added_by) VALUES (?, ?, ?)",
                    (clean_name, SERVER_TYPE, added_by),
                )
                await db.commit()
            except Exception as e:
                logger.error(f"Failed to add to whitelist in DB: {e}")

            logger.info(f"Whitelist: {player_name} added by {added_by}")
            return True

    async def remove(self, player_name: str) -> bool:
        """Remove player from whitelist. Returns False if not found."""
        async with self._lock:
            name_lower = player_name.strip().lower()
            original_len = len(self.players)
            self._data["players"] = [
                p for p in self.players if p["name"].lower() != name_lower
            ]
            if len(self._data["players"]) < original_len:
                try:
                    db = await get_db()
                    await db.execute(
                        "DELETE FROM whitelist "
                        "WHERE LOWER(player_name) = ? AND server_type = ?",
                        (name_lower, SERVER_TYPE),
                    )
                    await db.commit()
                except Exception as e:
                    logger.error(f"Failed to remove from whitelist in DB: {e}")

                logger.info(f"Whitelist: {player_name} removed")
                return True
            return False

    def is_whitelisted(self, player_name: str) -> bool:
        """Check if player is on whitelist"""
        if not self.enabled:
            return True  # If whitelist disabled, everyone is allowed
        name_lower = player_name.strip().lower()
        return any(p["name"].lower() == name_lower for p in self.players)

    def get_list(self) -> List[Dict[str, Any]]:
        """Get all whitelisted players"""
        return self.players.copy()

    def count(self) -> int:
        return len(self.players)
