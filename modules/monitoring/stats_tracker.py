"""
Server Statistics Tracker
Persists historical data for reports: uptime, peak players, savegame sizes, crashes

Persistenz: SQLite server_stats_tracker Tabelle (alleinige Datenquelle)
"""

import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any

from utils.logger import get_logger
from modules.database.db_manager import get_db

logger = get_logger("stats_tracker")


class StatsTracker:
    """
    Tracks server statistics over time for weekly/monthly reports.
    Data is persisted to SQLite and survives bot restarts.

    Tracked data:
    - Uptime checks (online/offline per check interval)
    - Peak concurrent players
    - Savegame file sizes
    - Crash timestamps
    """

    def __init__(self, server_type: str = "sat",
                 server_id: Optional[str] = None,
                 **kwargs) -> None:
        """
        Args:
            server_type: "sat", "mc" etc.
            server_id: Optionale Server-ID (z.B. "vanilla", "bmc")
        """
        self.server_type = server_type
        self.server_id = server_id

        self._data: Dict[str, Any] = {
            "uptime_checks": [],      # {"ts": iso, "online": bool}
            "player_counts": [],      # {"ts": iso, "count": int}
            "savegame_sizes": [],     # {"ts": iso, "size_mb": float}
            "crashes": [],            # {"ts": iso, "number": int}
        }

    async def load_from_db(self) -> None:
        """Load historical data from SQLite server_stats_tracker table."""
        try:
            db = await get_db()

            # Nur die letzten 90 Tage laden (wie vorher cleanup_old)
            cutoff = (datetime.now() - timedelta(days=90)).isoformat()

            # Uptime-Checks laden
            cursor = await db.execute(
                "SELECT timestamp, value_int FROM server_stats_tracker "
                "WHERE server_type = ? AND metric_type = 'uptime' "
                "AND (server_id = ? OR (server_id IS NULL AND ? IS NULL)) "
                "AND timestamp >= ? ORDER BY timestamp ASC",
                (self.server_type, self.server_id, self.server_id, cutoff)
            )
            rows = await cursor.fetchall()
            self._data["uptime_checks"] = [
                {"ts": row[0], "online": bool(row[1])} for row in rows
            ]

            # Player-Counts laden
            cursor = await db.execute(
                "SELECT timestamp, value_int FROM server_stats_tracker "
                "WHERE server_type = ? AND metric_type = 'player_count' "
                "AND (server_id = ? OR (server_id IS NULL AND ? IS NULL)) "
                "AND timestamp >= ? ORDER BY timestamp ASC",
                (self.server_type, self.server_id, self.server_id, cutoff)
            )
            rows = await cursor.fetchall()
            self._data["player_counts"] = [
                {"ts": row[0], "count": row[1] or 0} for row in rows
            ]

            # Savegame-Sizes laden
            cursor = await db.execute(
                "SELECT timestamp, value_real FROM server_stats_tracker "
                "WHERE server_type = ? AND metric_type = 'savegame_size' "
                "AND (server_id = ? OR (server_id IS NULL AND ? IS NULL)) "
                "AND timestamp >= ? ORDER BY timestamp ASC",
                (self.server_type, self.server_id, self.server_id, cutoff)
            )
            rows = await cursor.fetchall()
            self._data["savegame_sizes"] = [
                {"ts": row[0], "size_mb": row[1] or 0.0} for row in rows
            ]

            # Crashes laden
            cursor = await db.execute(
                "SELECT timestamp, value_int FROM server_stats_tracker "
                "WHERE server_type = ? AND metric_type = 'crash' "
                "AND (server_id = ? OR (server_id IS NULL AND ? IS NULL)) "
                "AND timestamp >= ? ORDER BY timestamp ASC",
                (self.server_type, self.server_id, self.server_id, cutoff)
            )
            rows = await cursor.fetchall()
            self._data["crashes"] = [
                {"ts": row[0], "number": row[1] or 0} for row in rows
            ]

            total = (len(self._data["uptime_checks"]) +
                     len(self._data["player_counts"]) +
                     len(self._data["savegame_sizes"]) +
                     len(self._data["crashes"]))
            logger.info(
                f"StatsTracker geladen aus SQLite: {total} Einträge "
                f"(server_type={self.server_type}, server_id={self.server_id})"
            )

        except Exception as e:
            logger.warning(f"StatsTracker SQLite-Lesefehler: {e}")

    # ------------------------------------------------------------------
    # Fire-and-forget DB write helper
    # ------------------------------------------------------------------

    def _fire_and_forget_insert(self, metric_type: str,
                                 value_int: Optional[int] = None,
                                 value_real: Optional[float] = None) -> None:
        """Schedule an async DB insert from synchronous code."""
        ts = datetime.now().isoformat()
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self._db_insert(metric_type, ts, value_int, value_real))
            else:
                logger.debug("Event loop not running, skipping DB write for stats_tracker")
        except RuntimeError:
            logger.debug("No event loop available, skipping DB write for stats_tracker")

    async def _db_insert(self, metric_type: str, timestamp: str,
                          value_int: Optional[int], value_real: Optional[float]) -> None:
        """Insert a single metric into server_stats_tracker."""
        try:
            db = await get_db()
            await db.execute(
                "INSERT INTO server_stats_tracker "
                "(server_type, server_id, metric_type, timestamp, value_int, value_real) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (self.server_type, self.server_id, metric_type,
                 timestamp, value_int, value_real)
            )
            await db.commit()
        except Exception as e:
            logger.error(f"SQLite write failed for stats_tracker {metric_type}: {e}")

    async def _db_cleanup_old(self, days: int = 90) -> None:
        """Remove records older than N days from SQLite."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        try:
            db = await get_db()
            await db.execute(
                "DELETE FROM server_stats_tracker "
                "WHERE server_type = ? "
                "AND (server_id = ? OR (server_id IS NULL AND ? IS NULL)) "
                "AND timestamp < ?",
                (self.server_type, self.server_id, self.server_id, cutoff)
            )
            await db.commit()
        except Exception as e:
            logger.error(f"SQLite cleanup failed for stats_tracker: {e}")

    def _cleanup_old(self, days: int = 90) -> None:
        """Remove records older than N days from in-memory data."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        for key in self._data:
            if isinstance(self._data[key], list):
                self._data[key] = [
                    r for r in self._data[key]
                    if r.get("ts", "") >= cutoff
                ]
        # Also schedule DB cleanup
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self._db_cleanup_old(days))
        except RuntimeError:
            pass

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
        self._fire_and_forget_insert("uptime", value_int=1 if is_online else 0)

    def record_player_count(self, count: int) -> None:
        """Record current player count"""
        self._data["player_counts"].append({
            "ts": datetime.now().isoformat(),
            "count": count,
        })
        if len(self._data["player_counts"]) % 1000 == 0:
            self._cleanup_old()
        self._fire_and_forget_insert("player_count", value_int=count)

    def record_savegame_size(self, size_mb: float) -> None:
        """Record savegame/world file size"""
        self._data["savegame_sizes"].append({
            "ts": datetime.now().isoformat(),
            "size_mb": round(size_mb, 2),
        })
        if len(self._data["savegame_sizes"]) % 1000 == 0:
            self._cleanup_old()
        self._fire_and_forget_insert("savegame_size", value_real=round(size_mb, 2))

    # Alias fuer MC-Nutzung (World-Groesse statt Savegame)
    record_world_size = record_savegame_size

    def record_crash(self, crash_number: int) -> None:
        """Record a crash event"""
        self._data["crashes"].append({
            "ts": datetime.now().isoformat(),
            "number": crash_number,
        })
        if len(self._data["crashes"]) % 100 == 0:
            self._cleanup_old()
        self._fire_and_forget_insert("crash", value_int=crash_number)

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
