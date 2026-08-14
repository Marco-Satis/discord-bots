"""
Server Statistics Tracker
Persists historical data for reports: uptime, peak players, savegame sizes, crashes

Persistenz: SQLite server_stats_tracker Tabelle (alleinige Datenquelle)

Speicher-Umbau 2026-08-14
-------------------------
Bis dahin hielt jeder Tracker seine kompletten 90 Tage Rohdaten zusätzlich als
Python-Listen im Speicher — bei vier Trackern (SAT plus drei MC-Server) und
einem Messwert alle fünf Minuten summierte sich das auf rund 68 MB. Der
recon-bot läuft unter `MemoryMax=768M`; das war ein Fünftel davon für Daten,
die vollständig in der Datenbank stehen.

Jetzt schreiben die `record_*`-Methoden nur noch (unverändert per
Fire-and-Forget), und die Auswertung fragt die Datenbank. Die Abfragen sind
Aggregate über `idx_sst_lookup` (COUNT, SUM, MAX, je ein Wert am Rand) — sie
lesen keine Rohdaten in den Prozess zurück, sondern lassen SQLite rechnen.

Preis dafür: die Getter sind async. Alle Aufrufer stehen ohnehin in
async-Funktionen (cogs/monitor_cog.py).
"""

from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any

from utils.logger import get_logger
from utils.async_tasks import schedule_from_sync
from modules.database.db_manager import get_db, get_read_db

logger = get_logger("stats_tracker")

