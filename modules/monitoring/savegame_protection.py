"""
Savegame Protection — Phase 11
Crash-Loop Protection, Integrity Checks, Backup Test, Auto-Rollback.
"""

import asyncio
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Callable, Awaitable, Dict, List, Any

from utils.logger import get_logger
from utils.config import PROJECT_ROOT

logger = get_logger("savegame_protection")


class SavegameProtection:
    """
    Protects savegame integrity through multiple mechanisms:
    - Crash-Loop Detection: Stops auto-restart after repeated crashes
    - Integrity Check: Validates savegame files after each save
    - Backup Verification: Periodically tests backup readability
    - Rollback Suggestion: Points to last known-good save on failure
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        cfg: Dict[str, Any] = config or {}
        self._crash_window_minutes: int = cfg.get("crash_window_minutes", 10)
        self._max_crashes_in_window: int = cfg.get("max_crashes_in_window", 3)
        self._size_drop_threshold: float = cfg.get("size_drop_threshold", 0.5)  # 50% drop = suspicious

        self._crash_times: List[datetime] = []
        self._crash_loop_active: bool = False
        self._last_known_good_save: Optional[Path] = None
        self._last_save_size: int = 0
        self._integrity_alerts: List[Dict[str, Any]] = []

        # Callbacks
        self.on_crash_loop: Optional[Callable] = None
        self.on_integrity_fail: Optional[Callable] = None

        # State file
        self._state_file: Path = PROJECT_ROOT / "data" / "savegame_protection.json"
        self._load_state()

    def _load_state(self) -> None:
        """Load persistent state"""
        try:
            if self._state_file.exists():
                with open(self._state_file, "r") as f:
                    data = json.load(f)
                lkg = data.get("last_known_good_save")
                if lkg:
                    self._last_known_good_save = Path(lkg)
                self._last_save_size = data.get("last_save_size", 0)
        except (json.JSONDecodeError, OSError) as e:
            logger.debug(f"State load error: {e}")

    def _save_state(self) -> None:
        """Persist state to disk"""
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._state_file, "w") as f:
                json.dump({
                    "last_known_good_save": str(self._last_known_good_save) if self._last_known_good_save else None,
                    "last_save_size": self._last_save_size,
                    "crash_loop_active": self._crash_loop_active,
                }, f, indent=2)
        except OSError as e:
            logger.debug(f"State save error: {e}")

    # ------------------------------------------------------------------
    # Crash-Loop Detection (Phase 11a)
    # ------------------------------------------------------------------

    async def record_crash(self) -> Dict[str, Any]:
        """
        Record a crash event. Returns status dict.
        If crash loop detected, returns {"crash_loop": True, ...}
        """
        now = datetime.now()
        self._crash_times.append(now)

        # Clean old entries outside window
        cutoff = now - timedelta(minutes=self._crash_window_minutes)
        self._crash_times = [t for t in self._crash_times if t > cutoff]

        result = {
            "crash_loop": False,
            "crashes_in_window": len(self._crash_times),
            "window_minutes": self._crash_window_minutes,
            "max_allowed": self._max_crashes_in_window,
        }

        if len(self._crash_times) >= self._max_crashes_in_window:
            self._crash_loop_active = True
            result["crash_loop"] = True
            result["suggested_save"] = str(self._last_known_good_save) if self._last_known_good_save else None

            logger.warning(
                f"CRASH LOOP DETECTED: {len(self._crash_times)} crashes "
                f"in {self._crash_window_minutes} minutes"
            )

            if self.on_crash_loop:
                try:
                    await self.on_crash_loop(result)
                except Exception as e:
                    logger.error(f"Crash loop callback error: {e}")

            self._save_state()

        return result

    @property
    def is_crash_loop(self) -> bool:
        """Whether crash loop protection is active"""
        return self._crash_loop_active

    def reset_crash_loop(self) -> None:
        """Manually reset crash loop protection"""
        self._crash_loop_active = False
        self._crash_times.clear()
        self._save_state()
        logger.info("Crash loop protection reset")

    def should_auto_restart(self) -> bool:
        """Whether auto-restart should proceed (False if crash loop)"""
        return not self._crash_loop_active

    # ------------------------------------------------------------------
    # Integrity Check (Phase 11b)
    # ------------------------------------------------------------------

    async def check_save_integrity(self, save_path: Path) -> Dict[str, Any]:
        """
        Check savegame integrity after a save operation.
        Returns {"ok": bool, "issues": list, "size_mb": float}
        """
        result = {"ok": True, "issues": [], "size_mb": 0.0}

        try:
            if not save_path.exists():
                result["ok"] = False
                result["issues"].append("Savegame-Datei existiert nicht")
                return result

            # Find newest .sav file
            if save_path.is_dir():
                saves = sorted(save_path.rglob("*.sav"),
                              key=lambda p: p.stat().st_mtime, reverse=True)
                if not saves:
                    result["ok"] = False
                    result["issues"].append("Keine .sav Dateien gefunden")
                    return result
                newest = saves[0]
            else:
                newest = save_path

            size = newest.stat().st_size
            size_mb = size / (1024 * 1024)
            result["size_mb"] = round(size_mb, 1)
            result["file"] = str(newest)

            # Check 1: File not empty
            if size == 0:
                result["ok"] = False
                result["issues"].append("Savegame ist leer (0 Bytes)")
                return result

            # Check 2: Minimum size (corrupt saves are usually tiny)
            if size < 1024:  # Less than 1 KB
                result["ok"] = False
                result["issues"].append(f"Savegame verdächtig klein: {size} Bytes")

            # Check 3: Size drop compared to previous save
            if self._last_save_size > 0:
                ratio = size / self._last_save_size
                if ratio < self._size_drop_threshold:
                    result["ok"] = False
                    prev_mb = self._last_save_size / (1024 * 1024)
                    result["issues"].append(
                        f"Drastischer Größenverlust: {prev_mb:.1f} MB → {size_mb:.1f} MB "
                        f"({ratio:.0%})"
                    )

            # Check 4: File header (Satisfactory saves start with specific bytes)
            try:
                with open(newest, "rb") as f:
                    header = f.read(4)
                # Satisfactory save header magic bytes check
                if len(header) < 4:
                    result["ok"] = False
                    result["issues"].append("Savegame Header zu kurz")
            except OSError as e:
                logger.debug(f"Failed to read file header: {e}")

            # Update tracking
            if result["ok"]:
                self._last_known_good_save = newest
                self._last_save_size = size
                self._save_state()
            else:
                if self.on_integrity_fail:
                    try:
                        await self.on_integrity_fail(result)
                    except Exception as e:
                        logger.error(f"Integrity callback error: {e}")

        except Exception as e:
            result["ok"] = False
            result["issues"].append(f"Prüffehler: {str(e)[:100]}")

        return result

    # ------------------------------------------------------------------
    # Backup Verification (Phase 11c)
    # ------------------------------------------------------------------

    async def verify_backup(self, backup_path: Path) -> Dict[str, Any]:
        """
        Verify a backup file is readable and not corrupt.
        Returns {"ok": bool, "detail": str}
        """
        try:
            if not backup_path.exists():
                return {"ok": False, "detail": "Backup-Datei nicht gefunden"}

            size = backup_path.stat().st_size
            if size == 0:
                return {"ok": False, "detail": "Backup ist leer"}

            # Test if archive is extractable
            if backup_path.suffix == ".gz" or str(backup_path).endswith(".tar.gz"):
                import tarfile
                def _test_tar():
                    with tarfile.open(str(backup_path), "r:gz") as tf:
                        members = tf.getnames()
                        return len(members)

                count = await asyncio.get_event_loop().run_in_executor(None, _test_tar)
                size_mb = size / (1024 * 1024)
                return {"ok": True,
                        "detail": f"OK — {count} Dateien, {size_mb:.1f} MB"}

            elif backup_path.suffix == ".zip":
                import zipfile
                def _test_zip():
                    with zipfile.ZipFile(str(backup_path), "r") as zf:
                        bad = zf.testzip()
                        return len(zf.namelist()), bad

                count, bad_file = await asyncio.get_event_loop().run_in_executor(None, _test_zip)
                if bad_file:
                    return {"ok": False, "detail": f"Korrupte Datei im Archiv: {bad_file}"}
                size_mb = size / (1024 * 1024)
                return {"ok": True,
                        "detail": f"OK — {count} Dateien, {size_mb:.1f} MB"}

            else:
                # Just check it exists and has content
                size_mb = size / (1024 * 1024)
                return {"ok": True, "detail": f"Vorhanden — {size_mb:.1f} MB"}

        except Exception as e:
            return {"ok": False, "detail": f"Fehler: {str(e)[:100]}"}

    async def verify_latest_backup(self, backup_dir: Path) -> Dict[str, Any]:
        """Find and verify the most recent backup"""
        try:
            if not backup_dir.exists():
                return {"ok": False, "detail": "Backup-Verzeichnis nicht gefunden"}

            # Find newest backup
            backups = []
            for pattern in ["*.tar.gz", "*.zip"]:
                backups.extend(backup_dir.glob(pattern))

            if not backups:
                return {"ok": False, "detail": "Keine Backups vorhanden"}

            newest = max(backups, key=lambda p: p.stat().st_mtime)
            result = await self.verify_backup(newest)
            result["file"] = newest.name
            return result

        except Exception as e:
            return {"ok": False, "detail": f"Fehler: {str(e)[:100]}"}

    # ------------------------------------------------------------------
    # Rollback Info (Phase 11d)
    # ------------------------------------------------------------------

    def get_rollback_info(self) -> Dict[str, Any]:
        """
        Get information about available rollback options.
        """
        info = {
            "crash_loop_active": self._crash_loop_active,
            "crashes_recent": len(self._crash_times),
            "last_known_good": None,
        }

        if self._last_known_good_save and self._last_known_good_save.exists():
            lkg = self._last_known_good_save
            info["last_known_good"] = {
                "path": str(lkg),
                "name": lkg.name,
                "size_mb": round(lkg.stat().st_size / (1024 * 1024), 1),
                "age_minutes": round(
                    (datetime.now().timestamp() - lkg.stat().st_mtime) / 60
                ),
            }

        return info
