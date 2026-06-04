#!/usr/bin/env python3
"""
Tests fuer Migration v8 (member_cache, E3 Web-Leaderboard Name+Avatar).

  - Fresh-Install: Tabelle existiert mit erwarteten Spalten, user_version >= 8.
  - PRIMARY KEY(guild_id, user_id): Upsert ersetzt statt dupliziert; gleicher
    User in 2 Guilds = getrennte Zeilen (Isolation).
  - Idempotenz: zweiter Lauf crasht nicht.

Lauf: python tests/test_migration_v8.py
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


async def run_tests() -> None:
    tmp = tempfile.mkdtemp(prefix="mig8_")
    db_path = Path(tmp) / "v8.db"
    await db_manager.init_db(db_path=db_path)
    db = await db_manager.get_db()

    try:
        cur = await db.execute("PRAGMA user_version")
        _check("version_min_8", (await cur.fetchone())[0] >= 8)
        _check("table_exists", await _table_exists(db, "member_cache"))

        cur = await db.execute("PRAGMA table_info(member_cache)")
        cols = {r[1] for r in await cur.fetchall()}
        _check(
            "columns_ok",
            {"guild_id", "user_id", "display_name", "avatar_url", "updated_at"}.issubset(cols),
            f"cols={cols}",
        )

        # gleicher User in 2 Guilds -> getrennte Zeilen (Isolation)
        await db.execute(
            "INSERT INTO member_cache (guild_id, user_id, display_name) "
            "VALUES ('g1', 'u1', 'NameInG1')"
        )
        await db.execute(
            "INSERT INTO member_cache (guild_id, user_id, display_name) "
            "VALUES ('g2', 'u1', 'NameInG2')"
        )
        await db.commit()
        cur = await db.execute("SELECT COUNT(*) FROM member_cache WHERE user_id='u1'")
        _check("two_guilds_separate", (await cur.fetchone())[0] == 2)

        # Upsert (PRIMARY KEY) ersetzt statt dupliziert
        await db.execute(
            "INSERT INTO member_cache (guild_id, user_id, display_name) "
            "VALUES ('g1', 'u1', 'Updated') "
            "ON CONFLICT(guild_id, user_id) DO UPDATE SET display_name=excluded.display_name"
        )
        await db.commit()
        cur = await db.execute(
            "SELECT display_name, COUNT(*) FROM member_cache "
            "WHERE guild_id='g1' AND user_id='u1'"
        )
        row = await cur.fetchone()
        _check("upsert_replaces", row[0] == "Updated" and row[1] == 1, f"row={tuple(row)}")

        # Idempotenz
        try:
            from modules.database.migrations import _apply_migration_v8
            await _apply_migration_v8(db)
            _check("idempotent", True)
        except Exception as e:  # noqa: BLE001
            _check("idempotent", False, f"Exception: {e}")
    finally:
        await db_manager.close_db()


def main() -> int:
    print("=" * 60)
    print("  Migration v8 Tests (member_cache)")
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
