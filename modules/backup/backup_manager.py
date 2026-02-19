"""
Backup Manager for Satisfactory Server
Creates compressed backups of savegames, handles rotation, and restore
"""

import os
import json
import shutil
import tarfile
import asyncio
import aiofiles
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any
from datetime import datetime
from utils.logger import get_logger
from utils.config import DATA_DIR, get_config
from utils.formatting import format_bytes

logger = get_logger("backup.manager")

BACKUP_HISTORY = DATA_DIR / "backup_history.json"


class BackupManager:
    """Manage server backups with rotation and history tracking"""

    def __init__(self, savegame_path: Path, backup_path: Path,
                 max_backups: int = 20):
        self.savegame_path = savegame_path
        self.backup_path = backup_path
        self.max_backups = max_backups
        self._history = {"backups": []}

    async def load(self) -> None:
        """Load backup history"""
        try:
            if BACKUP_HISTORY.exists():
                async with aiofiles.open(BACKUP_HISTORY, "r", encoding="utf-8") as f:
                    content = await f.read()
                    self._history = json.loads(content)
            else:
                await self._save_history()
        except (json.JSONDecodeError, FileNotFoundError, IOError) as e:
            logger.error(f"Failed to load backup history: {e}")

    async def _save_history(self) -> None:
        """Save backup history"""
        try:
            BACKUP_HISTORY.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(BACKUP_HISTORY, "w", encoding="utf-8") as f:
                await f.write(json.dumps(self._history, indent=2))
        except (IOError, OSError) as e:
            logger.error(f"Failed to save backup history: {e}")

    async def create_backup(self, name: str = None, created_by: str = "system",
                            verify: bool = True) -> Tuple[bool, str, Optional[Path]]:
        """
        Create a compressed backup of the savegame directory.
        Returns (success, message, backup_path)
        """
        try:
            self.backup_path.mkdir(parents=True, exist_ok=True)

            # Find savegame directory
            save_dir = self.savegame_path / "server"
            if not save_dir.exists():
                save_dir = self.savegame_path

            if not save_dir.exists():
                return False, "Savegame-Verzeichnis nicht gefunden!", None

            # Generate backup name
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            if name:
                safe_name = "".join(c for c in name if c.isalnum() or c in "-_")
                backup_name = f"backup_{safe_name}_{timestamp}"
            else:
                backup_name = f"backup_{timestamp}"

            backup_file = self.backup_path / f"{backup_name}.tar.gz"

            # Create backup in a thread to not block the event loop
            loop = asyncio.get_event_loop()
            size = await loop.run_in_executor(
                None, self._create_archive, save_dir, backup_file
            )

            if not backup_file.exists():
                return False, "Backup-Erstellung fehlgeschlagen!", None

            # Verify backup integrity
            if verify:
                valid, verify_msg = await self.verify_backup(backup_file)
                if not valid:
                    logger.error(f"Backup verification failed: {verify_msg}")
                    backup_file.unlink(missing_ok=True)
                    return False, f"Backup erstellt aber beschaedigt: {verify_msg}", None

            # Record in history
            entry = {
                "name": backup_name,
                "filename": backup_file.name,
                "path": str(backup_file),
                "size_bytes": size,
                "size_human": format_bytes(size),
                "created_at": datetime.now().isoformat(),
                "created_by": created_by,
                "type": "manual" if name else "auto"
            }
            self._history["backups"].append(entry)
            await self._save_history()

            # Rotation
            await self._rotate()

            logger.info(f"Backup created: {backup_name} ({format_bytes(size)}) by {created_by}")
            return True, f"Backup erstellt: {backup_name} ({format_bytes(size)})", backup_file

        except Exception as e:
            logger.error(f"Backup creation failed: {e}")
            return False, f"Backup fehlgeschlagen: {e}", None

    def _create_archive(self, source_dir: Path, target: Path) -> int:  # type: ignore
        """Create tar.gz archive (runs in executor)"""
        with tarfile.open(target, "w:gz") as tar:
            tar.add(source_dir, arcname=source_dir.name)
        return target.stat().st_size

    async def _rotate(self) -> None:
        """Remove oldest backups if exceeding max_backups"""
        backups = self.list_backups()
        if len(backups) <= self.max_backups:
            return

        # Sort by creation date, oldest first
        to_remove = backups[self.max_backups:]
        for bp in to_remove:
            try:
                bp_path = Path(bp["path"])
                if bp_path.exists():
                    bp_path.unlink()
                    logger.info(f"Rotated old backup: {bp['name']}")
            except OSError as e:
                logger.error(f"Failed to rotate backup: {e}")

        # Update history
        keep_names = {b["name"] for b in backups[:self.max_backups]}
        self._history["backups"] = [
            b for b in self._history["backups"] if b["name"] in keep_names
        ]
        await self._save_history()

    def list_backups(self, max_results: int = 50) -> List[Dict[str, Any]]:
        """List all backups sorted by date (newest first)"""
        backups = []

        # Combine history with actual files
        known_files = {b["filename"] for b in self._history.get("backups", [])}

        # From history
        for bp in self._history.get("backups", []):
            bp_path = Path(bp["path"])
            if bp_path.exists():
                backups.append(bp)

        # Scan for untracked backups
        try:
            for f in self.backup_path.glob("*.tar.gz"):
                if f.name not in known_files:
                    stat = f.stat()
                    backups.append({
                        "name": f.stem.replace(".tar", ""),
                        "filename": f.name,
                        "path": str(f),
                        "size_bytes": stat.st_size,
                        "size_human": format_bytes(stat.st_size),
                        "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        "created_by": "unknown",
                        "type": "untracked"
                    })
        except OSError as e:
            logger.error(f"Failed to scan backups: {e}")

        backups.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return backups[:max_results]

    def get_backup(self, name: str) -> Optional[Dict[str, Any]]:
        """Get backup by name"""
        for bp in self.list_backups():
            if bp["name"] == name or bp["filename"] == name:
                return bp
        return None

    def get_latest(self) -> Optional[Dict[str, Any]]:
        """Get latest backup"""
        backups = self.list_backups(max_results=1)
        return backups[0] if backups else None

    async def restore(self, backup_name: str) -> Tuple[bool, str]:
        """
        Restore a backup. Server MUST be stopped before calling this!
        Returns (success, message)
        """
        bp = self.get_backup(backup_name)
        if not bp:
            return False, f"Backup '{backup_name}' nicht gefunden!"

        bp_path = Path(bp["path"])
        if not bp_path.exists():
            return False, "Backup-Datei nicht mehr vorhanden!"

        try:
            save_dir = self.savegame_path / "server"
            if not save_dir.exists():
                save_dir = self.savegame_path

            # Create pre-restore backup
            pre_restore = self.backup_path / f"pre_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}.tar.gz"
            loop = asyncio.get_event_loop()
            if save_dir.exists():
                await loop.run_in_executor(
                    None, self._create_archive, save_dir, pre_restore
                )
                logger.info(f"Pre-restore backup: {pre_restore.name}")

            # Extract backup
            await loop.run_in_executor(
                None, self._extract_archive, bp_path, save_dir.parent
            )

            logger.info(f"Backup restored: {backup_name}")
            return True, f"Backup '{backup_name}' wiederhergestellt!"

        except Exception as e:
            logger.error(f"Restore failed: {e}")
            return False, f"Restore fehlgeschlagen: {e}"

    def _extract_archive(self, archive: Path, target_dir: Path) -> None:  # type: ignore
        """Extract tar.gz archive (runs in executor)"""
        with tarfile.open(archive, "r:gz") as tar:
            # Validate all members to prevent path traversal
            for member in tar.getmembers():
                member_path = (target_dir / member.name).resolve()
                if not str(member_path).startswith(str(target_dir.resolve())):
                    raise ValueError(f"Path traversal detected in archive: {member.name}")
            tar.extractall(path=target_dir)

    async def verify_backup(self, backup_path: Path) -> Tuple[bool, str]:
        """
        Verify backup integrity by testing extraction without writing files.
        Returns (valid, message)
        """
        if not backup_path.exists():
            return False, "Backup-Datei nicht gefunden!"

        try:
            loop = asyncio.get_running_loop()
            file_count, total_size = await loop.run_in_executor(
                None, self._verify_archive, backup_path
            )
            size_str = format_bytes(total_size)
            return True, f"Backup OK: {file_count} Dateien, {size_str} entpackt"
        except Exception as e:
            logger.error(f"Backup verification failed: {e}")
            return False, f"Backup beschaedigt: {e}"

    @staticmethod
    def _verify_archive(archive: Path) -> Tuple[int, int]:
        """Test-extract archive without writing (runs in executor)"""
        file_count = 0
        total_size = 0
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar.getmembers():
                # Verify each member can be read
                if member.isfile():
                    f = tar.extractfile(member)
                    if f:
                        # Read and discard to verify integrity
                        while True:
                            chunk = f.read(65536)
                            if not chunk:
                                break
                            total_size += len(chunk)
                        f.close()
                    file_count += 1
        return file_count, total_size

    def get_backup_path(self, name: str) -> Optional[Path]:
        """Get path to backup file for download"""
        bp = self.get_backup(name)
        if bp:
            p = Path(bp["path"])
            return p if p.exists() else None
        return None

    def count(self) -> int:
        return len(self.list_backups())

    def total_size(self) -> int:  # type: ignore
        """Total size of all backups in bytes"""
        total = 0
        try:
            for f in self.backup_path.glob("*.tar.gz"):
                total += f.stat().st_size
        except OSError:
            pass
        return total
