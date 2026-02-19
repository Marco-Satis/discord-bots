"""
Whitelist Manager for Satisfactory Server
JSON-based whitelist with add/remove/list operations
"""

import asyncio
import json
import aiofiles
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime
from utils.logger import get_logger
from utils.config import DATA_DIR

logger = get_logger("satisfactory.whitelist")

WHITELIST_FILE = DATA_DIR / "whitelist.json"


class WhitelistManager:
    """Manage server whitelist (JSON file based)"""

    def __init__(self, filepath: Optional[Path] = None) -> None:
        self.filepath = filepath or WHITELIST_FILE
        self._data: Dict[str, Any] = {"enabled": False, "players": []}
        self._lock = asyncio.Lock()

    async def load(self) -> None:
        """Load whitelist from disk"""
        try:
            if self.filepath.exists():
                async with aiofiles.open(self.filepath, "r", encoding="utf-8") as f:
                    content = await f.read()
                    self._data = json.loads(content)
            else:
                await self.save()
        except (json.JSONDecodeError, FileNotFoundError, IOError) as e:
            logger.error(f"Failed to load whitelist: {e}")

    async def save(self) -> None:
        """Save whitelist to disk"""
        try:
            self.filepath.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(self.filepath, "w", encoding="utf-8") as f:
                await f.write(json.dumps(self._data, indent=2, ensure_ascii=False))
        except (json.JSONDecodeError, FileNotFoundError, IOError) as e:
            logger.error(f"Failed to save whitelist: {e}")

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

            self._data.setdefault("players", []).append({
                "name": player_name.strip(),
                "added_by": added_by,
                "added_at": datetime.now().isoformat()
            })
            await self.save()
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
                await self.save()
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
