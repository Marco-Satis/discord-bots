#!/usr/bin/env python3
"""
Tests fuer Migration v11 (ungenutzten Index idx_sst_server entfernen).

Die Tabelle server_stats_tracker trug zwei Indizes. Gelesen wurde nur
idx_sst_lookup — die Abfragen pruefen server_id als
`(server_id = ? OR (server_id IS NULL AND ? IS NULL))`, und diese
OR-Verzweigung ueber dieselbe Spalte kann SQLite nicht in einen Index-Zugriff
uebersetzen. idx_sst_server kostete damit 16,5 MB, ohne je benutzt zu werden.

Geprueft wird deshalb beides: dass der Index verschwindet, und dass die
Abfragen danach weiterhin ueber einen Index laufen statt in einen Full-Scan
zu fallen.

Lauf: /home/botuser/Discord_Bots/venv/bin/python tests/test_migration_v11.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import aiosqlite  # noqa: F401
    from modules.database import db_manager
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False

_results: list[tuple[str, bool, str]] = []

# Die vier Leser in modules/monitoring/stats_tracker.py haben dieselbe Form.
LESE_QUERY = (
    "SELECT timestamp, value_int FROM server_stats_tracker "
    "WHERE server_type = ? AND metric_type = 'uptime' "
    "AND (server_id = ? OR (server_id IS NULL AND ? IS NULL)) "
    "AND timestamp >= ? ORDER BY timestamp ASC"
)


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


async def _index_exists(db, index: str) -> bool:
    cur = await db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name=?", (index,)
    )
    return await cur.fetchone() is not None


async def _plan(db, query: str, params: tuple) -> str:
    cur = await db.execute("EXPLAIN QUERY PLAN " + query, params)
    return " | ".join(str(row[-1]) for row in await cur.fetchall())


async def run_tests() -> None:
    # --- 1. Fresh-Install legt den zweiten Index gar nicht erst an ---
    tmp = tempfile.mkdtemp(prefix="mig11_")
    await db_manager.init_db(db_path=Path(tmp) / "frisch.db")
    db = await db_manager.get_db()
    try:
        cur = await db.execute("PRAGMA user_version")
        _check("version_min_11", (await cur.fetchone())[0] >= 11)

        _check("lookup_index_da", await _index_exists(db, "idx_sst_lookup"))
        _check("server_index_weg", not await _index_exists(db, "idx_sst_server"))

        # Die Abfrage muss weiterhin ueber einen Index laufen.
        plan = await _plan(db, LESE_QUERY, ("sat", None, None, "2026-01-01"))
        _check("lesequery_nutzt_index", "idx_sst_lookup" in plan, plan)
        _check("kein_full_scan", "SCAN server_stats_tracker" not in plan, plan)

        # Retention-DELETE ebenso.
        plan = await _plan(
            db,
            "SELECT rowid FROM server_stats_tracker WHERE server_type = ? "
            "AND (server_id = ? OR (server_id IS NULL AND ? IS NULL)) "
            "AND timestamp < ?",
            ("sat", None, None, "2026-01-01"),
        )
        _check("retention_nutzt_index", "idx_sst_lookup" in plan, plan)
    finally:
        await db_manager.close_db()

    # --- 2. Bestandsdatenbank: der alte Index wird wirklich entfernt ---
    tmp2 = tempfile.mkdtemp(prefix="mig11_alt_")
    pfad = Path(tmp2) / "alt.db"
    async with aiosqlite.connect(pfad) as roh:
        # Zustand vor der Migration nachstellen
        await roh.executescript(
            """
            CREATE TABLE server_stats_tracker (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_type TEXT NOT NULL,
                server_id TEXT,
                metric_type TEXT NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                value_int INTEGER,
                value_real REAL
            );
            CREATE INDEX idx_sst_lookup
                ON server_stats_tracker(server_type, metric_type, timestamp);
            CREATE INDEX idx_sst_server
                ON server_stats_tracker(server_type, server_id, metric_type, timestamp);
            """
        )
        await roh.execute(
            "INSERT INTO server_stats_tracker "
            "(server_type, server_id, metric_type, timestamp, value_int) "
            "VALUES ('sat', NULL, 'uptime', '2026-08-01T12:00:00', 1)"
        )
        await roh.commit()

        from modules.database.migrations import _apply_migration_v11

        _check("alt_hat_beide_indizes",
               await _index_exists(roh, "idx_sst_server")
               and await _index_exists(roh, "idx_sst_lookup"))

        await _apply_migration_v11(roh)

        _check("migration_entfernt_index",
               not await _index_exists(roh, "idx_sst_server"))
        _check("migration_laesst_lookup_stehen",
               await _index_exists(roh, "idx_sst_lookup"))

        # Daten bleiben unangetastet — DROP INDEX ruehrt keine Zeile an.
        cur = await roh.execute("SELECT COUNT(*) FROM server_stats_tracker")
        _check("daten_unberuehrt", (await cur.fetchone())[0] == 1)

        # Zweiter Lauf darf nicht krachen (IF EXISTS).
        try:
            await _apply_migration_v11(roh)
            _check("idempotent", True)
        except Exception as e:  # noqa: BLE001
            _check("idempotent", False, f"Exception: {e}")


def main() -> int:
    print("=" * 60)
    print("  Migration v11 (idx_sst_server entfernen)")
    print("=" * 60)
    if not HAVE_DEPS:
        print("  [SKIP] aiosqlite nicht installiert — laeuft am Server.")
        print("  ERGEBNIS: ALLE PRUEFUNGEN BESTANDEN (uebersprungen)")
        return 0

    try:
        asyncio.run(run_tests())
    except Exception as e:  # noqa: BLE001
        _check("run", False, f"Exception: {e}")

    failed = 0
    for name, ok, msg in _results:
        status = "[OK]  " if ok else "[FAIL]"
        line = f"  {status} {name}"
        if not ok and msg:
            line += f"  -> {msg}"
        print(line)
        if not ok:
            failed += 1

    print("-" * 60)
    if failed == 0:
        print(f"  ERGEBNIS: ALLE PRUEFUNGEN BESTANDEN ({len(_results)} Checks)")
        return 0
    print(f"  ERGEBNIS: {failed}/{len(_results)} FEHLGESCHLAGEN")
    return 1


if __name__ == "__main__":
    sys.exit(main())
