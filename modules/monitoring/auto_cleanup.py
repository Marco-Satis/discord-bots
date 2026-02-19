"""
Auto-Cleanup Module — Log rotation, crash replay cleanup, backup rotation
Phase 5: Automatic cleanup of old files to prevent disk space issues.
Also integrates Phase 4c disk monitoring.
"""

import asyncio
import gzip
import shutil
import psutil
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any

from utils.logger import get_logger
from utils.config import PROJECT_ROOT

logger = get_logger("auto_cleanup")


class AutoCleanup:
    """
    Manages automatic cleanup of logs, crash replays, and old backups.
    Also provides disk-space monitoring (Phase 4c).
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        cfg: Dict[str, Any] = config or {}
        self.log_dir: Path = PROJECT_ROOT / "logs"
        self.crash_replay_dir: Path = PROJECT_ROOT / "data" / "crash_replays"
        self.backup_dir: Path = Path(cfg.get("backup_path", str(PROJECT_ROOT / "backups")))
        self.settings_backup_dir: Path = PROJECT_ROOT / "data" / "settings_backups"
        self.weekly_snapshot_dir: Path = PROJECT_ROOT / "data" / "weekly_snapshots"

        # Retention settings (days)
        retention: Dict[str, Any] = cfg.get("retention", {})
        self.log_max_age_days: int = retention.get("logs", 30)
        self.crash_replay_max: int = retention.get("crash_replays", 20)
        self.backup_max_age_days: int = retention.get("backups", 30)
        self.settings_max: int = retention.get("settings_backups", 30)
        self.snapshot_max: int = retention.get("weekly_snapshots", 52)  # 1 year
        self.compress_logs_after_days: int = retention.get("compress_after", 3)

        # Disk thresholds
        self.disk_warning_percent: int = cfg.get("thresholds", {}).get("disk_warning", 90)
        self.disk_critical_percent: int = cfg.get("thresholds", {}).get("disk_critical", 95)

    async def run_cleanup(self) -> Dict[str, Any]:
        """
        Run all cleanup tasks.
        Returns summary of actions taken.
        """
        summary = {
            "logs_compressed": 0,
            "logs_deleted": 0,
            "crash_replays_deleted": 0,
            "backups_deleted": 0,
            "settings_deleted": 0,
            "snapshots_deleted": 0,
            "space_freed_mb": 0.0,
        }

        try:
            # 1. Compress old logs
            compressed = await self._compress_old_logs()
            summary["logs_compressed"] = compressed

            # 2. Delete very old logs
            deleted = await self._delete_old_files(
                self.log_dir, ["*.log.gz", "*.log.*.gz"],
                max_age_days=self.log_max_age_days * 2
            )
            summary["logs_deleted"] = deleted

            # 3. Trim crash replays
            trimmed = self._trim_directory(
                self.crash_replay_dir, "crash_*.log", self.crash_replay_max
            )
            summary["crash_replays_deleted"] = trimmed

            # 4. Old backups
            deleted = await self._delete_old_files(
                self.backup_dir, ["*.tar.gz", "*.zip"],
                max_age_days=self.backup_max_age_days
            )
            summary["backups_deleted"] = deleted

            # 5. Settings backups
            trimmed = self._trim_directory(
                self.settings_backup_dir, "settings_*.json", self.settings_max
            )
            summary["settings_deleted"] = trimmed

            # 6. Weekly snapshots
            trimmed = self._trim_directory(
                self.weekly_snapshot_dir, "snapshot_*.json", self.snapshot_max
            )
            summary["snapshots_deleted"] = trimmed

            logger.info(
                f"Cleanup complete: {summary['logs_compressed']} compressed, "
                f"{summary['logs_deleted'] + summary['crash_replays_deleted'] + summary['backups_deleted']} deleted"
            )
        except OSError as e:
            logger.error(f"Cleanup error: {e}", exc_info=True)

        return summary

    async def _compress_old_logs(self) -> int:
        """Compress .log files older than N days"""
        count = 0
        cutoff = datetime.now() - timedelta(days=self.compress_logs_after_days)

        if not self.log_dir.exists():
            return 0

        def _do_compress():
            nonlocal count
            for log_file in self.log_dir.glob("*.log"):
                # Skip current log files (those without .N suffix)
                # Only compress rotated logs: *.log.1, *.log.2 etc.
                pass

            for log_file in self.log_dir.glob("*.log.[0-9]*"):
                try:
                    if log_file.suffix == ".gz":
                        continue
                    mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
                    if mtime < cutoff:
                        gz_path = log_file.with_suffix(log_file.suffix + ".gz")
                        with open(log_file, 'rb') as f_in:
                            with gzip.open(gz_path, 'wb') as f_out:
                                shutil.copyfileobj(f_in, f_out)
                        log_file.unlink()
                        count += 1
                except OSError as e:
                    logger.debug(f"Compress error for {log_file}: {e}")

        await asyncio.get_event_loop().run_in_executor(None, _do_compress)
        return count

    async def _delete_old_files(self, directory: Path, patterns: List[str],
                                 max_age_days: int) -> int:
        """Delete files matching patterns older than max_age_days"""
        count = 0
        if not directory.exists():
            return 0

        cutoff = datetime.now() - timedelta(days=max_age_days)

        def _do_delete():
            nonlocal count
            for pattern in patterns:
                for f in directory.glob(pattern):
                    try:
                        mtime = datetime.fromtimestamp(f.stat().st_mtime)
                        if mtime < cutoff:
                            f.unlink()
                            count += 1
                    except OSError:
                        pass

        await asyncio.get_event_loop().run_in_executor(None, _do_delete)
        return count

    def _trim_directory(self, directory: Path, pattern: str, max_files: int) -> int:
        """Keep only the newest max_files matching pattern"""
        if not directory.exists():
            return 0

        try:
            files = sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime)
            deleted = 0
            while len(files) > max_files:
                oldest = files.pop(0)
                oldest.unlink()
                deleted += 1
            return deleted
        except OSError as e:
            logger.debug(f"Trim error for {directory}: {e}")
            return 0

    # ------------------------------------------------------------------
    # Disk Monitoring (Phase 4c)
    # ------------------------------------------------------------------

    async def check_disk_space(self) -> Dict[str, Any]:
        """
        Check disk space and return status.
        Returns dict with usage info and alert level.
        """
        disk = psutil.disk_usage("/")

        result = {
            "total_gb": round(disk.total / (1024**3), 1),
            "used_gb": round(disk.used / (1024**3), 1),
            "free_gb": round(disk.free / (1024**3), 1),
            "percent": disk.percent,
            "alert": None,
        }

        # Check savegame size separately
        savegame_size_mb = await self._get_savegame_size()
        result["savegame_mb"] = savegame_size_mb

        if disk.percent >= self.disk_critical_percent:
            result["alert"] = "critical"
        elif disk.percent >= self.disk_warning_percent:
            result["alert"] = "warning"

        return result

    async def _get_savegame_size(self) -> float:
        """Get total size of savegame files in MB"""
        try:
            save_paths = [
                Path("/home/satisfactory/.config/Epic/FactoryGame/Saved/SaveGames"),
            ]
            total = 0
            for save_path in save_paths:
                if save_path.exists():
                    for f in save_path.rglob("*.sav"):
                        total += f.stat().st_size
            return round(total / (1024 * 1024), 1)
        except OSError:
            return 0.0

    async def emergency_cleanup(self) -> str:
        """
        Emergency cleanup when disk is critically full.
        Aggressively removes old files.
        """
        actions = []

        # Delete old compressed logs
        deleted = await self._delete_old_files(self.log_dir, ["*.gz"], max_age_days=7)
        if deleted:
            actions.append(f"Alte Logs geloescht: {deleted}")

        # Trim crash replays to minimum
        trimmed = self._trim_directory(self.crash_replay_dir, "crash_*.log", 5)
        if trimmed:
            actions.append(f"Crash-Replays reduziert: {trimmed}")

        # Trim old backups aggressively
        trimmed = self._trim_directory(self.backup_dir, "*.tar.gz", 5)
        if trimmed:
            actions.append(f"Alte Backups reduziert: {trimmed}")

        return "\n".join(actions) if actions else "Keine Dateien zum Bereinigen gefunden."
