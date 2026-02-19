"""
Server Statistics Tracker
Persists historical data for reports: uptime, peak players, savegame sizes, crashes
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Any

from utils.logger import get_logger

logger = get_logger("stats_tracker")


class StatsTracker:
    """
    Tracks server statistics over time for weekly/monthly reports.
    Data is persisted to JSON and survives bot restarts.

    Tracked data:
    - Uptime checks (online/offline per check interval)
    - Peak concurrent players
    - Savegame file sizes
    - Crash timestamps
    """

    def __init__(self, data_dir: Path) -> None:
        self.data_dir: Path = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.data_file: Path = data_dir / "stats_history.json"

        self._data: Dict[str, Any] = {
            "uptime_checks": [],      # {"ts": iso, "online": bool}
            "player_counts": [],      # {"ts": iso, "count": int}
            "savegame_sizes": [],     # {"ts": iso, "size_mb": float}
            "crashes": [],            # {"ts": iso, "number": int}
        }

        self._load()

    def _load(self) -> None:
        """Load history from disk"""
        try:
            if self.data_file.exists():
                with open(self.data_file, "r") as f:
                    loaded = json.load(f)
                # Merge with defaults for new keys
                for key in self._data:
                    if key in loaded:
                        self._data[key] = loaded[key]
                logger.info(f"Stats history loaded ({len(self._data['uptime_checks'])} uptime records)")
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to load stats history: {e}")

    def _save(self) -> None:
        """Save history to disk"""
        try:
            with open(self.data_file, "w") as f:
                json.dump(self._data, f, ensure_ascii=False)
        except OSError as e:
            logger.error(f"Failed to save stats history: {e}")

    def _cleanup_old(self, days: int = 90) -> None:
        """Remove records older than N days"""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        for key in self._data:
            if isinstance(self._data[key], list):
                self._data[key] = [
                    r for r in self._data[key]
                    if r.get("ts", "") >= cutoff
                ]

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_uptime_check(self, is_online: bool) -> None:
        """Record a single uptime check result"""
        self._data["uptime_checks"].append({
            "ts": datetime.now().isoformat(),
            "online": is_online,
        })
        # Cleanup periodically (every 1000 records)
        if len(self._data["uptime_checks"]) % 1000 == 0:
            self._cleanup_old()
        self._save()

    def record_player_count(self, count: int) -> None:
        """Record current player count"""
        self._data["player_counts"].append({
            "ts": datetime.now().isoformat(),
            "count": count,
        })
        self._save()

    def record_savegame_size(self, size_mb: float) -> None:
        """Record savegame file size"""
        self._data["savegame_sizes"].append({
            "ts": datetime.now().isoformat(),
            "size_mb": round(size_mb, 2),
        })
        self._save()

    def record_crash(self, crash_number: int) -> None:
        """Record a crash event"""
        self._data["crashes"].append({
            "ts": datetime.now().isoformat(),
            "number": crash_number,
        })
        self._save()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_uptime_percent(self, days: int = 7) -> float:
        """Calculate uptime percentage over the last N days"""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        checks = [r for r in self._data["uptime_checks"] if r["ts"] >= cutoff]

        if not checks:
            return 0.0

        online = sum(1 for c in checks if c["online"])
        return round((online / len(checks)) * 100, 1)

    def get_peak_players(self, days: int = 7) -> int:
        """Get peak concurrent player count over the last N days"""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        counts = [
            r["count"] for r in self._data["player_counts"]
            if r["ts"] >= cutoff
        ]
        return max(counts) if counts else 0

    def get_savegame_growth(self, days: int = 7) -> Optional[Dict[str, Any]]:
        """
        Get savegame size trend over the last N days.
        Returns dict with start_mb, end_mb, growth_mb, growth_percent
        """
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        sizes = [
            r for r in self._data["savegame_sizes"]
            if r["ts"] >= cutoff
        ]

        if len(sizes) < 2:
            return None

        start = sizes[0]["size_mb"]
        end = sizes[-1]["size_mb"]
        growth = end - start
        growth_pct = (growth / start * 100) if start > 0 else 0

        return {
            "start_mb": round(start, 1),
            "end_mb": round(end, 1),
            "growth_mb": round(growth, 1),
            "growth_percent": round(growth_pct, 1),
        }

    def get_crashes(self, days: int = 7) -> List[Dict[str, Any]]:
        """Get crash events over the last N days"""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        return [c for c in self._data["crashes"] if c["ts"] >= cutoff]

    def get_total_checks(self, days: int = 7) -> int:
        """Total uptime checks in period"""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        return len([r for r in self._data["uptime_checks"] if r["ts"] >= cutoff])

    def check_savegame_trend(self, warn_growth_mb: float = 500,
                              warn_growth_pct: float = 50,
                              days: int = 7) -> Optional[Dict[str, Any]]:
        """
        Check if savegame growth exceeds thresholds.
        Returns warning dict if threshold exceeded, None otherwise.
        """
        growth = self.get_savegame_growth(days)
        if not growth:
            return None

        warnings = []
        if growth["growth_mb"] > warn_growth_mb:
            warnings.append(
                f"Savegame wuchs um {growth['growth_mb']:.0f} MB "
                f"in {days} Tagen (Schwelle: {warn_growth_mb} MB)"
            )
        if growth["growth_percent"] > warn_growth_pct:
            warnings.append(
                f"Savegame wuchs um {growth['growth_percent']:.0f}% "
                f"in {days} Tagen (Schwelle: {warn_growth_pct}%)"
            )

        if not warnings:
            return None

        return {
            "warnings": warnings,
            "current_mb": growth["end_mb"],
            "growth_mb": growth["growth_mb"],
            "growth_pct": growth["growth_percent"],
            "days": days,
        }
