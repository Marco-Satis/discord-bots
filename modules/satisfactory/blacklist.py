"""
Blacklist Manager for Satisfactory Server
JSON-based blacklist with add/remove/list operations
"""

import json
import aiofiles
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime
from utils.logger import get_logger
from utils.config import DATA_DIR

logger = get_logger("satisfactory.blacklist")

BLACKLIST_FILE = DATA_DIR / "blacklist.json"


class BlacklistManager:
    """Manage server blacklist/banlist (JSON file based)"""

    def __init__(self, filepath: Optional[Path] = None) -> None:
        self.filepath = filepath or BLACKLIST_FILE
        self._data: Dict[str, Any] = {"players": []}

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

    @property
    def players(self) -> List[Dict[str, Any]]:
        return self._data.get("players", [])

    async def add(self, player_name: str, reason: str, banned_by: str) -> bool:
        """Add player to blacklist. Returns False if already banned."""
        name_lower = player_name.strip().lower()
        for p in self.players:
            if p["name"].lower() == name_lower:
                return False

        self._data.setdefault("players", []).append({
            "name": player_name.strip(),
            "reason": reason,
            "banned_by": banned_by,
            "banned_at": datetime.now().isoformat()
        })
        await self.save()
        logger.info(f"Blacklist: {player_name} banned by {banned_by} - {reason}")
        return True

    async def remove(self, player_name: str) -> bool:
        """Remove player from blacklist (unban). Returns False if not found."""
        name_lower = player_name.strip().lower()
        original_len = len(self.players)
        self._data["players"] = [
            p for p in self.players if p["name"].lower() != name_lower
        ]
        if len(self._data["players"]) < original_len:
            await self.save()
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