# Nach so vielen neuen Messwerten wird der Aufräum-Lauf angestoßen. Vorher
# hing die Schwelle an der Länge der Speicherliste; ohne die Listen zählt der
# Tracker selbst mit. Der Aufräum-Lauf löscht ohnehin über alle Metriken
# dieses Servers hinweg, deshalb genügt ein gemeinsamer Zähler.
AUFRAEUM_INTERVALL = 1000


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
        self._seit_aufraeumen = 0

    # ------------------------------------------------------------------
    # Gemeinsame Filter-Bausteine
    # ------------------------------------------------------------------

    def _filter(self, metric_type: str, days: int) -> tuple:
        """
        WHERE-Klausel plus Parameter für eine Metrik im Zeitfenster.

        Die server_id-Prüfung als OR-Paar ist nötig, weil SAT sie NULL lässt
        und `NULL = NULL` in SQL nicht wahr ist.
        """
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        klausel = (
            "server_type = ? AND metric_type = ? "
            "AND (server_id = ? OR (server_id IS NULL AND ? IS NULL)) "
            "AND timestamp >= ?"
        )
        params = (self.server_type, metric_type,
                  self.server_id, self.server_id, cutoff)
        return klausel, params

    async def _eine_zeile(self, sql: str, params: tuple) -> Optional[tuple]:
        """Eine Abfrage über den Lese-Pool, Fehler werden geloggt statt geworfen."""
        try:
            conn = await get_read_db()
            cursor = await conn.execute(sql, params)
            return await cursor.fetchone()
        except Exception as e:
            logger.warning(f"StatsTracker-Abfrage fehlgeschlagen: {e}")
            return None

    async def load_from_db(self) -> None:
        """
        Meldet, wie viele Messwerte für diesen Server vorliegen.

        Früher las diese Methode die Rohdaten in den Speicher. Das entfällt —
        sie bleibt als Start-Diagnose erhalten, damit im Log weiterhin sichtbar
        ist, ob ein Tracker Daten hat (und der Aufrufer in bots/recon_bot.py
        unverändert bleibt).
        """
        zeile = await self._eine_zeile(
            "SELECT COUNT(*) FROM server_stats_tracker "
            "WHERE server_type = ? "
            "AND (server_id = ? OR (server_id IS NULL AND ? IS NULL))",
            (self.server_type, self.server_id, self.server_id),
        )
        anzahl = zeile[0] if zeile else 0
        logger.info(
            f"StatsTracker bereit: {anzahl} Einträge in der Datenbank "
            f"(server_type={self.server_type}, server_id={self.server_id})"
        )

    # ------------------------------------------------------------------
    # Fire-and-forget DB write helper
    # ------------------------------------------------------------------

    def _fire_and_forget_insert(self, metric_type: str,
                                 value_int: Optional[int] = None,
                                 value_real: Optional[float] = None) -> None:
        """Schedule an async DB insert from synchronous code.

        Uses utils.async_tasks.schedule_from_sync mit Reference-Tracking gegen
        GC-Verlust (audit 2026-05-17, async.md H2).
        """
        ts = datetime.now().isoformat()
        schedule_from_sync(
            self._db_insert(metric_type, ts, value_int, value_real),
            name=f"stats_tracker.insert[{self.server_type}/{metric_type}]",
        )

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
        """Aufräum-Lauf anstoßen (nur noch Datenbank — es gibt keinen Speicherstand mehr)."""
        schedule_from_sync(
            self._db_cleanup_old(days),
            name=f"stats_tracker.cleanup[{self.server_type}]",
        )

    def _mitzaehlen(self) -> None:
        """Aufräumen anstoßen, wenn genug neue Messwerte dazugekommen sind."""
        self._seit_aufraeumen += 1
        if self._seit_aufraeumen >= AUFRAEUM_INTERVALL:
            self._seit_aufraeumen = 0
            self._cleanup_old()

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_uptime_check(self, is_online: bool) -> None:
        """Record a single uptime check result"""
        self._fire_and_forget_insert("uptime", value_int=1 if is_online else 0)
        self._mitzaehlen()

    def record_player_count(self, count: int) -> None:
        """Record current player count"""
        self._fire_and_forget_insert("player_count", value_int=count)
        self._mitzaehlen()

    def record_savegame_size(self, size_mb: float) -> None:
        """Record savegame/world file size"""
        self._fire_and_forget_insert("savegame_size", value_real=round(size_mb, 2))
        self._mitzaehlen()

    # Alias fuer MC-Nutzung (World-Groesse statt Savegame)
    record_world_size = record_savegame_size

    def record_crash(self, crash_number: int) -> None:
        """Record a crash event"""
        self._fire_and_forget_insert("crash", value_int=crash_number)
        self._mitzaehlen()

    # ------------------------------------------------------------------
    # Queries — rechnen in SQLite, nicht im Prozess
    # ------------------------------------------------------------------

    async def get_uptime_percent(self, days: int = 7) -> float:
        """Calculate uptime percentage over the last N days"""
        klausel, params = self._filter("uptime", days)
        zeile = await self._eine_zeile(
            f"SELECT COUNT(*), SUM(value_int) FROM server_stats_tracker WHERE {klausel}",
            params,
        )
        if not zeile or not zeile[0]:
            return 0.0
        gesamt, online = zeile[0], zeile[1] or 0
        return round((online / gesamt) * 100, 1)

    async def get_peak_players(self, days: int = 7) -> int:
        """Get peak concurrent player count over the last N days"""
        klausel, params = self._filter("player_count", days)
        zeile = await self._eine_zeile(
            f"SELECT MAX(value_int) FROM server_stats_tracker WHERE {klausel}",
            params,
        )
        return int(zeile[0]) if zeile and zeile[0] is not None else 0

    async def get_savegame_growth(self, days: int = 7) -> Optional[Dict[str, Any]]:
        """
        Get savegame size trend over the last N days.
        Returns dict with start_mb, end_mb, growth_mb, growth_percent
        """
        klausel, params = self._filter("savegame_size", days)

        zeile = await self._eine_zeile(
            f"SELECT COUNT(*) FROM server_stats_tracker WHERE {klausel}", params
        )
        if not zeile or zeile[0] < 2:
            return None

        erste = await self._eine_zeile(
            f"SELECT value_real FROM server_stats_tracker WHERE {klausel} "
            "ORDER BY timestamp ASC LIMIT 1",
            params,
        )
        letzte = await self._eine_zeile(
            f"SELECT value_real FROM server_stats_tracker WHERE {klausel} "
            "ORDER BY timestamp DESC LIMIT 1",
            params,
        )
        if not erste or not letzte:
            return None

        start = erste[0] or 0.0
        end = letzte[0] or 0.0
        growth = end - start
        growth_pct = (growth / start * 100) if start > 0 else 0

        return {
            "start_mb": round(start, 1),
            "end_mb": round(end, 1),
            "growth_mb": round(growth, 1),
            "growth_percent": round(growth_pct, 1),
        }

    async def get_crashes(self, days: int = 7) -> List[Dict[str, Any]]:
        """Get crash events over the last N days"""
        klausel, params = self._filter("crash", days)
        try:
            conn = await get_read_db()
            cursor = await conn.execute(
                "SELECT timestamp, value_int FROM server_stats_tracker "
                f"WHERE {klausel} ORDER BY timestamp ASC",
                params,
            )
            zeilen = await cursor.fetchall()
        except Exception as e:
            logger.warning(f"StatsTracker-Abfrage (crashes) fehlgeschlagen: {e}")
            return []
        return [{"ts": z[0], "number": z[1] or 0} for z in zeilen]

    async def get_total_checks(self, days: int = 7) -> int:
        """Total uptime checks in period"""
        klausel, params = self._filter("uptime", days)
        zeile = await self._eine_zeile(
            f"SELECT COUNT(*) FROM server_stats_tracker WHERE {klausel}", params
        )
        return int(zeile[0]) if zeile else 0

    async def check_savegame_trend(self, warn_growth_mb: float = 500,
                                    warn_growth_pct: float = 50,
                                    days: int = 7) -> Optional[Dict[str, Any]]:
        """
        Check if savegame growth exceeds thresholds.
        Returns warning dict if threshold exceeded, None otherwise.
        """
        growth = await self.get_savegame_growth(days)
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
