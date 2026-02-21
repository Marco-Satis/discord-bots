"""
Database Maintenance — SQLite Backup + Data Retention

F63: Dedizierte SQLite-Backup-Rotation fuer botdata.db
     - Backup via SQLite backup API (sicher im WAL-Modus)
     - Rotation: max 24 hourly + 7 daily + 4 weekly
     - Integritaetscheck nach jedem Backup
     - Logging von Groesse und Status

F56: Automatische Daten-Retention mit konfigurierbaren Fristen
     - stats_history: 90 Tage
     - events: 30 Tage
     - audit_log: 365 Tage
     - command_log: 90 Tage
     - alerts_sent (resolved): 30 Tage
     - player_sessions: unbegrenzt (kein Cleanup)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import aiosqlite

from modules.database.db_manager import get_db, get_db_path
from utils.config import DATA_DIR
from utils.logger import get_logger

logger = get_logger("database.maintenance")

# ---------------------------------------------------------------------------
# Backup-Verzeichnis
# ---------------------------------------------------------------------------
DB_BACKUP_DIR = DATA_DIR / "backups" / "db"

# ---------------------------------------------------------------------------
# Standard-Retention (Tage) — Tabelle -> (Spaltenname, Tage)
# None = unbegrenzt (kein Cleanup)
# ---------------------------------------------------------------------------
DEFAULT_RETENTION: dict[str, tuple[str, int]] = {
    "stats_history": ("timestamp", 90),
    "events":        ("timestamp", 30),
    "audit_log":     ("timestamp", 365),
    "command_log":   ("timestamp", 90),
}

# Sonderfall: alerts_sent wird nur geloescht wenn resolved=TRUE
ALERTS_RETENTION_DAYS = 30


class DatabaseMaintenance:
    """SQLite Backup + Data Retention Manager."""

    def __init__(
        self,
        backup_dir: Optional[Path] = None,
        retention: Optional[dict[str, tuple[str, int]]] = None,
        alerts_retention_days: int = ALERTS_RETENTION_DAYS,
    ) -> None:
        self.backup_dir = backup_dir or DB_BACKUP_DIR
        self.retention = retention or DEFAULT_RETENTION
        self.alerts_retention_days = alerts_retention_days

    # ------------------------------------------------------------------
    # F63: SQLite Backup
    # ------------------------------------------------------------------

    async def create_backup(self) -> dict:
        """Creates a backup of botdata.db using the SQLite backup API.

        The SQLite backup API is safe to use while the database is in
        WAL mode and being accessed concurrently.

        Returns:
            dict with keys: success, path, size_mb, integrity_ok, error
        """
        result: dict = {
            "success": False,
            "path": None,
            "size_mb": 0.0,
            "integrity_ok": False,
            "error": None,
        }

        try:
            # 1. Sicherstellen dass Backup-Verzeichnis existiert
            self.backup_dir.mkdir(parents=True, exist_ok=True)

            # 2. Dateiname mit Zeitstempel generieren
            now = datetime.now(timezone.utc)
            filename = f"botdata_{now.strftime('%Y%m%d_%H%M%S')}.db"
            backup_path = self.backup_dir / filename
            result["path"] = str(backup_path)

            # 3. Quell-Datenbank holen
            source_db = await get_db()

            # 4. Backup via SQLite backup API
            #    aiosqlite wraps sqlite3.Connection -> Zugriff via ._conn
            dest_db: Optional[aiosqlite.Connection] = None
            try:
                dest_db = await aiosqlite.connect(str(backup_path))
                # backup() ist synchron auf dem sqlite3.Connection-Objekt
                source_db._conn.backup(dest_db._conn)
                logger.info(f"Backup erstellt: {backup_path.name}")
            finally:
                if dest_db is not None:
                    await dest_db.close()

            # 5. Integritaetscheck auf dem Backup
            integrity_ok = await self._check_backup_integrity(backup_path)
            result["integrity_ok"] = integrity_ok

            # 6. Dateigroesse ermitteln
            size_bytes = backup_path.stat().st_size
            size_mb = round(size_bytes / (1024 * 1024), 2)
            result["size_mb"] = size_mb

            result["success"] = True
            logger.info(
                f"Backup abgeschlossen: {filename} "
                f"({size_mb} MB, Integritaet: {'OK' if integrity_ok else 'FEHLER'})"
            )

        except Exception as exc:
            result["error"] = str(exc)
            logger.error(f"Backup fehlgeschlagen: {exc}")

        return result

    async def _check_backup_integrity(self, backup_path: Path) -> bool:
        """Run PRAGMA integrity_check on a backup file.

        Opens an independent connection to the backup, runs the check,
        and closes the connection again.
        """
        db: Optional[aiosqlite.Connection] = None
        try:
            db = await aiosqlite.connect(str(backup_path))
            cursor = await db.execute("PRAGMA integrity_check")
            row = await cursor.fetchone()
            ok = row is not None and row[0] == "ok"
            if not ok:
                logger.warning(
                    f"Integritaetscheck fehlgeschlagen fuer {backup_path.name}: {row}"
                )
            return ok
        except Exception as exc:
            logger.error(
                f"Integritaetscheck-Fehler fuer {backup_path.name}: {exc}"
            )
            return False
        finally:
            if db is not None:
                await db.close()

    # ------------------------------------------------------------------
    # F63: Backup-Rotation
    # ------------------------------------------------------------------

    async def rotate_backups(
        self,
        max_hourly: int = 24,
        max_daily: int = 7,
        max_weekly: int = 4,
    ) -> int:
        """Rotate old backups and return the number of deleted files.

        Strategy (simplified, deterministic):
        - Alle Backup-Dateien nach Alter sortieren (neueste zuerst).
        - Jedes Backup wird einer Kategorie zugeordnet:
            * hourly  — juenger als 24 h
            * daily   — juenger als 7 Tage (aelteste pro Tag behalten)
            * weekly  — aelter als 7 Tage  (aelteste pro Woche behalten)
        - Ueberzaehlige Backups jeder Kategorie werden geloescht.
        """
        if not self.backup_dir.exists():
            return 0

        # Alle botdata_*.db Dateien sammeln
        backup_files = sorted(
            self.backup_dir.glob("botdata_*.db"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,  # neueste zuerst
        )

        if not backup_files:
            return 0

        now = datetime.now(timezone.utc)
        hourly_cutoff = now - timedelta(hours=24)
        daily_cutoff = now - timedelta(days=7)

        hourly: list[Path] = []
        daily_buckets: dict[str, list[Path]] = {}   # "YYYY-MM-DD" -> files
        weekly_buckets: dict[str, list[Path]] = {}   # "YYYY-WW"   -> files

        for fp in backup_files:
            try:
                mtime = datetime.fromtimestamp(fp.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue

            if mtime >= hourly_cutoff:
                hourly.append(fp)
            elif mtime >= daily_cutoff:
                day_key = mtime.strftime("%Y-%m-%d")
                daily_buckets.setdefault(day_key, []).append(fp)
            else:
                week_key = mtime.strftime("%Y-W%W")
                weekly_buckets.setdefault(week_key, []).append(fp)

        to_keep: set[Path] = set()

        # Hourly: behalte die neuesten max_hourly
        for fp in hourly[:max_hourly]:
            to_keep.add(fp)

        # Daily: pro Tag das aelteste Backup behalten (bis max_daily Tage)
        daily_keys_sorted = sorted(daily_buckets.keys(), reverse=True)
        for day_key in daily_keys_sorted[:max_daily]:
            # Aeltestes Backup des Tages behalten (letztes in der Liste,
            # da Liste absteigend nach mtime sortiert ist)
            oldest = daily_buckets[day_key][-1]
            to_keep.add(oldest)

        # Weekly: pro Woche das aelteste Backup behalten (bis max_weekly Wochen)
        weekly_keys_sorted = sorted(weekly_buckets.keys(), reverse=True)
        for week_key in weekly_keys_sorted[:max_weekly]:
            oldest = weekly_buckets[week_key][-1]
            to_keep.add(oldest)

        # Alles loeschen was nicht in to_keep ist
        deleted = 0
        for fp in backup_files:
            if fp not in to_keep:
                try:
                    fp.unlink()
                    deleted += 1
                    logger.debug(f"Backup geloescht: {fp.name}")
                except OSError as exc:
                    logger.warning(f"Konnte Backup nicht loeschen {fp.name}: {exc}")

        if deleted > 0:
            logger.info(
                f"Backup-Rotation: {deleted} Dateien geloescht, "
                f"{len(to_keep)} behalten"
            )

        return deleted

    # ------------------------------------------------------------------
    # F56: Data Retention
    # ------------------------------------------------------------------

    async def run_retention(self) -> dict[str, int]:
        """Run data retention cleanup on all configured tables.

        Deletes rows older than the configured number of days for each
        table. Also cleans up resolved alerts older than
        ``alerts_retention_days``.

        Returns:
            dict mapping table_name -> number of deleted rows
        """
        results: dict[str, int] = {}
        db = await get_db()
        now = datetime.now(timezone.utc)

        # 1. Standard-Retention fuer konfigurierte Tabellen
        for table, (column, days) in self.retention.items():
            try:
                cutoff = (now - timedelta(days=days)).isoformat()
                cursor = await db.execute(
                    f"DELETE FROM [{table}] WHERE [{column}] < ?",
                    (cutoff,),
                )
                deleted = cursor.rowcount
                results[table] = deleted
                if deleted > 0:
                    logger.info(
                        f"Retention [{table}]: {deleted} Zeilen geloescht "
                        f"(aelter als {days} Tage)"
                    )
            except Exception as exc:
                results[table] = -1
                logger.error(f"Retention-Fehler fuer [{table}]: {exc}")

        # 2. Sonderfall: alerts_sent — nur aufgeloeste Alerts loeschen
        try:
            cutoff_alerts = (
                now - timedelta(days=self.alerts_retention_days)
            ).isoformat()
            cursor = await db.execute(
                "DELETE FROM alerts_sent "
                "WHERE resolved = 1 AND resolved_at < ?",
                (cutoff_alerts,),
            )
            deleted = cursor.rowcount
            results["alerts_sent (resolved)"] = deleted
            if deleted > 0:
                logger.info(
                    f"Retention [alerts_sent]: {deleted} aufgeloeste Alerts "
                    f"geloescht (aelter als {self.alerts_retention_days} Tage)"
                )
        except Exception as exc:
            results["alerts_sent (resolved)"] = -1
            logger.error(f"Retention-Fehler fuer [alerts_sent]: {exc}")

        # Aenderungen committen
        try:
            await db.commit()
        except Exception as exc:
            logger.error(f"Retention-Commit fehlgeschlagen: {exc}")

        total = sum(v for v in results.values() if v > 0)
        logger.info(
            f"Retention abgeschlossen: {total} Zeilen in "
            f"{len(results)} Tabellen bereinigt"
        )

        return results

    # ------------------------------------------------------------------
    # Statistiken
    # ------------------------------------------------------------------

    async def get_db_stats(self) -> dict:
        """Get database statistics for monitoring/dashboard.

        Returns:
            dict with: db_size_mb, table_counts, last_backup, backup_count
        """
        stats: dict = {
            "db_size_mb": 0.0,
            "table_counts": {},
            "last_backup": None,
            "backup_count": 0,
        }

        # DB-Dateigroesse
        try:
            db_path = get_db_path()
            if db_path.exists():
                stats["db_size_mb"] = round(
                    db_path.stat().st_size / (1024 * 1024), 2
                )
        except OSError:
            pass

        # Zeilenanzahl pro Tabelle
        try:
            db = await get_db()
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'search_%'"
            )
            tables = await cursor.fetchall()
            for table in tables:
                name = table[0]
                try:
                    c = await db.execute(f"SELECT COUNT(*) FROM [{name}]")
                    row = await c.fetchone()
                    stats["table_counts"][name] = row[0] if row else 0
                except Exception:
                    stats["table_counts"][name] = -1
        except Exception as exc:
            logger.error(f"Fehler beim Lesen der Tabellenzaehler: {exc}")

        # Backup-Informationen
        try:
            if self.backup_dir.exists():
                backups = sorted(
                    self.backup_dir.glob("botdata_*.db"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                stats["backup_count"] = len(backups)
                if backups:
                    latest = backups[0]
                    mtime = datetime.fromtimestamp(
                        latest.stat().st_mtime, tz=timezone.utc
                    )
                    stats["last_backup"] = {
                        "filename": latest.name,
                        "size_mb": round(
                            latest.stat().st_size / (1024 * 1024), 2
                        ),
                        "created_at": mtime.isoformat(),
                    }
        except OSError as exc:
            logger.warning(f"Fehler beim Lesen der Backup-Infos: {exc}")

        return stats

    # ------------------------------------------------------------------
    # Kombinierte Wartung
    # ------------------------------------------------------------------

    async def full_maintenance(self) -> dict:
        """Run complete maintenance cycle: backup + rotation + retention.

        Returns:
            Combined result dict with keys: backup, rotated_files, retention
        """
        results: dict = {}

        # 1. Backup erstellen
        logger.info("Starte vollstaendige Datenbank-Wartung ...")
        backup_result = await self.create_backup()
        results["backup"] = backup_result

        # 2. Alte Backups rotieren
        deleted = await self.rotate_backups()
        results["rotated_files"] = deleted

        # 3. Retention ausfuehren
        retention_results = await self.run_retention()
        results["retention"] = retention_results

        # 4. Zusammenfassung loggen
        bk_status = "OK" if backup_result.get("success") else "FEHLER"
        total_retained = sum(
            v for v in retention_results.values() if v > 0
        )
        logger.info(
            f"Datenbank-Wartung abgeschlossen: "
            f"Backup={bk_status}, "
            f"Rotiert={deleted} Dateien, "
            f"Retention={total_retained} Zeilen entfernt"
        )

        return results
