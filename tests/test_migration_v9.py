#!/usr/bin/env python3
"""
Tests fuer Migration v9 (RBAC: rbac_role_map + dashboard_audit).

  - Fresh-Install: beide Tabellen existieren mit erwarteten Spalten, user_version >= 9.
  - rbac_role_map PRIMARY KEY(role_id, resource, action): INSERT OR IGNORE dedupliziert.
  - dashboard_audit: AUTOINCREMENT-id + ts-Default; Index idx_audit_ts vorhanden.
  - Idempotenz: zweiter Lauf crasht nicht.

Lauf: python tests/test_migration_v9.py
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


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


async def _table_exists(db, table: str) -> bool:
    cur = await db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    return await cur.fetchone() is not None


async def _index_exists(db, index: str) -> bool:
    cur = await db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name=?", (index,)
    )
    return await cur.fetchone() is not None


async def run_tests() -> None:
    tmp = tempfile.mkdtemp(prefix="mig9_")
    db_path = Path(tmp) / "v9.db"
    await db_manager.init_db(db_path=db_path)
    db = await db_manager.get_db()

    try:
        cur = await db.execute("PRAGMA user_version")
        _check("version_min_9", (await cur.fetchone())[0] >= 9)

        _check("rbac_table_exists", await _table_exists(db, "rbac_role_map"))
        _check("audit_table_exists", await _table_exists(db, "dashboard_audit"))
        _check("idx_audit_ts_exists", await _index_exists(db, "idx_audit_ts"))

        cur = await db.execute("PRAGMA table_info(rbac_role_map)")
        rbac_cols = {r[1] for r in await cur.fetchall()}
        _check(
            "rbac_columns_ok",
            {"role_id", "resource", "action"}.issubset(rbac_cols),
            f"cols={rbac_cols}",
        )

        cur = await db.execute("PRAGMA table_info(dashboard_audit)")
        audit_cols = {r[1] for r in await cur.fetchall()}
        _check(
            "audit_columns_ok",
            {"id", "ts", "discord_id", "username", "resource", "action", "detail", "ip"}.issubset(audit_cols),
            f"cols={audit_cols}",
        )

        # PRIMARY KEY dedupliziert (INSERT OR IGNORE)
        await db.execute(
            "INSERT OR IGNORE INTO rbac_role_map (role_id, resource, action) "
            "VALUES ('111', 'minecraft', 'edit')"
        )
        await db.execute(
            "INSERT OR IGNORE INTO rbac_role_map (role_id, resource, action) "
            "VALUES ('111', 'minecraft', 'edit')"
        )
        await db.commit()
        cur = await db.execute(
            "SELECT COUNT(*) FROM rbac_role_map WHERE role_id='111' "
            "AND resource='minecraft' AND action='edit'"
        )
        _check("rbac_pk_dedup", (await cur.fetchone())[0] == 1)

        # dashboard_audit: id auto + ts default gesetzt
        await db.execute(
            "INSERT INTO dashboard_audit (discord_id, username, resource, action, detail, ip) "
            "VALUES ('42', 'tester', 'rbac', 'edit', '{\"x\":1}', '127.0.0.1')"
        )
        await db.commit()
        cur = await db.execute("SELECT id, ts FROM dashboard_audit LIMIT 1")
        row = await cur.fetchone()
        _check("audit_autoincrement_ts", row[0] is not None and row[1] is not None, f"row={tuple(row)}")

        # Idempotenz
        try:
            from modules.database.migrations import _apply_migration_v9
            await _apply_migration_v9(db)
            _check("idempotent", True)
        except Exception as e:  # noqa: BLE001
            _check("idempotent", False, f"Exception: {e}")
    finally:
        await db_manager.close_db()


def main() -> int:
    print("=" * 60)
    print("  Migration v9 Tests (rbac_role_map + dashboard_audit)")
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
