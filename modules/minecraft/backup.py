"""
Minecraft World Backup Manager
Handles world backup/restore operations
"""

import asyncio
import shutil
import time
from pathlib import Path
from datetime import datetime
from typing import Tuple, List, Optional, Dict
from utils.logger import get_logger
import os

logger = get_logger("minecraft.backup")


class MinecraftBackupManager:
    """Minecraft world backup management"""

    def __init__(self,
                 world_path: Optional[Path] = None,
                 backup_path: Optional[Path] = None):
        self.world_path = Path(world_path or os.getenv(
            "MINECRAFT_WORLD_PATH",
            "/home/minecraft/server/world"
        ))
        self.backup_path = Path(backup_path or os.getenv(
            "MINECRAFT_BACKUP_PATH",
            "/home/minecraft/backups"
        ))

        # Create backup directory if it doesn't exist
        self.backup_path.mkdir(parents=True, exist_ok=True)

    async def create_backup(self,
                           name: Optional[str] = None,
                           created_by: str = "System") -> Tuple[bool, str, Optional[Path]]:
        """
        Create a backup of the world folder

        Args:
            name: Optional custom backup name (default: timestamp)
            created_by: Username of who created the backup

        Returns:
            (success, message, backup_path)
        """
        if not self.world_path.exists():
            return False, f"World-Pfad existiert nicht: {self.world_path}", None

        # Generate backup name
        if not name:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            name = f"world_{timestamp}"

        # Sanitize name
        name = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)

        backup_dest = self.backup_path / name

        if backup_dest.exists():
            return False, f"Backup existiert bereits: {name}", None

        try:
            logger.info(f"Creating backup: {name} (by {created_by})")

            # Copy world directory
            def copy_tree():
                shutil.copytree(self.world_path, backup_dest,
                              ignore=shutil.ignore_patterns('*.lock'))

            # Run copy in thread pool to avoid blocking
            await asyncio.get_event_loop().run_in_executor(None, copy_tree)

            # Create metadata file
            metadata_path = backup_dest / ".backup_metadata"
            metadata_content = f"""Created: {datetime.now().isoformat()}
By: {created_by}
World Path: {self.world_path}
"""
            metadata_path.write_text(metadata_content, encoding='utf-8')

            size_mb = self._get_path_size_mb(backup_dest)
            msg = f"Backup erstellt: {name} ({size_mb:.1f} MB)"
            logger.info(msg)

            return True, msg, backup_dest

        except Exception as e:
            logger.error(f"Backup creation failed: {e}")
            # Cleanup partial backup
            if backup_dest.exists():
                try:
                    shutil.rmtree(backup_dest)
                except (IOError, OSError) as cleanup_err:
                    logger.debug(f"Failed to cleanup partial backup: {cleanup_err}")
            return False, f"Backup fehlgeschlagen: {e}", None

    def list_backups(self, max_results: int = 20) -> List[Dict]:
        """
        List available backups sorted by date (newest first)

        Args:
            max_results: Maximum number of backups to return

        Returns:
            List of backup info dicts with name, date, size, created_by
        """
        backups = []

        try:
            for backup_dir in sorted(self.backup_path.iterdir(),
                                    key=lambda p: p.stat().st_mtime,
                                    reverse=True):
                if not backup_dir.is_dir():
                    continue

                try:
                    # Get metadata if available
                    metadata_file = backup_dir / ".backup_metadata"
                    created_by = "System"
                    created_date = datetime.fromtimestamp(
                        backup_dir.stat().st_mtime
                    ).isoformat()

                    if metadata_file.exists():
                        metadata = metadata_file.read_text(encoding='utf-8')
                        for line in metadata.split("\n"):
                            if line.startswith("Created:"):
                                created_date = line.split(":", 1)[1].strip()
                            elif line.startswith("By:"):
                                created_by = line.split(":", 1)[1].strip()

                    size_mb = self._get_path_size_mb(backup_dir)

                    backups.append({
                        "name": backup_dir.name,
                        "created": created_date,
                        "size_mb": size_mb,
                        "created_by": created_by,
                        "path": backup_dir
                    })

                except Exception as e:
                    logger.debug(f"Error reading backup info for {backup_dir.name}: {e}")
                    continue

            return backups[:max_results]

        except Exception as e:
            logger.error(f"Error listing backups: {e}")
            return []

    async def restore(self, backup_name: str) -> Tuple[bool, str]:
        """
        Restore world from backup

        WARNING: This will overwrite the current world!

        Args:
            backup_name: Name of backup to restore

        Returns:
            (success, message)
        """
        backup_path = self.backup_path / backup_name

        if not backup_path.exists():
            return False, f"Backup nicht gefunden: {backup_name}"

        if not backup_path.is_dir():
            return False, f"Backup ist kein Verzeichnis: {backup_name}"

        try:
            logger.info(f"Restoring world from backup: {backup_name}")

            # Backup current world first
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            current_backup = self.backup_path / f"world_before_restore_{timestamp}"

            if self.world_path.exists():
                def backup_current():
                    shutil.copytree(self.world_path, current_backup,
                                  ignore=shutil.ignore_patterns('*.lock'))

                await asyncio.get_event_loop().run_in_executor(None, backup_current)
                logger.info(f"Current world backed up to: {current_backup.name}")

                # Remove current world
                shutil.rmtree(self.world_path)

            # Restore from backup
            def restore_backup():
                shutil.copytree(backup_path, self.world_path)

            await asyncio.get_event_loop().run_in_executor(None, restore_backup)

            msg = f"Welt wiederhergestellt: {backup_name}"
            logger.info(msg)
            return True, msg

        except Exception as e:
            logger.error(f"Restore failed: {e}")
            return False, f"Wiederherstellung fehlgeschlagen: {e}"

    def delete_backup(self, backup_name: str) -> Tuple[bool, str]:
        """Delete a backup"""
        backup_path = self.backup_path / backup_name

        if not backup_path.exists():
            return False, f"Backup nicht gefunden: {backup_name}"

        try:
            shutil.rmtree(backup_path)
            logger.info(f"Deleted backup: {backup_name}")
            return True, f"Backup geloescht: {backup_name}"
        except Exception as e:
            logger.error(f"Failed to delete backup {backup_name}: {e}")
            return False, f"Loeschung fehlgeschlagen: {e}"

    def _get_path_size_mb(self, path: Path) -> float:
        """Calculate directory size in MB"""
        total = 0
        try:
            for entry in path.rglob("*"):
                if entry.is_file():
                    total += entry.stat().st_size
        except Exception as e:
            logger.debug(f"Error calculating size: {e}")

        return total / (1024 * 1024)
