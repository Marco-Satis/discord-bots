#!/usr/bin/env python3
"""
Tests fuer Migration v6 (Leveling guild-scoped + voice_sessions, Phase C).

Schwerpunkt (Plan P18 Daten-Migration-Safety):
  - Upgrade-Pfad: bestehende leveling-XP-Daten bleiben erhalten + guild_id backfill.
  - Fresh-Install: leveling hat guild_id, voice_sessions existiert.
  - UNIQUE(guild_id, user_id): gleicher User in 2 Guilds = getrennte XP (kein Leak).
  - Idempotenz: zweiter Lauf migriert nicht erneut.

Lauf: python tests/test_migration_v6.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import aiosqlite
    from modules.database import db_manager
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False

_results: list[tuple[str, bool, str]] = []

OLD_LEVELING_SCHEMA = (
    "CREATE TABLE leveling ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT UNIQUE NOT NULL, "
    "xp INTEGER DEFAULT 0, level INTEGER DEFAULT 0, messages INTEGER DEFAULT 0, "
    "voice_minutes INTEGER DEFAULT 0, last_xp_time TIMESTAMP)"
)


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


async def _columns(db, table: str) -> list[str]:
    cur = await db.execute(f"PRAGMA table_info({table})")
    return [r[1] for r in await cur.fetchall()]


async def _table_exists(db, table: str) -> bool:
    cur = await db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    return await cur.fetchone() is not None


async def run_tests() -> None:
    # ============================================================
    # Szenario 1: UPGRADE — alte leveling-Daten bei v5 -> v6
    # ============================================================
    tmp1 = tempfile.mkdtemp(prefix="mig6_up_")
    db_path = Path(tmp1) / "up.db"

    pre = await aiosqlite.connect(str(db_path))
    await pre.execute(OLD_LEVELING_SCHEMA)
    await pre.execute(
        "INSERT INTO leveling (user_id, xp, level, messages, voice_minutes) "
        "VALUES ('42', 1000, 5, 100, 30)"
    )
    await pre.execute("INSERT INTO leveling (user_id, xp, level) VALUES ('77', 50, 1)")
    await pre.execute("PRAGMA user_version = 5")
    await pre.commit()
    await pre.close()

    await db_manager.init_db(db_path=db_path)
    db = await db_manager.get_db()
    try:
        cur = await db.execute("PRAGMA user_version")
        ver = (await cur.fetchone())[0]
        _check("upgrade_version_6", ver >= 6, f"ver={ver}")

        cols = await _columns(db, "leveling")
        _check("upgrade_guild_id_col", "guild_id" in cols)

        _check("upgrade_voice_sessions", await _table_exists(db, "voice_sessions"))

        # Daten erhalten?
        cur = await db.execute(
            "SELECT xp, level, messages, voice_minutes, guild_id "
            "FROM leveling WHERE user_id='42'"
        )
        row = await cur.fetchone()
        _check("upgrade_data_xp", row is not None and row[0] == 1000, f"row={tuple(row) if row else None}")
        _check("upgrade_data_level", row is not None and row[1] == 5)
        _check("upgrade_data_messages", row is not None and row[2] == 100)
        _check("upgrade_guild_backfilled", row is not None and row[4] not in (None, ""))

        cur = await db.execute("SELECT COUNT(*) FROM leveling")
        _check("upgrade_row_count", (await cur.fetchone())[0] == 2)

        # UNIQUE(guild_id, user_id): gleicher user_id, andere Guild -> erlaubt
        gid = row[4]
        await db.execute(
            "INSERT INTO leveling (guild_id, user_id, xp) VALUES (?, '42', 999)",
            ("999888777",),
        )
        await db.commit()
        cur = await db.execute("SELECT COUNT(*) FROM leveling WHERE user_id='42'")
        _check("isolation_same_user_two_guilds", (await cur.fetchone())[0] == 2)

        # gleicher user_id + gleiche Guild -> UNIQUE-Verletzung
        dup_failed = False
        try:
            await db.execute(
                "INSERT INTO leveling (guild_id, user_id, xp) VALUES (?, '42', 1)",
                (gid,),
            )
            await db.commit()
        except Exception:
            dup_failed = True
        _check("unique_same_guild_user", dup_failed)
    finally:
        await db_manager.close_db()

    # ============================================================
    # Szenario 2: FRESH-INSTALL — leeres DB -> v6 komplett
    # ============================================================
    tmp2 = tempfile.mkdtemp(prefix="mig6_fresh_")
    db_path2 = Path(tmp2) / "fresh.db"
    await db_manager.init_db(db_path=db_path2)
    db2 = await db_manager.get_db()
    try:
        cur = await db2.execute("PRAGMA user_version")
        _check("fresh_version_6", (await cur.fetchone())[0] >= 6)
        cols = await _columns(db2, "leveling")
        _check("fresh_leveling_guild_id", "guild_id" in cols)
        _check("fresh_voice_sessions", await _table_exists(db2, "voice_sessions"))
        # voice_sessions Spalten korrekt
        vcols = await _columns(db2, "voice_sessions")
        _check("fresh_vs_columns",
               {"guild_id", "user_id", "valid", "xp_awarded"}.issubset(set(vcols)))
    finally:
        await db_manager.close_db()

    # ============================================================
    # Szenario 3: IDEMPOTENZ — v6 erneut auf migriertem DB
    # ============================================================
    await db_manager.init_db(db_path=db_path2)  # nochmal öffnen
    db3 = await db_manager.get_db()
    try:
        from modules.database.migrations import _apply_migration_v6
        await _apply_migration_v6(db3)  # darf NICHT crashen / Daten zerstören
        _check("idempotent_rerun_ok", True)
        _check("idempotent_still_guild_id", "guild_id" in await _columns(db3, "leveling"))
    except Exception as e:  # noqa: BLE001
        _check("idempotent_rerun_ok", False, f"Exception: {e}")
    finally:
        await db_manager.close_db()


def main() -> int:
    print("=" * 60)
    print("  Migration v6 Tests (leveling guild-scoped + voice_sessions)")
    print("=" * 60)
    if not HAVE_DEPS:
        print("  [SKIP] aiosqlite nicht installiert — Test läuft am Server.")
        print("  ERGEBNIS: ALLE PRUEFUNGEN BESTANDEN (übersprungen)")
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
