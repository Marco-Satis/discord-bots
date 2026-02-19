"""
Server Optimizer Module
Handles recurring system optimizations for Satisfactory game server.
- Satisfactory process priority (renice)
- Cache cleanup when memory is high
- Temp file cleanup
- Log rotation trigger
"""

import asyncio
import subprocess
import psutil
from datetime import datetime, timedelta
from typing import Optional
from utils.logger import get_logger

logger = get_logger("monitor.optimizer")


class ServerOptimizer:
    """Manages recurring server optimizations"""

    def __init__(self, config: dict):
        self.config = config
        thresholds = config.get("thresholds", {})
        self.cpu_threshold = thresholds.get("cpu_warning", 80)
        self.ram_threshold = thresholds.get("ram_warning", 85)
        self.disk_threshold = thresholds.get("disk_warning", 90)

        self.last_optimization: Optional[datetime] = None
        self.optimization_count: int = 0
        self.cooldown_minutes: int = 10  # Min time between auto-optimizations

    # ------------------------------------------------------------------
    # Satisfactory Process Priority
    # ------------------------------------------------------------------

    async def set_satisfactory_priority(self) -> tuple[bool, str]:
        """Set Satisfactory process to high priority (nice -10)"""
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["pgrep", "-f", "FactoryServer"],
                capture_output=True, text=True, timeout=5,
            )

            if result.returncode != 0 or not result.stdout.strip():
                return False, "Satisfactory-Prozess nicht gefunden"

            pids = result.stdout.strip().split("\n")
            renice_results = []

            for pid in pids:
                pid = pid.strip()
                if not pid:
                    continue
                try:
                    proc = psutil.Process(int(pid))
                    current_nice = proc.nice()

                    if current_nice > -10:
                        renice = await asyncio.to_thread(
                            subprocess.run,
                            ["sudo", "renice", "-10", "-p", pid],
                            capture_output=True, text=True, timeout=5,
                        )
                        if renice.returncode == 0:
                            renice_results.append(
                                f"PID {pid}: nice {current_nice} → -10"
                            )
                        else:
                            renice_results.append(
                                f"PID {pid}: renice fehlgeschlagen"
                            )
                    else:
                        renice_results.append(
                            f"PID {pid}: bereits nice {current_nice}"
                        )
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    renice_results.append(f"PID {pid}: Zugriff verweigert")

            msg = "\n".join(renice_results) if renice_results else "Keine PIDs"
            logger.info(f"Priority set: {msg}")
            return True, msg

        except Exception as e:
            logger.error(f"Error setting priority: {e}")
            return False, str(e)

    # ------------------------------------------------------------------
    # Cache Cleanup
    # ------------------------------------------------------------------

    async def clear_system_caches(self) -> tuple[bool, str]:
        """Clear system caches (page cache, dentries, inodes)"""
        try:
            # sync first
            await asyncio.to_thread(
                subprocess.run, ["sync"], timeout=10
            )

            # Drop caches via dedicated script (avoids bash -c injection risk)
            result = await asyncio.to_thread(
                subprocess.run,
                ["sudo", "/usr/local/bin/drop-caches.sh"],
                capture_output=True, text=True, timeout=10,
            )

            if result.returncode == 0:
                logger.info("System caches cleared")
                return True, "System-Caches geleert"
            else:
                return False, f"Fehler: {result.stderr}"

        except Exception as e:
            logger.error(f"Error clearing caches: {e}")
            return False, str(e)

    # ------------------------------------------------------------------
    # Temp File Cleanup
    # ------------------------------------------------------------------

    async def clean_temp_files(self) -> tuple[bool, str]:
        """Clean old temp files and logs"""
        cleaned = 0

        try:
            # Clean /tmp files older than 7 days
            result = await asyncio.to_thread(
                subprocess.run,
                [
                    "sudo", "find", "/tmp", "-type", "f",
                    "-atime", "+7", "-delete",
                ],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                cleaned += 1

            # Clean old journal logs (keep 3 days)
            result = await asyncio.to_thread(
                subprocess.run,
                ["sudo", "journalctl", "--vacuum-time=3d"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                cleaned += 1

            msg = f"Temp-Dateien + alte Logs bereinigt ({cleaned} Aktionen)"
            logger.info(msg)
            return True, msg

        except Exception as e:
            logger.error(f"Error cleaning temp files: {e}")
            return False, str(e)

    # ------------------------------------------------------------------
    # Full Optimization
    # ------------------------------------------------------------------

    async def run_full_optimization(self) -> dict:
        """Run all optimizations and return results"""
        results = {}

        # 1. Set Satisfactory priority
        success, msg = await self.set_satisfactory_priority()
        results["priority"] = {"success": success, "message": msg}

        # 2. Check if cache clearing is needed
        mem = psutil.virtual_memory()
        if mem.percent > self.ram_threshold:
            success, msg = await self.clear_system_caches()
            results["cache"] = {"success": success, "message": msg}
        else:
            results["cache"] = {
                "success": True,
                "message": f"RAM bei {mem.percent:.0f}% — kein Cache-Clear nötig",
            }

        # 3. Check disk and clean if needed
        disk = psutil.disk_usage("/")
        if disk.percent > self.disk_threshold:
            success, msg = await self.clean_temp_files()
            results["cleanup"] = {"success": success, "message": msg}
        else:
            results["cleanup"] = {
                "success": True,
                "message": f"Disk bei {disk.percent:.0f}% — kein Cleanup nötig",
            }

        self.last_optimization = datetime.now()
        self.optimization_count += 1

        logger.info(
            f"Full optimization #{self.optimization_count} completed: "
            f"{sum(1 for r in results.values() if r['success'])}/{len(results)} OK"
        )

        return results

    # ------------------------------------------------------------------
    # Auto-Optimization Check
    # ------------------------------------------------------------------

    async def check_and_optimize(self) -> Optional[dict]:
        """Check if optimization is needed and run if so.
        Returns results dict if optimization was run, None otherwise.
        """
        # Cooldown check
        if self.last_optimization:
            elapsed = datetime.now() - self.last_optimization
            if elapsed < timedelta(minutes=self.cooldown_minutes):
                return None

        cpu = psutil.cpu_percent(interval=1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")

        needs_opt = (
            cpu > self.cpu_threshold
            or mem.percent > self.ram_threshold
            or disk.percent > self.disk_threshold
        )

        if needs_opt:
            reasons = []
            if cpu > self.cpu_threshold:
                reasons.append(f"CPU: {cpu:.0f}% > {self.cpu_threshold}%")
            if mem.percent > self.ram_threshold:
                reasons.append(
                    f"RAM: {mem.percent:.0f}% > {self.ram_threshold}%"
                )
            if disk.percent > self.disk_threshold:
                reasons.append(
                    f"Disk: {disk.percent:.0f}% > {self.disk_threshold}%"
                )

            logger.warning(f"Auto-optimization triggered: {', '.join(reasons)}")
            results = await self.run_full_optimization()
            results["triggers"] = reasons
            return results

        # Always keep Satisfactory priority high (lightweight check)
        await self.set_satisfactory_priority()
        return None

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        """Get optimizer status"""
        return {
            "optimization_count": self.optimization_count,
            "last_optimization": (
                self.last_optimization.isoformat()
                if self.last_optimization
                else None
            ),
            "thresholds": {
                "cpu": self.cpu_threshold,
                "ram": self.ram_threshold,
                "disk": self.disk_threshold,
            },
        }
