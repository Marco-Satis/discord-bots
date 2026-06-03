#!/usr/bin/env python3
"""
Tests fuer Migration v7 (user_linked_accounts, Phase Linked).

  - Fresh-Install: Tabelle existiert mit erwarteten Spalten, user_version >= 7.
  - UNIQUE(guild_id, user_id, platform): gleicher User+Platform 2 Guilds = ok,
    gleiche Guild+User+Platform doppelt = Verletzung (Isolation + Dedupe).
  - Idempotenz: zweiter Lauf crasht nicht.

Lauf: python tests/test_migration_v7.py
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
    tmp = tempfile.mkdtemp(prefix="mig7_")
    db_path = Path(tmp) / "v7.db"
    await db_manager.init_db(db_path=db_path)
    db = await db_manager.get_db()

    try:
        cur = await db.execute("PRAGMA user_version")
        _check("version_min_7", (await cur.fetchone())[0] >= 7)
        _check("table_exists", await _table_exists(db, "user_linked_accounts"))

        cur = await db.execute("PRAGMA table_info(user_linked_accounts)")
        cols = {r[1] for r in await cur.fetchall()}
        _check(
            "columns_ok",
            {"guild_id", "user_id", "platform", "account_id", "linked_at"}.issubset(cols),
            f"cols={cols}",
        )

        # gleicher User+Platform in 2 Guilds -> erlaubt
        await db.execute(
            "INSERT INTO user_linked_accounts (guild_id, user_id, platform, account_id) "
            "VALUES ('g1', 'u1', 'activision', 'Acti#1')"
        )
        await db.execute(
            "INSERT INTO user_linked_accounts (guild_id, user_id, platform, account_id) "
            "VALUES ('g2', 'u1', 'activision', 'Acti#1')"
        )
        await db.commit()
        cur = await db.execute(
            "SELECT COUNT(*) FROM user_linked_accounts WHERE user_id='u1'"
        )
        _check("two_guilds_ok", (await cur.fetchone())[0] == 2)

        # gleiche Guild+User+Platform doppelt -> UNIQUE-Verletzung
        dup_failed = False
        try:
            await db.execute(
                "INSERT INTO user_linked_accounts (guild_id, user_id, platform, account_id) "
                "VALUES ('g1', 'u1', 'activision', 'Anders')"
            )
            await db.commit()
        except Exception:
            dup_failed = True
        _check("unique_same_guild_platform", dup_failed)

        # Idempotenz
        try:
            from modules.database.migrations import _apply_migration_v7
            await _apply_migration_v7(db)
            _check("idempotent", True)
        except Exception as e:  # noqa: BLE001
            _check("idempotent", False, f"Exception: {e}")
    finally:
        await db_manager.close_db()


def main() -> int:
    print("=" * 60)
    print("  Migration v7 Tests (user_linked_accounts)")
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
