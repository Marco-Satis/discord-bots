"""
Savegame Statistics for Satisfactory
Analyzes .sav files to extract game statistics
"""

from pathlib import Path
from typing import Dict, Optional, List, Any
from datetime import datetime
from utils.logger import get_logger
from utils.formatting import format_bytes
from modules.satisfactory.save_header import read_save_header, header_to_dict

logger = get_logger("satisfactory.savegame_stats")


class SavegameStats:
    """Parse and analyze Satisfactory savegame files"""

    def __init__(self, savegame_path: Path) -> None:
        self.savegame_path = savegame_path

    def list_saves(self, max_results: int = 20) -> List[Dict[str, Any]]:
        """List available savegame files sorted by modification date"""
        saves = []
        try:
            save_dir = self.savegame_path / "server"
            if not save_dir.exists():
                save_dir = self.savegame_path
            
            for f in save_dir.glob("*.sav"):
                stat = f.stat()
                saves.append({
                    "name": f.stem,
                    "filename": f.name,
                    "path": str(f),
                    "size_bytes": stat.st_size,
                    "size_human": format_bytes(stat.st_size),
                    "modified": datetime.fromtimestamp(stat.st_mtime),
                    "modified_str": datetime.fromtimestamp(stat.st_mtime).strftime("%d.%m.%Y %H:%M")
                })
        except Exception as e:
            logger.error(f"Failed to list saves: {e}")

        saves.sort(key=lambda x: x["modified"], reverse=True)
        return saves[:max_results]

    def get_latest_save(self) -> Optional[Dict[str, Any]]:
        """Get the most recent savegame"""
        saves = self.list_saves(max_results=1)
        return saves[0] if saves else None

    async def analyze(self, save_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Analyze a savegame file and return statistics.
        If no save_name specified, uses the latest save.
        """
        stats = {
            "name": "Unbekannt",
            "size": "N/A",
            "last_modified": "N/A",
            "session_name": "",
            "play_hours": 0,
            "available": False,
            "error": None
        }

        try:
            if save_name:
                save_dir = self.savegame_path / "server"
                if not save_dir.exists():
                    save_dir = self.savegame_path
                
                save_file = save_dir / f"{save_name}.sav"
                if not save_file.exists():
                    save_file = save_dir / save_name
                    if not save_file.exists():
                        stats["error"] = f"Savegame '{save_name}' nicht gefunden."
                        return stats
            else:
                latest = self.get_latest_save()
                if not latest:
                    stats["error"] = "Keine Savegames gefunden."
                    return stats
                save_file = Path(latest["path"])

            file_stat = save_file.stat()
            stats["name"] = save_file.stem
            stats["size"] = format_bytes(file_stat.st_size)
            stats["size_bytes"] = file_stat.st_size
            stats["last_modified"] = datetime.fromtimestamp(
                file_stat.st_mtime
            ).strftime("%d.%m.%Y %H:%M")
            stats["available"] = True

            # Read header using shared parser
            try:
                header = read_save_header(save_file)
                if header:
                    stats.update(header_to_dict(header))
            except (IOError, OSError, ValueError) as e:
                logger.debug(f"Could not parse save header: {e}")

        except Exception as e:
            stats["error"] = f"Analyse fehlgeschlagen: {e}"
            logger.error(f"Savegame analysis error: {e}")

        return stats

    def get_save_count(self) -> int:
        """Count total number of savegame files"""
        try:
            save_dir = self.savegame_path / "server"
            if not save_dir.exists():
                save_dir = self.savegame_path
            return len(list(save_dir.glob("*.sav")))
        except (IOError, OSError) as e:
            logger.debug(f"Failed to count savegames: {e}")
            return 0

    def get_total_size(self) -> int:
        """Get total size of all savegames in bytes"""
        total = 0
        try:
            save_dir = self.savegame_path / "server"
            if not save_dir.exists():
                save_dir = self.savegame_path
            for f in save_dir.glob("*.sav"):
                total += f.stat().st_size
        except (IOError, OSError) as e:
            logger.debug(f"Failed to calculate total savegame size: {e}")
        return total
