"""
Server Settings Backup - Save/Restore Satisfactory API settings as JSON
Backs up ServerOptions and AdvancedGameSettings
"""

import json
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple, List, Dict, Any

from utils.logger import get_logger

logger = get_logger("settings_backup")


class SettingsBackup:
    """
    Backs up and restores Satisfactory server settings via the API.
    Settings are stored as JSON files.
    """

    def __init__(self, api: Any, backup_dir: Path) -> None:
        self.api = api
        self.backup_dir = backup_dir
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    async def save_settings(self, name: Optional[str] = None) -> Tuple[bool, str, Optional[Path]]:
        """Save current server settings to JSON.
        Returns (success, message, filepath)"""
        try:
            # Fetch settings from API
            server_options = await self.api.get_server_options()
            creative_mode, advanced_settings = await self.api.get_advanced_game_settings()
            server_state = await self.api.query_server_state()

            settings = {
                "timestamp": datetime.now().isoformat(),
                "session_name": server_state.active_session,
                "server_options": server_options,
                "advanced_game_settings": advanced_settings,
                "creative_mode_enabled": creative_mode,
            }

            # Generate filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            if name:
                safe_name = "".join(c for c in name if c.isalnum() or c in "-_")
                if not safe_name:
                    safe_name = "backup"
                filename = f"settings_{safe_name}_{timestamp}.json"
            else:
                filename = f"settings_{timestamp}.json"

            filepath = self.backup_dir / filename

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(settings, f, indent=2, ensure_ascii=False)

            logger.info(f"Settings saved: {filename}")

            # Rotate old backups (keep last 20)
            self._rotate(max_files=20)

            return True, f"Settings gesichert: {filename}", filepath

        except Exception as e:
            logger.error(f"Failed to save settings: {e}")
            return False, f"Fehler: {e}", None

    async def restore_settings(self, filename: str) -> Tuple[bool, str]:
        """Restore server settings from a JSON backup.
        Returns (success, message)"""
        # Prevent path traversal
        safe_name = Path(filename).name
        filepath = self.backup_dir / safe_name
        if not filepath.resolve().is_relative_to(self.backup_dir.resolve()):
            return False, "Ungueltiger Dateiname!"
        if not filepath.exists():
            return False, f"Datei nicht gefunden: {safe_name}"

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                settings = json.load(f)

            applied = []
            failed = []

            # Restore server options
            server_options = settings.get("server_options", {})
            if server_options:
                success = await self.api.apply_server_options(server_options)
                if success:
                    applied.append(f"Server-Optionen ({len(server_options)} Einstellungen)")
                else:
                    failed.append("Server-Optionen")

            result_parts = []
            if applied:
                result_parts.append("✅ Wiederhergestellt: " + ", ".join(applied))
            if failed:
                result_parts.append("❌ Fehlgeschlagen: " + ", ".join(failed))

            msg = "\n".join(result_parts) if result_parts else "Keine Einstellungen zum Wiederherstellen."
            success = len(failed) == 0 and len(applied) > 0

            logger.info(f"Settings restored from {filename}: {len(applied)} OK, {len(failed)} failed")
            return success, msg

        except Exception as e:
            logger.error(f"Failed to restore settings: {e}")
            return False, f"Fehler: {e}"

    def list_backups(self) -> List[Dict[str, Any]]:
        """List available settings backups"""
        backups = []
        try:
            for f in sorted(self.backup_dir.glob("settings_*.json"),
                           key=lambda p: p.stat().st_mtime, reverse=True):
                try:
                    with open(f, "r") as fh:
                        data = json.load(fh)
                    backups.append({
                        "filename": f.name,
                        "timestamp": data.get("timestamp", "?"),
                        "session": data.get("session_name", "?"),
                        "options_count": len(data.get("server_options", {})),
                    })
                except (json.JSONDecodeError, OSError, ValueError) as e:
                    logger.debug(f"Failed to read settings backup {f.name}: {e}")
                    backups.append({
                        "filename": f.name,
                        "timestamp": "?",
                        "session": "?",
                        "options_count": 0,
                    })
        except Exception as e:
            logger.error(f"Failed to list settings backups: {e}")
        return backups

    def _rotate(self, max_files: int = 20) -> None:
        """Keep only the last N settings backups"""
        try:
            files = sorted(self.backup_dir.glob("settings_*.json"),
                          key=lambda p: p.stat().st_mtime)
            while len(files) > max_files:
                oldest = files.pop(0)
                oldest.unlink()
                logger.debug(f"Rotated settings backup: {oldest.name}")
        except OSError as e:
            logger.error(f"Settings rotation error: {e}")
