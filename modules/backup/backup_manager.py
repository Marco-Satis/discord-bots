"""
Backup Manager for Satisfactory Server
Creates compressed backups of savegames, handles rotation, and restore

Dual-Read Mode: Writes to SQLite backup_history table,
reads from SQLite primary with JSON fallback.
"""

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
from modules.database.db_manager import get_db

logger = get_logger("backup.manager")

BACKUP_HISTORY = DATA_DIR / "backup_history.json"


class BackupManager:
    """Manage server backups with rotation and history tracking"""

    def __init__(self, savegame_path: Path, backup_path: Path,
                 max_backups: int = 20, server_type: str = "satisfactory"):
        self.savegame_path = savegame_path
        self.backup_path = backup_path
        self.max_backups = max_backups
        self.server_type = server_type
        self._history = {"backups": []}

    async def load(self) -> None:
        """Load backup history from SQLite, fall back to JSON"""
        # Try SQLite first
        loaded = await self.load_from_db()
        if loaded:
            return

        # Fallback: load from JSON
        try:
            if BACKUP_HISTORY.exists():
                async with aiofiles.open(BACKUP_HISTORY, "r", encoding="utf-8") as f:
                    content = await f.read()
                    self._history = json.loads(content)
                logger.info("Backup history loaded from JSON fallback")
            else:
                self._history = {"backups": []}
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to load backup history from JSON: {e}")

    async def load_from_db(self) -> bool:
        """
        Load backup history from SQLite backup_history table.
        Returns True if successful, False otherwise (triggers JSON fallback).
        """
        try:
            db = await get_db()
            cursor = await db.execute(
                "SELECT id, server_type, backup_type, filename, size_bytes, "
                "checksum, integrity_ok, cloud_synced, cloud_synced_at, created_at "
                "FROM backup_history "
                "WHERE server_type = ? "
                "ORDER BY created_at DESC",
                (self.server_type,)
            )
            rows = await cursor.fetchall()

            if not rows and not BACKUP_HISTORY.exists():
                # No data in DB and no JSON file — fresh start
                self._history = {"backups": []}
                logger.info("No backup history found in DB or JSON — starting fresh")
                return True

            if not rows:
                # DB empty but JSON might have data — let caller fall back
                return False

            backups = []
            for row in rows:
                # Reconstruct the backup name from filename (strip .tar.gz)
                filename = row["filename"]
                name = filename
                if name.endswith(".tar.gz"):
                    name = name[:-7]
                elif name.endswith(".tar"):
                    name = name[:-4]

                backup_path = self.backup_path / filename

                entry = {
                    "name": name,
                    "filename": filename,
                    "path": str(backup_path),
                    "size_bytes": row["size_bytes"] or 0,
                    "size_human": format_bytes(row["size_bytes"] or 0),
                    "created_at": row["created_at"] or "",
                    "created_by": "system",
                    "type": row["backup_type"] or "auto",
                    "db_id": row["id"],
                    "checksum": row["checksum"],
                    "integrity_ok": row["integrity_ok"],
                    "cloud_synced": row["cloud_synced"],
                    "cloud_synced_at": row["cloud_synced_at"],
                }
                backups.append(entry)

            self._history = {"backups": backups}
            logger.info(f"Backup history loaded from SQLite: {len(backups)} entries")
            return True

        except Exception as e:
            logger.error(f"Failed to load backup history from SQLite: {e}")
            return False

    async def _save_history(self) -> None:
        """Save backup history to SQLite backup_history table"""
        try:
            db = await get_db()

            # Get existing filenames in DB for this server_type
            cursor = await db.execute(
                "SELECT filename FROM backup_history WHERE server_type = ?",
                (self.server_type,)
            )
            existing_rows = await cursor.fetchall()
            existing_filenames = {row["filename"] for row in existing_rows}

            # Insert any new entries that are not yet in the DB
            for entry in self._history.get("backups", []):
                filename = entry.get("filename", "")
                if filename and filename not in existing_filenames:
                    backup_type = entry.get("type", "auto")
                    size_bytes = entry.get("size_bytes", 0)
                    created_at = entry.get("created_at", datetime.now().isoformat())

                    await db.execute(
                        "INSERT INTO backup_history "
                        "(server_type, backup_type, filename, size_bytes, created_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (self.server_type, backup_type, filename,
                         size_bytes, created_at)
                    )
                    existing_filenames.add(filename)

            await db.commit()
            logger.debug("Backup history saved to SQLite")

        except Exception as e:
            logger.error(f"Failed to save backup history to SQLite: {e}")

    async def _save_entry_to_db(self, entry: Dict[str, Any]) -> None:
        """Insert a single backup entry directly into the SQLite backup_history table."""
        try:
            db = await get_db()
            await db.execute(
                "INSERT INTO backup_history "
                "(server_type, backup_type, filename, size_bytes, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    self.server_type,
                    entry.get("type", "auto"),
                    entry.get("filename", ""),
                    entry.get("size_bytes", 0),
                    entry.get("created_at", datetime.now().isoformat()),
                )
            )
            await db.commit()
            logger.debug(f"Backup entry saved to SQLite: {entry.get('filename')}")
        except Exception as e:
            logger.error(f"Failed to save backup entry to SQLite: {e}")

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
            loop = asyncio.get_running_loop()
            size = await loop.run_in_executor(
                None, self._create_archive, save_dir, backup_file
            )

            if not backup_file.exists():
                return False, "Backup-Erstellung fehlgeschlagen!", None

            # Verify backup integrity
            integrity_ok = None
            if verify:
                valid, verify_msg = await self.verify_backup(backup_file)
                integrity_ok = valid
                if not valid:
                    logger.error(f"Backup verification failed: {verify_msg}")
                    backup_file.unlink(missing_ok=True)
                    return False, f"Backup erstellt aber beschaedigt: {verify_msg}", None

            # Record in history
            backup_type = "manual" if name else "auto"
            created_at = datetime.now().isoformat()
            entry = {
                "name": backup_name,
                "filename": backup_file.name,
                "path": str(backup_file),
                "size_bytes": size,
                "size_human": format_bytes(size),
                "created_at": created_at,
                "created_by": created_by,
                "type": backup_type
            }
            self._history["backups"].append(entry)

            # Write to SQLite
            await self._save_entry_to_db(entry)

            # Update integrity_ok in DB if verification was performed
            if integrity_ok is not None:
                try:
                    db = await get_db()
                    await db.execute(
                        "UPDATE backup_history SET integrity_ok = ? "
                        "WHERE server_type = ? AND filename = ?",
                        (integrity_ok, self.server_type, backup_file.name)
                    )
                    await db.commit()
                except Exception as e:
                    logger.error(f"Failed to update integrity_ok in DB: {e}")

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

                # Remove from SQLite
                try:
                    db = await get_db()
                    await db.execute(
                        "DELETE FROM backup_history "
                        "WHERE server_type = ? AND filename = ?",
                        (self.server_type, bp["filename"])
                    )
                    await db.commit()
                except Exception as e:
                    logger.error(f"Failed to remove rotated backup from DB: {e}")

            except OSError as e:
                logger.error(f"Failed to rotate backup: {e}")

        # Update in-memory history
        keep_names = {b["name"] for b in backups[:self.max_backups]}
        self._history["backups"] = [
            b for b in self._history["backups"] if b["name"] in keep_names
        ]

    def list_backups(self, max_results: int = 50) -> List[Dict[str, Any]]:
        """List all backups sorted by date (newest first).

        Uses in-memory data loaded at startup (from SQLite or JSON fallback).
        Also scans filesystem for untracked backups.
        """
        backups = []

        # Combine history with actual files
        known_files = {b["filename"] for b in self._history.get("backups", [])}

        # From in-memory history (loaded from SQLite or JSON)
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
            loop = asyncio.get_running_loop()
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
            # Validate all members and filter to prevent path traversal
            safe_members = []
            for member in tar.getmembers():
                member_path = (target_dir / member.name).resolve()
                if not str(member_path).startswith(str(target_dir.resolve())):
                    raise ValueError(f"Path traversal detected in archive: {member.name}")
                safe_members.append(member)
            tar.extractall(path=target_dir, members=safe_members)

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
                        try:
                            # Read and discard to verify integrity
                            while True:
                                chunk = f.read(65536)
                                if not chunk:
                                    break
                                total_size += len(chunk)
                        finally:
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
